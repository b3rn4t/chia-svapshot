"""SVApshot's pipeline, expressed as CHIA nodes.

Each ``@ChiaFunction`` below is one schedulable unit of the loop's task graph.
The split follows what each stage actually *needs*, which is the whole point of
giving them separate resource labels:

===================  ==================  ===============================================
node                 resource            why it is scarce
===================  ==================  ===============================================
``prepare_run_root``  ``svapshot_cpu``   nothing scarce; plain filesystem work
``svapshot_scaffold`` ``svapshot_cpu``   harness scaffolding, CPU-bound
``svapshot_seed``     ``llm``            burns LLM credits / rate limit
``svapshot_prove``    ``formal``         holds a VC Formal or JasperGold licence
``collect_snapshot``  *(none)*           pure parsing, runs anywhere
``score_snapshot``    *(none)*           pure decision function
===================  ==================  ===============================================

Ordering between stages is expressed as *data* dependencies: a node takes the
previous stage's :class:`StageResult` as an argument, so Ray resolves the
``ObjectRef`` before the node starts. There is no separate "depends on" API —
passing the ref is the edge.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from chia.base.ChiaFunction import ChiaFunction

from ._stage_driver import BEGIN, END
from .state import (
    DutSpec,
    PropertyRecord,
    Snapshot,
    StageResult,
    SvapshotConfig,
    Verdict,
    unwrap,
)
from .workspace import build_run_root, stage_env

_DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_stage_driver.py")

_PROPERTY_RE = re.compile(
    r"^\s*(?P<name>\w+)\s*:\s*(?P<kind>assert|assume|cover)\s+property", re.MULTILINE
)
_LABELLED_RE = re.compile(
    r"^\s*(?P<kind>assert|assume|cover)\s+property.*?//\s*(?P<name>\w+)", re.MULTILINE
)


def _tail(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else "...\n" + text[-limit:]


def _extract_payload(stdout: str) -> Dict[str, Any]:
    """Pull the driver's JSON report out of a noisy SVApshot stdout stream."""
    if BEGIN not in stdout or END not in stdout:
        return {}
    blob = stdout.split(BEGIN, 1)[1].split(END, 1)[0].strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return {}


def _run_stage(
    stage: str,
    dut: DutSpec,
    cfg: SvapshotConfig,
    run_root: str,
    extra: Optional[Dict[str, Any]] = None,
) -> StageResult:
    args: Dict[str, Any] = {
        "checkout": cfg.svapshot_root,
        "rtl_module": dut.rtl_relpath,
        "sources": dut.sources,
        "includes": dut.includes,
        "module_type": dut.module_type,
        "llm_model": cfg.llm_model,
        "execution": cfg.execution,
        "verbosity": cfg.verbosity,
        "formal_tool": cfg.formal_tool,
        "assertion_source": cfg.assertion_source,
        "initial_file": "initial",
        "module": dut.module,
    }
    args.update(extra or {})

    env = stage_env(cfg.svapshot_root, run_root, dut.sources[0])
    started = time.time()
    try:
        proc = subprocess.run(
            [cfg.interpreter, _DRIVER, stage, json.dumps(args)],
            cwd=run_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=cfg.timeout_s,
        )
        stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = f"timed out after {cfg.timeout_s}s"
        rc = 124

    payload = _extract_payload(stdout)
    sva_dir = os.path.join(run_root, f"ft_{dut.module}", "sva")
    artifacts = sorted(os.listdir(sva_dir)) if os.path.isdir(sva_dir) else []

    return StageResult(
        stage=stage,
        module=dut.module,
        ok=bool(payload.get("ok")),
        returncode=rc,
        elapsed_s=round(time.time() - started, 2),
        workspace=run_root,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr, 2000),
        artifacts=artifacts,
        detail=payload,
    )


@ChiaFunction(resources={"svapshot_cpu": 1})
def prepare_run_root(dut: DutSpec, cfg: SvapshotConfig) -> str:
    """Materialise the isolated SVApshot run root for *dut* and return its path."""
    dut = unwrap(dut)
    run_root = dut.workspace
    build_run_root(cfg.svapshot_root, run_root)
    return run_root


@ChiaFunction(resources={"svapshot_cpu": 1})
def svapshot_scaffold(dut: DutSpec, cfg: SvapshotConfig, run_root: str) -> StageResult:
    """Run the scaffolder and the file-list fixups that make the DUT formal-ready."""
    return _run_stage("scaffold", unwrap(dut), cfg, unwrap(run_root))


_PKG_SECTION = "// Automatically detected packages (dependency order)"


