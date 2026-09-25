#!/usr/bin/env python3
"""CHIA PPA loop: formal gate, baseline PPA, snapshot, rewrite, retain, reprove, score, LEC.

Reading top to bottom, one DUT freezes a snapshot once, then runs three
rewrite modes (ports stay frozen):

    optimise  keep interface and function, better PPA (retry toward a passing snapshot)
    expand    add capability, keep the old one
    trim      drop to the essential path

The two headline cases are better Yosys PPA with a passing snapshot, and a
failing snapshot on the new RTL. A snapshot-clean rewrite then gets an
observational EQY LEC: a LEC fail does not fail the loop, it shows LEC is
stricter than the snapshot as a functional gate.

The first six arrows of a successful pass are programmatic CHIA edges:
each node is dispatched with ``chia_remote(...)`` and the ``ObjectRef`` is
the next node's argument. VC Formal elaborate is never retried; a DUT that
does not elaborate is out of the loop. The only back-edge is reprove →
implementation.

Run it::

    ./run_ppa.sh --design sargantana                 # offline bypass
    ./run_ppa.sh --real --design sargantana          # VCF + Yosys + Vertex + reprove
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import ray
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chia_svapshot import ppa_offline
from chia_svapshot.ppa_nodes import (
    llm_rtl_rewrite,
    retain_snapshot,
    score_ppa,
    stage_ppa_dut,
    svapshot_generate_and_freeze,
    svapshot_reprove,
    vcf_elaborate,
    yosys_lec,
    yosys_ppa,
)
from chia_svapshot.ppa_rtl import (
    REWRITE_MODES,
    canonical_rewrite_mode,
    is_optimise_mode,
    classify_mode_outcome,
    headline_for,
    lec_headline,
)
from chia_svapshot.state import unwrap


def _dump(path: str, payload: Any) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=_jsonable)
        handle.write("\n")
    return path


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return str(value)


def _asdict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    raw = getattr(value, "__dict__", None)
    return dict(raw) if raw is not None else {"value": value}


def main(argv: Optional[List[str]] = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.environ.get("SVAPSHOT_ROOT") or os.path.abspath(
        os.path.join(here, "..")
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design",
        action="append",
        dest="designs",
        help="Target key from hackathon/targets.yaml (repeatable)",
    )
    parser.add_argument(
        "--targets",
        default=os.path.join(here, "hackathon", "targets.yaml"),
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Root for run artifacts (default: runs/ppa_<design>)",
    )
    parser.add_argument("--rewrite-attempts", type=int, default=3)
    parser.add_argument(
        "--retain-mode", default="exists", choices=["exists", "touched"],
    )
    parser.add_argument("--formal-tool", default="vcformal")
    parser.add_argument("--target-yield", type=float, default=0.0)
    parser.add_argument(
        "--svapshot-model",
        default=os.environ.get("SVAPSHOT_LLM_MODEL", "gemini-2.5-pro"),
    )
    parser.add_argument(
        "--reuse-snapshot",
        default="",
        help="Directory with <module>_prop.sv + snapshot_metrics.json to skip generation",
    )
    parser.add_argument(
        "--rewrite-rtl",
        default="",
        help="Seed the first optimise attempt from this SystemVerilog file (ports still checked)",
    )
    parser.add_argument(
        "--rewrite-modes",
        default="optimise,expand,trim",
        help="Comma-separated rewrite modes after the snapshot (default: all three)",
    )
    parser.add_argument("--ray-address", default=None)
    parser.add_argument("--bypass-config", default=None)
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--summary-json", default=None)
    args = parser.parse_args(argv)

    designs = args.designs or ["sargantana"]
    rewrite_modes = [
        canonical_rewrite_mode(mode.strip())
        for mode in (args.rewrite_modes or "").split(",")
        if mode.strip()
    ]
    unknown = [mode for mode in rewrite_modes if mode not in REWRITE_MODES]
    if unknown:
        parser.error(f"unknown rewrite mode(s) {unknown}; have {REWRITE_MODES}")
    if not rewrite_modes:
        rewrite_modes = list(REWRITE_MODES)

    py_path = os.pathsep.join(
        [
            here,
            root,
            os.path.join(root, "src", "core"),
            os.path.join(root, "src", "analysis"),
            os.environ.get("PYTHONPATH", ""),
        ]
    )
    runtime_env = {
        "env_vars": {
            "PYTHONPATH": py_path,
            "SVAPSHOT_ROOT": root,
            "PYTHONUNBUFFERED": "1",
            "SVAPSHOT_PYTHON": os.environ.get("SVAPSHOT_PYTHON", sys.executable),
            "SVAPSHOT_LLM_MODEL": os.environ.get("SVAPSHOT_LLM_MODEL", args.svapshot_model),
            "SVAPSHOT_MAX_ASSERTIONS": os.environ.get("SVAPSHOT_MAX_ASSERTIONS", "50"),
            "SVAPSHOT_COVERAGE": os.environ.get("SVAPSHOT_COVERAGE", "0"),
            "SVAPSHOT_YOSYS_ABC": os.environ.get("SVAPSHOT_YOSYS_ABC", ""),
            "SVAPSHOT_VCF_DOCKER": os.environ.get("SVAPSHOT_VCF_DOCKER", ""),
            "GOOGLE_CLOUD_PROJECT": os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
            "GOOGLE_CLOUD_LOCATION": os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
            "PATH": os.environ.get("PATH", ""),
            "SNPSLMD_LICENSE_FILE": os.environ.get("SNPSLMD_LICENSE_FILE", ""),
        }
    }
    if args.ray_address:
        ray.init(address=args.ray_address, runtime_env=runtime_env)
    else:
        ray.init(
            resources={"svapshot_cpu": 4, "llm": 2, "formal": 1, "yosys": 2},
            ignore_reinit_error=True,
            log_to_driver=False,
            runtime_env=runtime_env,
        )

    if args.profile_dir:
        from chia.trace.profiler import start_collector

        os.makedirs(args.profile_dir, exist_ok=True)
        start_collector(log_dir=args.profile_dir)

    bypass = Bypass(yaml_path=args.bypass_config)
    ppa_offline.register(bypass)

    os.environ.setdefault("SVAPSHOT_ROOT", root)
    os.environ.setdefault("SVAPSHOT_LLM_MODEL", args.svapshot_model)
    os.environ.setdefault("SVAPSHOT_MAX_ASSERTIONS", "50")
    os.environ.setdefault("SVAPSHOT_COVERAGE", "0")

    print("== CHIA PPA loop ==")
    print(f"   designs   {', '.join(designs)}")
    print(f"   modes     {', '.join(rewrite_modes)}")
    print(f"   bypass    {args.bypass_config or 'disabled (everything runs for real)'}")
    print()

    started = time.time()
    reports: List[Dict[str, Any]] = []
    all_ok = True

    for name in designs:
        workspace = args.workspace or os.path.join(here, "runs", f"ppa_{name}")
        if args.workspace and len(designs) > 1:
            workspace = os.path.join(args.workspace, name)
        os.makedirs(workspace, exist_ok=True)
        print(f"\n== {name} :: {workspace} ==")
        report = _run_one(
            name=name,
            workspace=workspace,
            targets_path=args.targets,
            rewrite_attempts=args.rewrite_attempts,
            retain_mode=args.retain_mode,
            formal_tool=args.formal_tool,
            target_yield=args.target_yield,
            model=args.svapshot_model,
            reuse_snapshot=args.reuse_snapshot,
            rewrite_rtl=args.rewrite_rtl,
            rewrite_modes=rewrite_modes,
        )
        reports.append(report)
        all_ok = all_ok and bool(report.get("ok"))
        print(f"   [{('ok' if report.get('ok') else 'FAIL')}] {name}: {report.get('reason')}")

    elapsed = time.time() - started
    summary = {
        "elapsed_s": round(elapsed, 2),
        "designs": reports,
        "ok": all_ok,
        "bypass_config": args.bypass_config,
    }
    out = args.summary_json or os.path.join(
        args.workspace or os.path.join(here, "runs"), "ppa_loop_summary.json"
    )
    if args.workspace is None and len(designs) == 1:
        out = os.path.join(here, "runs", f"ppa_{designs[0]}", "summary.json")
    _dump(out, summary)
    print(f"\n== done in {elapsed:.1f}s == {out}")
    ray.shutdown()
    return 0 if all_ok else 1


def _run_one(
    *,
    name: str,
    workspace: str,
    targets_path: str,
    rewrite_attempts: int,
    retain_mode: str,
    formal_tool: str,
    target_yield: float,
    model: str,
    reuse_snapshot: str = "",
    rewrite_rtl: str = "",
    rewrite_modes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    # --------------------------------------------------------- entry gate
    # Both dispatches return immediately; the refs are the graph's edges.
    dut_ref = stage_ppa_dut.chia_remote(name, workspace, targets_path)
    elab_ref = vcf_elaborate.chia_remote(dut_ref)
    design, elab = unwrap(get(dut_ref)), unwrap(get(elab_ref))
    print(
        f"  [stage] {design.module}: {os.path.basename(design.origin)} "
        f"ports={len(design.ports)} type={design.module_type}"
    )
    print(f"  [elab]  ok={elab.ok} {elab.elapsed_s:.1f}s")
    if not elab.ok:
        print(f"         {elab.log_tail[-400:]}")
        return {
            "design": name,
            "module": design.module,
            "ok": False,
            "reason": "VC Formal elaborate failed (hard gate)",
            "elab": _asdict(elab),
        }

    base_ref = yosys_ppa.chia_remote(dut_ref, "baseline", elab_ref)
    baseline = unwrap(get(base_ref))
    print(
        f"  [ppa]   baseline cells={baseline.cells} levels={baseline.levels} "
        f"ok={baseline.ok} {baseline.elapsed_s:.1f}s"
    )
    if not baseline.ok:
        return {
            "design": name,
            "module": design.module,
            "ok": False,
            "reason": "baseline Yosys PPA failed",
            "elab": _asdict(elab),
            "baseline_ppa": _asdict(baseline),
        }

    # --------------------------------------------------------- snapshot
    snap_ref = svapshot_generate_and_freeze.chia_remote(
        dut_ref, base_ref, target_yield, formal_tool, reuse_snapshot,
    )
    snapshot = unwrap(get(snap_ref))
    print(
        f"  [snap]  {snapshot.snapshot_worthy}/{snapshot.total} worthy "
        f"ok={snapshot.ok} — {snapshot.reason}"
    )
    if not snapshot.ok:
        return {
            "design": name,
            "module": design.module,
            "ok": False,
            "reason": snapshot.reason,
            "elab": _asdict(elab),
            "baseline_ppa": _asdict(baseline),
            "snapshot": _asdict(snapshot),
        }

    # --------------------------------------------------------- rewrite modes
    # One snapshot, then optimise / expand / trim. Optimise retries toward a
    # passing contract. Expand and trim record a failing snapshot as a result.
    modes = rewrite_modes or list(REWRITE_MODES)
    mode_reports: List[Dict[str, Any]] = []
    headlines: List[str] = []
    for mode in modes:
        print(f"  === mode {mode} ===")
        mode_report = _run_mode(
            mode=mode,
            design=design,
            dut_ref=dut_ref,
            snap_ref=snap_ref,
            base_ref=base_ref,
            rewrite_attempts=rewrite_attempts,
            retain_mode=retain_mode,
            formal_tool=formal_tool,
            model=model,
            rewrite_rtl=rewrite_rtl if is_optimise_mode(mode) else "",
        )
        mode_reports.append(mode_report)
        headline = mode_report.get("headline") or ""
        if headline:
            headlines.append(f"{mode}: {headline}")
            print(f"    [case]  {headline}")
        lec_line = mode_report.get("lec_headline") or ""
        if lec_line:
            headlines.append(f"{mode}: {lec_line}")
            print(f"    [case]  {lec_line}")

    return {
        "design": name,
        "module": design.module,
        "ok": True,
        "reason": "; ".join(headlines) or "modes completed (no headline case)",
        "elab": _asdict(elab),
        "baseline_ppa": _asdict(baseline),
        "snapshot": _asdict(snapshot),
        "modes": mode_reports,
        "headlines": headlines,
        "compatibility_ok": True,
    }


def _run_mode(
    *,
    mode: str,
    design,
    dut_ref,
    snap_ref,
    base_ref,
    rewrite_attempts: int,
    retain_mode: str,
    formal_tool: str,
    model: str,
    rewrite_rtl: str,
) -> Dict[str, Any]:
    goal = (design.rewrite_modes or {}).get(mode) or design.rewrite_goal
    attempts_budget = rewrite_attempts if is_optimise_mode(mode) else 1
    feedback = ""
    rewrite = retain = reprove = None
    attempts: List[Dict[str, Any]] = []
    for attempt in range(attempts_budget):
        tag = f"{mode}_rw{attempt}"
        print(f"    --- {mode} attempt {attempt} ---")
        seed = rewrite_rtl if attempt == 0 else ""
        rewrite_ref = llm_rtl_rewrite.chia_remote(
            dut_ref, attempt, feedback, model, seed, goal, mode, _chia_tag=tag,
        )
        retain_ref = retain_snapshot.chia_remote(
            dut_ref, snap_ref, rewrite_ref, retain_mode, _chia_tag=tag,
        )
        reprove_ref = svapshot_reprove.chia_remote(
            dut_ref, rewrite_ref, retain_ref, formal_tool, _chia_tag=tag,
        )
        rewrite, retain, reprove = (
            unwrap(value)
            for value in get([rewrite_ref, retain_ref, reprove_ref])
        )
        print(
            f"    [rtl]    ok={rewrite.ok} ports_ok={rewrite.ports_ok} "
            f"— {rewrite.reason}"
        )
        print(
            f"    [retain] kept={retain.kept} dropped={retain.dropped} "
            f"— {retain.reason}"
        )
        print(
            f"    [reprove] ok={reprove.ok} "
            f"{reprove.snapshot_worthy}/{reprove.total} — {reprove.reason}"
        )
        attempts.append(
            {
                "attempt": attempt,
                "rewrite": _asdict(rewrite),
                "retain": {k: _asdict(retain).get(k) for k in (
                    "ok", "kept", "dropped", "kept_names", "reason"
                )},
                "reprove": _asdict(reprove),
            }
        )
        if reprove.ok:
            break
        feedback = reprove.feedback or rewrite.reason
        if attempt == attempts_budget - 1 and is_optimise_mode(mode):
            print("    [loop] optimise budget exhausted")

    candidate = score = lec = None
    if rewrite is not None and rewrite.ok and rewrite.ports_ok:
        cand_ref = yosys_ppa.chia_remote(rewrite, f"candidate_{mode}", rewrite)
        score_ref = score_ppa.chia_remote(dut_ref, base_ref, cand_ref)
        candidate, score = unwrap(get(cand_ref)), unwrap(get(score_ref))
        print(
            f"    [ppa]   {mode} cells={candidate.cells} "
            f"levels={candidate.levels} ok={candidate.ok}"
        )
        print(f"    [score] win={score.win} — {score.reason}")
    if (
        rewrite is not None
        and rewrite.ok
        and rewrite.ports_ok
        and reprove is not None
        and reprove.ok
    ):
        lec_ref = yosys_lec.chia_remote(dut_ref, rewrite, reprove, _chia_tag=f"{mode}_lec")
        lec = unwrap(get(lec_ref))
        print(
            f"    [lec]   equivalent={lec.equivalent} "
            f"inconclusive={lec.inconclusive} skipped={lec.skipped} "
            f"— {lec.reason}"
        )

    expand_followup = {}
    if (
        mode == "expand"
        and rewrite is not None
        and rewrite.ok
        and reprove is not None
        and reprove.ok
        and os.path.isfile(getattr(design, "rtl_path", "") or "")
    ):
        from chia_svapshot.expand_followup import expand_coverage_step

        snap = unwrap(get(snap_ref))
        follow_dir = os.path.join(design.workspace, "expand_followup")
        try:
            with open(design.rtl_path, encoding="utf-8", errors="replace") as handle:
                baseline_rtl = handle.read()
            with open(rewrite.rtl_path, encoding="utf-8", errors="replace") as handle:
                candidate_rtl = handle.read()
            expand_followup = expand_coverage_step(
                module=design.module,
                baseline_rtl=baseline_rtl,
                candidate_rtl=candidate_rtl,
                frozen_prop=snap.prop_file,
                workspace=follow_dir,
                model=model,
            )
            print(f"    [expand] {expand_followup.get('reason')}")
        except Exception as exc:  # noqa: BLE001 — observational; must not fail the mode
            expand_followup = {"ok": False, "reason": f"expand follow-up failed: {exc}"}
            print(f"    [expand] {expand_followup['reason']}")

    snapshot_ok = bool(reprove and reprove.ok)
    outcome = classify_mode_outcome(
        rewrite_ok=bool(rewrite and rewrite.ok and rewrite.ports_ok),
        snapshot_ok=snapshot_ok,
        ppa_win=bool(score and score.win),
    )
    headline = headline_for(outcome)
    lec_line = lec_headline(
        snapshot_ok=snapshot_ok,
        equivalent=bool(lec and lec.equivalent),
        inconclusive=bool(lec and lec.inconclusive),
        skipped=bool(lec is None or lec.skipped),
    )
    return {
        "mode": mode,
        "outcome": outcome,
        "headline": headline,
        "lec_headline": lec_line,
        "ok": snapshot_ok,
        "reason": (
            headline
            or lec_line
            or (score.reason if score else None)
            or (reprove.reason if reprove else "no reprove")
        ),
        "attempts": attempts,
        "candidate_ppa": _asdict(candidate) if candidate else {},
        "score": _asdict(score) if score else {},
        "lec": _asdict(lec) if lec else {},
        "expand_followup": expand_followup,
    }


if __name__ == "__main__":
    sys.exit(main())