@ChiaFunction(resources={"svapshot_cpu": 1})
def patch_package_closure(
    dut: DutSpec, run_root: str, scaffold: StageResult
) -> StageResult:
    """Add packages that SVApshot's detector missed to ``manual_sub.vc``.

    SVApshot scans the DUT's own ``import <pkg>::*`` lines. That finds packages
    the module names directly, but not the ones those packages import in turn:
    ``div_4bits`` imports ``drac_pkg``, and ``drac_pkg`` imports ``riscv_pkg``,
    which never reaches the file list. The staging node already resolved the
    full closure in dependency order, so this node reconciles the two rather
    than re-deriving anything.
    """
    dut, run_root, scaffold = unwrap(dut), unwrap(run_root), unwrap(scaffold)
    path = os.path.join(run_root, f"ft_{dut.module}", "manual_sub.vc")
    if not scaffold.ok or not os.path.isfile(path):
        return StageResult(
            stage="patch",
            module=dut.module,
            ok=scaffold.ok,
            returncode=0 if scaffold.ok else -1,
            elapsed_s=0.0,
            workspace=run_root,
            stderr_tail="skipped: no manual_sub.vc to patch",
        )

    with open(path, "r", errors="replace") as handle:
        lines = handle.read().splitlines()

    wanted = [f"./{dut.includes}/{name}" for name in dut.packages]
    present = {line.strip() for line in lines}
    missing = [entry for entry in wanted if entry not in present]

    if missing:
        # Rewrite the whole detected-packages block so ordering is right:
        # a package must be compiled after everything it imports.
        keep = [line for line in lines if line.strip() not in wanted]
        try:
            anchor = keep.index(_PKG_SECTION) + 1
        except ValueError:
            keep.append(_PKG_SECTION)
            anchor = len(keep)
        patched = keep[:anchor] + wanted + keep[anchor:]
        with open(path, "w") as handle:
            handle.write("\n".join(patched) + "\n")

    return StageResult(
        stage="patch",
        module=dut.module,
        ok=True,
        returncode=0,
        elapsed_s=0.0,
        workspace=run_root,
        stdout_tail=(
            f"added {missing} to manual_sub.vc"
            if missing
            else "package closure already complete"
        ),
        detail={"closure": wanted, "added": missing},
    )


@ChiaFunction(resources={"llm": 1})
def svapshot_seed(
    dut: DutSpec,
    cfg: SvapshotConfig,
    run_root: str,
    scaffold: StageResult,
    seed_text: str = "",
) -> StageResult:
    """Generate the initial assertion set.

    Args:
        scaffold: Result of the scaffolding stage. Taken as an argument purely
            to make the ordering edge explicit to Ray; the value is checked so a
            failed scaffold short-circuits instead of producing a confusing
            second failure.
        seed_text: Assertions to seed with when ``cfg.assertion_source`` is
            ``empty`` — this is how the agentic edge feeds properties back in.
    """
    dut, run_root, scaffold = unwrap(dut), unwrap(run_root), unwrap(scaffold)
    if not scaffold.ok:
        return StageResult(
            stage="seed",
            module=dut.module,
            ok=False,
            returncode=-1,
            elapsed_s=0.0,
            workspace=run_root,
            stderr_tail="skipped: scaffold stage failed",
        )
    return _run_stage("seed", dut, cfg, run_root, {"seed_text": seed_text})


@ChiaFunction(resources={"formal": 1})
def svapshot_prove(
    dut: DutSpec, cfg: SvapshotConfig, run_root: str, seed: StageResult
) -> StageResult:
    """Run the SVApshot agent: repair, extend and formally prove the properties.

    This is the node that holds a formal licence, so it carries the ``formal``
    resource and is the one worth caching or bypassing on reruns.
    """
    dut, run_root, seed = unwrap(dut), unwrap(run_root), unwrap(seed)
    if not seed.ok:
        return StageResult(
            stage="prove",
            module=dut.module,
            ok=False,
            returncode=-1,
            elapsed_s=0.0,
            workspace=run_root,
            stderr_tail="skipped: seed stage failed",
        )
    return _run_stage("prove", dut, cfg, run_root)


@ChiaFunction(resources={"formal": 1})
def svapshot_formal_check(
    dut: DutSpec, cfg: SvapshotConfig, run_root: str, upstream: StageResult
) -> StageResult:
    """Prove the current property file without invoking the LLM agent.

    Same licence footprint as :func:`svapshot_prove`, but it only runs the
    formal tool and qualifies the log. Use it when the properties come from
    somewhere other than SVApshot's agent — a hand-written seed, or an agent
    reached over the tool server.
    """
    dut, run_root, upstream = unwrap(dut), unwrap(run_root), unwrap(upstream)
    if not upstream.ok:
        return StageResult(
            stage="formal",
            module=dut.module,
            ok=False,
            returncode=-1,
            elapsed_s=0.0,
            workspace=run_root,
            stderr_tail=f"skipped: {upstream.stage} stage failed",
        )
    return _run_stage("formal", dut, cfg, run_root)


def _parse_properties(prop_path: str) -> List[PropertyRecord]:
    with open(prop_path, "r", errors="replace") as handle:
        text = handle.read()
    records: List[PropertyRecord] = []
    seen = set()
    for match in _PROPERTY_RE.finditer(text):
        name = match.group("name")
        if name in seen:
            continue
        seen.add(name)
        records.append(PropertyRecord(name=name, kind=match.group("kind")))
    for match in _LABELLED_RE.finditer(text):
        name = match.group("name")
        if name not in seen:
            seen.add(name)
            records.append(PropertyRecord(name=name, kind=match.group("kind")))
    return records


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", errors="replace") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


@ChiaFunction()
def collect_snapshot(dut: DutSpec, run_root: str, prove: StageResult) -> Snapshot:
    """Turn SVApshot's on-disk output into a typed :class:`Snapshot`.

    Prefers ``snapshot_metrics.json`` when the run got far enough to emit it and
    falls back to counting properties in the generated property file, so a
    partially successful run still yields a usable object instead of an error.
    """
    dut, run_root, prove = unwrap(dut), unwrap(run_root), unwrap(prove)
    sva_dir = os.path.join(run_root, f"ft_{dut.module}", "sva")
    snap = Snapshot(module=dut.module, workspace=run_root, ok=prove.ok)

    prop_path = os.path.join(sva_dir, f"{dut.module}_prop.sv")
    if os.path.isfile(prop_path):
        snap.prop_file = prop_path
        snap.properties = _parse_properties(prop_path)
    else:
        snap.notes.append(f"no property file at {prop_path}")

    snap.manifest = _read_json(os.path.join(sva_dir, "snapshot_manifest.json"))
    snap.metrics = _read_json(os.path.join(sva_dir, "snapshot_metrics.json"))

    qual = (snap.metrics.get("qualification") or {}) if snap.metrics else {}
    if qual:
        snap.proved = int(qual.get("proved_non_vacuous", 0))
        snap.vacuous = int(qual.get("proved_vacuous", 0))
        snap.failed = int(qual.get("failing_property_mismatch", 0)) + int(
            qual.get("failing_missing_assumption", 0)
        )
        snap.inconclusive = int(qual.get("inconclusive", 0))
        snap.snapshot_worthy = snap.proved
        snap.total_properties = int(qual.get("total_properties", 0))
    else:
        snap.notes.append("no snapshot_metrics.json; counts derived from property file only")
        snap.inconclusive = len(snap.properties)

    return snap


@ChiaFunction()
def score_snapshot(snapshot: Snapshot, target_yield: float, iteration: int) -> Verdict:
    """Decide whether the contract is good enough, and say why if it is not.

    The feedback string is what the loop hands to the agent on the next turn, so
    it is written for an LLM reader rather than for a log.
    """
    snapshot = unwrap(snapshot)
    ratio = snapshot.yield_ratio
    if snapshot.total == 0:
        return Verdict(
            accepted=False,
            iteration=iteration,
            yield_ratio=0.0,
            target_yield=target_yield,
            reason="no properties were generated",
            feedback=(
                f"SVApshot produced no properties for {snapshot.module}. "
                f"Notes: {'; '.join(snapshot.notes) or 'none'}. "
                "Inspect the scaffolding and propose an initial set of SVA "
                "properties for this module."
            ),
        )

    if ratio >= target_yield:
        return Verdict(
            accepted=True,
            iteration=iteration,
            yield_ratio=ratio,
            target_yield=target_yield,
            reason=(
                f"{snapshot.snapshot_worthy}/{snapshot.total} properties are "
                f"snapshot-worthy ({ratio:.0%} >= {target_yield:.0%})"
            ),
        )

    return Verdict(
        accepted=False,
        iteration=iteration,
        yield_ratio=ratio,
        target_yield=target_yield,
        reason=(
            f"snapshot yield {ratio:.0%} is below the {target_yield:.0%} target"
        ),
        feedback=(
            f"Module {snapshot.module}: {snapshot.total} properties generated but "
            f"only {snapshot.snapshot_worthy} are snapshot-worthy "
            f"({snapshot.failed} failing, {snapshot.vacuous} vacuous, "
            f"{snapshot.inconclusive} inconclusive). Read the RTL and the current "
            "property file, then propose replacements for the properties that did "
            "not prove non-vacuously."
        ),
    )
