"""CHIA nodes for the PPA loop.

Each ``@ChiaFunction`` is one schedulable unit. Resource labels match
``cluster.yaml``; passing the previous node's return value is the edge.

===============================  ============  ================================
node                             resource      why it is scarce
===============================  ============  ================================
``stage_ppa_dut``                svapshot_cpu  filesystem copy
``vcf_elaborate``                formal        VC Formal licence (hard gate)
``yosys_ppa``                    yosys         Yosys synth (baseline / candidate)
``svapshot_generate_and_freeze`` formal        generation holds the licence
``llm_rtl_rewrite``              llm           RTL implementation credits
``retain_snapshot``              svapshot_cpu  signal-set filter
``svapshot_reprove``             formal        re-proof of retained SVA
``score_ppa``                    *(none)*      pure comparison
``yosys_lec``                    yosys         EQY after a snapshot-clean rewrite
===============================  ============  ================================
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Union

from .ppa_rtl import (
    canonical_rewrite_mode,
    expand_path,
    extract_assertions,
    extract_ports,
    extract_sv_module,
    failed_count,
    port_diff,
    ports_equal,
    proved_names,
    reprove_passed,
    splice_assertions,
)
from .ppa_state import (
    ElabResult,
    FrozenSnapshot,
    LecResult,
    PpaDesign,
    PpaMeasure,
    PpaScore,
    ReproveResult,
    RetainResult,
    RewriteResult,
)
from .state import unwrap

try:
    from chia.base.ChiaFunction import ChiaFunction
except ImportError:  # pragma: no cover — loop still reads without CHIA installed
    def ChiaFunction(*_args, **_kwargs):  # type: ignore[misc]
        def decorate(fn):
            return fn
        return decorate


_HERE = os.path.dirname(os.path.abspath(__file__))
_HACK = os.path.abspath(os.path.join(_HERE, "..", "hackathon"))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _HACK not in sys.path:
    sys.path.insert(0, _HACK)
_ANALYSIS = os.path.join(_REPO, "src", "analysis")
_CORE = os.path.join(_REPO, "src", "core")
for _extra in (_ANALYSIS, _CORE):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)


def _hack_nodes():
    from types import SimpleNamespace

    from nodes.emit_verilog import EmitResult
    from nodes.ppa_lec import run_lec
    from nodes.ppa_yosys import better, measure_ppa
    from nodes.snapshot import generate_and_freeze, reprove_frozen
    from nodes.vcf_elab import elaborate

    return SimpleNamespace(
        EmitResult=EmitResult,
        better=better,
        measure_ppa=measure_ppa,
        run_lec=run_lec,
        generate_and_freeze=generate_and_freeze,
        reprove_frozen=reprove_frozen,
        elaborate=elaborate,
    )


def _targets_path() -> str:
    return os.path.join(_HACK, "targets.yaml")


def _mapping() -> Dict[str, str]:
    return {
        "SVAPSHOT_ROOT": os.environ.get("SVAPSHOT_ROOT") or _REPO,
        "CHIPYARD_ROOT": os.environ.get("CHIPYARD_ROOT")
        or os.path.expanduser("~/chipyard"),
    }


def _collect_rtl(spec: Dict[str, Any], mapping: Dict[str, str]) -> tuple:
    """Expand ``rtl`` + ``rtl_globs``; ``*_pkg.sv`` files go to packages."""
    import glob

    seen: set = set()
    ordered: List[str] = []

    def add(path: str) -> None:
        path = os.path.abspath(path)
        if path in seen or not os.path.isfile(path):
            return
        seen.add(path)
        ordered.append(path)

    for raw in spec.get("rtl") or []:
        add(expand_path(raw, mapping))
    for pattern in spec.get("rtl_globs") or []:
        for path in sorted(glob.glob(expand_path(pattern, mapping))):
            add(path)
    def _is_package(path: str) -> bool:
        if os.path.basename(path).endswith("_pkg.sv"):
            return True
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                head = handle.read(4000)
        except OSError:
            return False
        return bool(re.search(r"(?m)^\s*package\s+\w+", head))

    rtl: List[str] = []
    packages: List[str] = []
    for path in ordered:
        if _is_package(path):
            packages.append(path)
        else:
            rtl.append(path)
    for raw in spec.get("packages") or []:
        path = os.path.abspath(expand_path(raw, mapping))
        if path not in packages and os.path.isfile(path):
            packages.append(path)
    package_names = {os.path.basename(path) for path in packages}
    rtl = [path for path in rtl if os.path.basename(path) not in package_names]
    includes = [
        os.path.abspath(expand_path(raw, mapping))
        for raw in spec.get("include_dirs") or []
    ]
    return rtl, packages, includes


def _expand_match_instance(
    spec: Dict[str, Any], mapping: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    raw = spec.get("match_instance")
    if not raw:
        return None
    from .ppa_rtl import expand_path

    parent = expand_path(raw.get("parent") or "", mapping)
    packages = [
        expand_path(path, mapping) for path in (raw.get("packages") or [])
    ]
    return {
        "parent": parent,
        "instance": (raw.get("instance") or "").strip(),
        "packages": [path for path in packages if path],
    }


def _resolve_match_instance(
    match: Optional[Dict[str, Any]],
    module: str,
    leaf_path: str,
) -> tuple:
    """Return (header defaults, prompt context, parent path, instance name)."""
    if not match or not match.get("parent"):
        return {}, "", "", ""
    from instantiation_params import (
        format_instantiation_prompt,
        match_instance_setting,
    )

    context = match_instance_setting(
        match["parent"],
        module,
        instance=match.get("instance") or None,
        package_paths=match.get("packages") or [],
        leaf_path=leaf_path,
    )
    defaults = dict(context.resolved_elaboration_overrides)
    prompt = format_instantiation_prompt(context)
    return defaults, prompt, match["parent"], match.get("instance") or ""


def load_target(name: str, targets_path: Optional[str] = None) -> Dict[str, Any]:
    import yaml

    path = targets_path or _targets_path()
    with open(path, encoding="utf-8") as handle:
        blob = yaml.safe_load(handle)
    if name not in blob["designs"]:
        raise KeyError(f"unknown design {name!r}; have {sorted(blob['designs'])}")
    spec = blob["designs"][name]
    mapping = _mapping()
    rtl, packages, includes = _collect_rtl(spec, mapping)
    return {
        "name": name,
        "module": spec["snapshot_module"],
        "module_type": spec.get("module_type") or "sequential",
        "rtl": rtl,
        "packages": packages,
        "include_dirs": includes,
        "parameters": spec.get("parameters") or {},
        "rewrite": (spec.get("rewrite") or "").strip(),
        "rewrites": {
            canonical_rewrite_mode(key): (value or "").strip()
            for key, value in (spec.get("rewrites") or {}).items()
        },
        "rewrite_scope": (spec.get("rewrite_scope") or "top").strip(),
        "ppa_goal": spec.get("goal") or "area_or_delay",
        "max_regression": spec.get("max_regression", 0.05),
        "match_instance": _expand_match_instance(spec, mapping),
    }


def _stage_tree(
    dest: str,
    rtl_files: List[str],
    package_files: List[str],
    include_dirs: Optional[List[str]] = None,
) -> str:
    modules = os.path.join(dest, "modules")
    packages = os.path.join(dest, "packages")
    include = os.path.join(dest, "include")
    os.makedirs(modules, exist_ok=True)
    os.makedirs(packages, exist_ok=True)
    for src in rtl_files:
        shutil.copy2(src, os.path.join(modules, os.path.basename(src)))
    for src in package_files:
        shutil.copy2(src, os.path.join(packages, os.path.basename(src)))
    staged_include = ""
    for src in include_dirs or []:
        if not os.path.isdir(src):
            continue
        os.makedirs(include, exist_ok=True)
        for child in os.listdir(src):
            origin = os.path.join(src, child)
            target = os.path.join(include, child)
            if os.path.isdir(origin):
                if os.path.isdir(target):
                    shutil.copytree(origin, target, dirs_exist_ok=True)
                else:
                    shutil.copytree(origin, target)
            else:
                shutil.copy2(origin, target)
        # Snapshot generation uses +incdir+packages; keep common_cells/ there too.
        for child in os.listdir(src):
            origin = os.path.join(src, child)
            target = os.path.join(packages, child)
            if os.path.isdir(origin):
                if os.path.isdir(target):
                    shutil.copytree(origin, target, dirs_exist_ok=True)
                else:
                    shutil.copytree(origin, target)
        staged_include = include
    return staged_include


def _sources_of(tree: str, rtl_files: List[str], package_files: List[str]) -> List[str]:
    paths = [
        os.path.join(tree, "packages", os.path.basename(src)) for src in package_files
    ]
    paths += [os.path.join(tree, "modules", os.path.basename(src)) for src in rtl_files]
    return paths


def _include_dirs_of(staged: object) -> List[str]:
    listed = list(getattr(staged, "include_dirs", None) or [])
    tree = getattr(staged, "tree", "") or ""
    auto = os.path.join(tree, "include") if tree else ""
    if auto and os.path.isdir(auto) and auto not in listed:
        listed.append(auto)
    return [path for path in listed if os.path.isdir(path)]


def _emit(design: Union[PpaDesign, RewriteResult], workspace: str):
    ns = _hack_nodes()
    tree = design.tree
    files = list(design.sources)
    return ns.EmitResult(
        workspace=workspace,
        source_root=tree,
        modules=[design.module],
        files=files,
        notes=["chia ppa loop"],
    )


@ChiaFunction(resources={"svapshot_cpu": 1})
def stage_ppa_dut(
    name: str,
    workspace: str,
    targets_path: Optional[str] = None,
) -> PpaDesign:
    """Copy the snapshot DUT plus child RTL and packages into ``workspace``."""
    spec = load_target(name, targets_path)
    missing = [p for p in spec["rtl"] + spec["packages"] if not os.path.isfile(p)]
    missing += [p for p in spec.get("include_dirs") or [] if not os.path.isdir(p)]
    if missing:
        raise FileNotFoundError("missing RTL: " + ", ".join(missing))

    tree = os.path.join(os.path.abspath(workspace), "baseline_rtl")
    if os.path.isdir(tree):
        shutil.rmtree(tree)
    staged_include = _stage_tree(
        tree, spec["rtl"], spec["packages"], spec.get("include_dirs") or [],
    )
    sources = _sources_of(tree, spec["rtl"], spec["packages"])
    rtl_path = os.path.join(tree, "modules", f"{spec['module']}.sv")
    defaults, inst_context, match_parent, match_name = _resolve_match_instance(
        spec.get("match_instance"), spec["module"], rtl_path,
    )
    if defaults:
        from instantiation_params import (
            apply_parameter_defaults,
            numeric_yosys_parameters,
        )

        with open(rtl_path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        patched = apply_parameter_defaults(text, defaults)
        if patched != text:
            with open(rtl_path, "w", encoding="utf-8") as handle:
                handle.write(patched)
        yosys_params = dict(spec["parameters"])
        yosys_params.update(numeric_yosys_parameters(defaults))
        record = {
            "parent": match_parent,
            "instance": match_name,
            "defaults": defaults,
        }
        with open(os.path.join(tree, "match_instance.json"), "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
    else:
        yosys_params = dict(spec["parameters"])
    with open(rtl_path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    return PpaDesign(
        name=spec["name"],
        module=spec["module"],
        module_type=spec["module_type"],
        workspace=os.path.abspath(workspace),
        tree=tree,
        rtl_path=rtl_path,
        sources=sources,
        packages=[os.path.basename(p) for p in spec["packages"]],
        parameters=yosys_params,
        rewrite_goal=spec["rewrite"] or (spec["rewrites"].get("optimise") or ""),
        rewrite_modes=spec["rewrites"] or (
            {"optimise": spec["rewrite"]} if spec["rewrite"] else {}
        ),
        ppa_goal=spec["ppa_goal"],
        max_regression=float(spec["max_regression"]),
        origin=spec["rtl"][0],
        ports=extract_ports(text, spec["module"]),
        include_dirs=[staged_include] if staged_include else [],
        rewrite_scope=spec.get("rewrite_scope") or "top",
        instantiation_context=inst_context,
        parameter_defaults=defaults,
        match_parent=match_parent,
        match_instance=match_name,
    )


@ChiaFunction(resources={"formal": 1})
def vcf_elaborate(design: PpaDesign) -> ElabResult:
    """Hard gate: analyze + elaborate the baseline. Failure ends the loop."""
    design = unwrap(design)
    ns = _hack_nodes()
    workdir = os.path.join(design.workspace, "vcf_elab_base")
    started = time.time()
    report = ns.elaborate(
        design.sources,
        design.module,
        workdir=workdir,
        include_dirs=_include_dirs_of(design),
        parameters=design.parameters or None,
    )
    return ElabResult(
        ok=bool(report.get("ok")),
        module=design.module,
        returncode=int(report.get("returncode") or 0),
        elapsed_s=round(time.time() - started, 2),
        workspace=workdir,
        log_tail=str(report.get("log_tail") or "")[-2500:],
    )


@ChiaFunction(resources={"yosys": 1})
def yosys_ppa(
    staged: Union[PpaDesign, RewriteResult],
    label: str,
    upstream: Optional[object] = None,
) -> PpaMeasure:
    """Yosys ``synth; stat; ltp`` on baseline or candidate sources."""
    staged = unwrap(staged)
    if upstream is not None:
        upstream = unwrap(upstream)
        ok = getattr(upstream, "ok", True)
        if ok is False:
            return PpaMeasure(
                ok=False,
                label=label,
                module=getattr(staged, "module", ""),
                workspace=getattr(staged, "workspace", ""),
                log_tail=f"skipped: upstream {type(upstream).__name__} failed",
            )
    ns = _hack_nodes()
    workdir = os.path.join(
        getattr(staged, "workspace", None)
        or os.path.dirname(getattr(staged, "tree", ".")),
        f"yosys_{label}",
    )
    started = time.time()
    report = ns.measure_ppa(
        list(staged.sources),
        staged.module,
        workdir=workdir,
        parameters=getattr(staged, "parameters", None) or None,
        include_dirs=_include_dirs_of(staged),
    )
    return PpaMeasure(
        ok=bool(report.get("ok")),
        label=label,
        module=staged.module,
        cells=report.get("cells"),
        wires=report.get("wires"),
        levels=report.get("levels"),
        area_um2=report.get("area_um2"),
        pdk=str(report.get("pdk") or ""),
        frontend=str(report.get("frontend") or ""),
        elapsed_s=round(time.time() - started, 2),
        workspace=workdir,
        log_tail=str(report.get("log_tail") or "")[-2000:],
    )


def _frozen_from_dir(design: PpaDesign, reuse_dir: str) -> FrozenSnapshot:
    """Copy an already-frozen contract into this run's workspace."""
    only_proven = os.path.join(reuse_dir, f"{design.module}_prop_only_proven.sv")
    full_prop = os.path.join(reuse_dir, f"{design.module}_prop.sv")
    prop_src = only_proven if os.path.isfile(only_proven) else full_prop
    metrics_src = os.path.join(reuse_dir, "snapshot_metrics.json")
    if not os.path.isfile(prop_src):
        raise FileNotFoundError(f"no property file in {reuse_dir}")
    dest = os.path.join(design.workspace, "contract", "frozen")
    os.makedirs(dest, exist_ok=True)
    prop_dest = os.path.join(dest, f"{design.module}_prop.sv")
    metrics_dest = os.path.join(dest, "snapshot_metrics.json")
    shutil.copy2(prop_src, prop_dest)
    metrics: dict = {}
    if os.path.isfile(metrics_src):
        shutil.copy2(metrics_src, metrics_dest)
        with open(metrics_dest, encoding="utf-8") as handle:
            metrics = json.load(handle)
    qual = metrics.get("qualification") or {}
    props = metrics.get("properties") or qual.get("properties") or {}
    worthy = int(qual.get("proved_non_vacuous") or 0)
    if worthy == 0 and props:
        worthy = sum(
            1
            for status in props.values()
            if status in ("proved_non_vacuous", "proven", "proven_non_vacuous")
        )
    total = int(qual.get("total_properties") or 0) or len(props)
    if total == 0:
        import re

        with open(prop_dest, encoding="utf-8", errors="replace") as handle:
            total = len(re.findall(r"^\s*\w+\s*:\s*assert\s+property", handle.read(), re.M))
    ratio = (worthy / total) if total else 0.0
    return FrozenSnapshot(
        ok=worthy > 0,
        module=design.module,
        directory=dest,
        prop_file=prop_dest,
        metrics_file=metrics_dest,
        snapshot_worthy=worthy,
        total=total,
        yield_ratio=ratio,
        reason=f"{worthy}/{total} properties reused from {reuse_dir}",
        workspace=design.workspace,
        elapsed_s=0.0,
    )


@ChiaFunction(resources={"formal": 1})
def svapshot_generate_and_freeze(
    design: PpaDesign,
    baseline: PpaMeasure,
    target_yield: float = 0.0,
    formal_tool: str = "vcformal",
    reuse_dir: str = "",
) -> FrozenSnapshot:
    """Generate SVA on the baseline DUT and freeze proved-non-vacuous properties."""
    design, baseline = unwrap(design), unwrap(baseline)
    reuse_dir = unwrap(reuse_dir) or ""
    if not baseline.ok:
        return FrozenSnapshot(
            ok=False,
            module=design.module,
            directory="",
            prop_file="",
            metrics_file="",
            snapshot_worthy=0,
            total=0,
            yield_ratio=0.0,
            reason="skipped: baseline Yosys PPA failed",
            workspace=design.workspace,
        )
    if reuse_dir:
        return _frozen_from_dir(design, reuse_dir)
    ns = _hack_nodes()
    started = time.time()
    contract = ns.generate_and_freeze(
        _emit(design, os.path.join(design.workspace, "snap")),
        design.module,
        target_yield=target_yield,
        formal_tool=formal_tool,
        module_type=design.module_type,
        instantiation_context=design.instantiation_context,
        elaboration_overrides=design.parameter_defaults,
    )
    ok = bool(contract.prop_file and os.path.isfile(contract.prop_file)
              and contract.snapshot_worthy > 0)
    return FrozenSnapshot(
        ok=ok,
        module=contract.module,
        directory=contract.directory,
        prop_file=contract.prop_file,
        metrics_file=contract.metrics_file,
        snapshot_worthy=contract.snapshot_worthy,
        total=contract.total,
        yield_ratio=contract.yield_ratio,
        reason=contract.reason if ok else (
            contract.reason or "SVApshot produced no proved-non-vacuous properties"
        ),
        workspace=os.path.join(design.workspace, "snap"),
        elapsed_s=round(time.time() - started, 2),
    )


_MODE_PREAMBLE = {
    "optimise": (
        "Mode: optimise. Keep interface, keep functionality and optimise "
        "RTL for better PPA. Every existing result must stay bit-identical "
        "for the same inputs."
    ),
    "expand": (
        "Mode: expand. Keep every current result. Add one extra capability "
        "that is still driven from the existing port list (no new pins)."
    ),
    "trim": (
        "Mode: trim. Same port list. Strip the module to its essential path "
        "and drop non-essential features. Unused outputs may be tied off."
    ),
}


def _rewrite_prompt(
    module: str,
    rtl: str,
    goal: str,
    ports: list,
    feedback: str,
    mode: str = "optimise",
    rewrite_scope: str = "top",
    parameter_defaults: Optional[Dict[str, str]] = None,
) -> str:
    mode = canonical_rewrite_mode(mode)
    port_lines = ", ".join(f"{p.direction} {p.packed} {p.name}".strip() for p in ports)
    extra = f"\n\nPrevious attempt was rejected:\n{feedback}\n" if feedback else ""
    if parameter_defaults:
        frozen = ", ".join(
            f"{name}={value}"
            for name, value in sorted(parameter_defaults.items())
        )
        extra += (
            "\nKeep these parameter defaults exactly (parent instance "
            f"setting):\n{frozen}\n"
        )
    preamble = _MODE_PREAMBLE.get(mode, _MODE_PREAMBLE["optimise"])
    if rewrite_scope == "hierarchy":
        scope_rule = (
            "- Scope: hierarchy. You may name child modules to rewrite, but "
            "this prompt still returns only the listed module body.\n"
        )
    else:
        scope_rule = (
            "- Scope: top-level. Rewrite ONLY this top module. Do not emit "
            "or rewrite child modules; keep their instantiations as leaves. "
            "You only have top-level ports, internals, and pipeline/handshake "
            "structure to work with.\n"
        )
    return (
        f"Rewrite SystemVerilog module `{module}`.\n{preamble}\n\n"
        f"Goal:\n{goal}\n\n"
        "Hard constraints:\n"
        "- Return the complete module from `module` through `endmodule` and "
        "nothing else. Do not truncate.\n"
        "- Do not modify the port list: no added, removed, renamed, or "
        "reordered ports, and no change to direction or width. Snapshot "
        "assertions bind these pins; a different port list breaks the "
        "snapshot.\n"
        f"- Frozen ports (copy exactly): {port_lines}\n"
        f"{scope_rule}"
        "- Child instantiations may stay. Keep the same module name.\n"
        f"{extra}\nCurrent RTL:\n```systemverilog\n{rtl}\n```\n"
    )


def _commit_candidate(
    design: PpaDesign, attempt: int, candidate: str, reason: str, feedback: str, started: float,
    mode: str = "optimise",
) -> RewriteResult:
    cand_ports = extract_ports(candidate, design.module)
    ports_ok = ports_equal(design.ports, cand_ports)
    if not ports_ok:
        return RewriteResult(
            ok=False,
            attempt=attempt,
            module=design.module,
            tree="",
            rtl_path="",
            ports_ok=False,
            reason=port_diff(design.ports, cand_ports) or "port list changed",
            feedback=feedback,
            rewrite_mode=mode,
            elapsed_s=round(time.time() - started, 2),
        )
    tree = os.path.join(design.workspace, f"candidate_rtl_{mode}_{attempt}")
    if os.path.isdir(tree):
        shutil.rmtree(tree)
    shutil.copytree(design.tree, tree)
    rtl_path = os.path.join(tree, "modules", f"{design.module}.sv")
    if design.parameter_defaults:
        from instantiation_params import apply_parameter_defaults

        candidate = apply_parameter_defaults(candidate, design.parameter_defaults)
    with open(rtl_path, "w", encoding="utf-8") as handle:
        handle.write(candidate)
        if not candidate.endswith("\n"):
            handle.write("\n")
    sources = []
    for dirname in ("packages", "modules"):
        folder = os.path.join(tree, dirname)
        if os.path.isdir(folder):
            sources.extend(
                os.path.join(folder, name)
                for name in sorted(os.listdir(folder))
                if name.endswith((".sv", ".v", ".svh"))
            )
    return RewriteResult(
        ok=True,
        attempt=attempt,
        module=design.module,
        tree=tree,
        rtl_path=rtl_path,
        sources=sources,
        parameters=design.parameters,
        ports_ok=True,
        reason=reason,
        feedback=feedback,
        rewrite_mode=mode,
        elapsed_s=round(time.time() - started, 2),
    )


@ChiaFunction(resources={"llm": 1})
def llm_rtl_rewrite(
    design: PpaDesign,
    attempt: int,
    feedback: str = "",
    model: Optional[str] = None,
    rewrite_rtl: str = "",
    rewrite_goal: str = "",
    rewrite_mode: str = "optimise",
) -> RewriteResult:
    """LLM implementation of one rewrite mode. Ports are frozen."""
    design = unwrap(design)
    rewrite_rtl = unwrap(rewrite_rtl) or ""
    rewrite_goal = unwrap(rewrite_goal) or design.rewrite_goal
    rewrite_mode = canonical_rewrite_mode(unwrap(rewrite_mode) or "optimise")
    started = time.time()
    with open(design.rtl_path, encoding="utf-8", errors="replace") as handle:
        baseline = handle.read()
    if rewrite_rtl:
        with open(rewrite_rtl, encoding="utf-8", errors="replace") as handle:
            candidate = handle.read()
        return _commit_candidate(
            design, attempt, candidate,
            f"seeded rewrite from {rewrite_rtl}",
            feedback, started, rewrite_mode,
        )
    model = model or os.environ.get("SVAPSHOT_LLM_MODEL") or "gemini-2.5-pro"
    prompt = _rewrite_prompt(
        design.module, baseline, rewrite_goal, design.ports, feedback,
        mode=rewrite_mode,
        rewrite_scope=getattr(design, "rewrite_scope", "top") or "top",
        parameter_defaults=design.parameter_defaults,
    )
    try:
        from vertex_llm import generate as vertex_generate
    except ImportError:
        sys.path.insert(0, os.path.join(_REPO, "src", "core"))
        from vertex_llm import generate as vertex_generate  # type: ignore

    # ptw/tlb-sized modules overflow the 16k default and come back without
    # `endmodule`. Give the rewrite enough room for a complete module.
    raw = vertex_generate(model, prompt, max_output_tokens=65536)
    try:
        candidate = extract_sv_module(raw, design.module)
    except ValueError as exc:
        return RewriteResult(
            ok=False,
            attempt=attempt,
            module=design.module,
            tree="",
            rtl_path="",
            reason=str(exc),
            feedback=feedback,
            rewrite_mode=rewrite_mode,
            elapsed_s=round(time.time() - started, 2),
        )
    return _commit_candidate(
        design, attempt, candidate,
        "ports frozen; candidate written",
        feedback, started, rewrite_mode,
    )


@ChiaFunction(resources={"svapshot_cpu": 1})
def retain_snapshot(
    design: PpaDesign,
    snapshot: FrozenSnapshot,
    rewrite: RewriteResult,
    mode: str = "exists",
) -> RetainResult:
    """Keep frozen properties whose signals still exist on the rewritten DUT."""
    from retain_after_refactor import kept_assertions, retain_after_refactor

    design, snapshot, rewrite = unwrap(design), unwrap(snapshot), unwrap(rewrite)
    started = time.time()
    if not rewrite.ok or not rewrite.ports_ok:
        return RetainResult(
            ok=False,
            module=design.module,
            prop_file="",
            kept=0,
            dropped=0,
            reason=rewrite.reason or "rewrite rejected",
            elapsed_s=round(time.time() - started, 2),
        )
    if not snapshot.prop_file or not os.path.isfile(snapshot.prop_file):
        return RetainResult(
            ok=False,
            module=design.module,
            prop_file="",
            kept=0,
            dropped=0,
            reason="no frozen property file",
            elapsed_s=round(time.time() - started, 2),
        )

    with open(snapshot.prop_file, encoding="utf-8", errors="replace") as handle:
        prop_text = handle.read()
    assertions = extract_assertions(prop_text)
    metrics: dict = {}
    if snapshot.metrics_file and os.path.isfile(snapshot.metrics_file):
        with open(snapshot.metrics_file, encoding="utf-8") as handle:
            metrics = json.load(handle)
    worthy = proved_names(metrics)
    if not worthy:
        # Paper-style metrics JSON has qualification counts but no per-name map.
        for extra in (
            os.path.join(os.path.dirname(snapshot.prop_file), "snapshot_metrics.json"),
            os.path.join(_REPO, "chia_svapshot", "snapshot_library",
                         design.module, "latest", "snapshot_metrics.json"),
        ):
            if extra and os.path.isfile(extra):
                with open(extra, encoding="utf-8") as handle:
                    worthy = proved_names(json.load(handle))
                if worthy:
                    break
    if worthy:
        from coi import assertion_name

        assertions = [
            row for row in assertions if (assertion_name(row) or "") in worthy
        ]

    with open(design.rtl_path, encoding="utf-8", errors="replace") as handle:
        baseline_rtl = handle.read()
    with open(rewrite.rtl_path, encoding="utf-8", errors="replace") as handle:
        candidate_rtl = handle.read()

    decisions = retain_after_refactor(
        assertions,
        candidate_rtl,
        module_name=design.module,
        baseline_rtl=baseline_rtl,
        mode=mode,
    )
    kept = kept_assertions(decisions)
    dest = os.path.join(
        design.workspace, "contract", "retained",
        rewrite.rewrite_mode or "optimise",
        f"{design.module}_prop.sv",
    )
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as handle:
        handle.write(splice_assertions(prop_text, kept))
    try:
        from chia_svapshot.snapshot_library import archive_snapshot

        archive_snapshot(design.module, os.path.dirname(dest), label="chia_retained")
    except Exception as exc:
        print(f"  [retain] archive skipped: {exc}", flush=True)
    return RetainResult(
        ok=bool(kept),
        module=design.module,
        prop_file=dest,
        kept=len(kept),
        dropped=len(decisions) - len(kept),
        kept_names=[row.name for row in decisions if row.kept],
        decisions=[row.to_dict() for row in decisions],
        reason=f"kept {len(kept)}/{len(decisions)}",
        elapsed_s=round(time.time() - started, 2),
    )


@ChiaFunction(resources={"formal": 1})
def svapshot_reprove(
    design: PpaDesign,
    rewrite: RewriteResult,
    retained: RetainResult,
    formal_tool: str = "vcformal",
) -> ReproveResult:
    """Re-prove retained SVA on the candidate. Failure returns to implementation."""
    ns = _hack_nodes()
    design, rewrite, retained = unwrap(design), unwrap(rewrite), unwrap(retained)
    started = time.time()
    if not rewrite.ok or not rewrite.ports_ok:
        ok, reason = reprove_passed(
            kept=0, snapshot_worthy=0, failed=0, ports_ok=False,
        )
        return ReproveResult(
            ok=ok,
            module=design.module,
            snapshot_worthy=0,
            total=0,
            failed=0,
            reason=reason,
            feedback=rewrite.reason,
            elapsed_s=round(time.time() - started, 2),
        )
    if not retained.ok or retained.kept <= 0:
        return ReproveResult(
            ok=False,
            module=design.module,
            snapshot_worthy=0,
            total=0,
            failed=0,
            reason=retained.reason or "no retained assertions",
            feedback=retained.reason,
            elapsed_s=round(time.time() - started, 2),
        )

    contract = ns.reprove_frozen(
        _emit(rewrite, os.path.join(design.workspace, f"reprove_{rewrite.attempt}")),
        design.module,
        retained.prop_file,
        formal_tool=formal_tool,
    )
    metrics: dict = {}
    if contract.metrics_file and os.path.isfile(contract.metrics_file):
        with open(contract.metrics_file, encoding="utf-8") as handle:
            metrics = json.load(handle)
    failed = failed_count(metrics)
    ok, reason = reprove_passed(
        kept=retained.kept,
        snapshot_worthy=contract.snapshot_worthy,
        failed=failed,
        ports_ok=True,
    )
    feedback = ""
    if not ok:
        feedback = (
            f"Reprove failed: {reason}. "
            f"proved={contract.snapshot_worthy} failed={failed} "
            f"total={contract.total}. "
            "Keep the original port list. Restore behaviour the snapshot checks."
        )
    return ReproveResult(
        ok=ok,
        module=design.module,
        snapshot_worthy=contract.snapshot_worthy,
        total=contract.total,
        failed=failed,
        reason=reason,
        feedback=feedback,
        prop_file=contract.prop_file,
        metrics=metrics,
        elapsed_s=round(time.time() - started, 2),
    )


@ChiaFunction()
def score_ppa(
    design: PpaDesign,
    baseline: PpaMeasure,
    candidate: PpaMeasure,
) -> PpaScore:
    """Compare candidate Yosys PPA against the recorded baseline."""
    ns = _hack_nodes()
    design, baseline, candidate = unwrap(design), unwrap(baseline), unwrap(candidate)
    if not candidate.ok:
        return PpaScore(
            win=False,
            reason="candidate Yosys PPA failed",
            baseline={"cells": baseline.cells, "levels": baseline.levels},
            candidate={"ok": False},
        )
    report = ns.better(
        {
            "cells": baseline.cells,
            "levels": baseline.levels,
            "area_um2": baseline.area_um2,
        },
        {
            "cells": candidate.cells,
            "levels": candidate.levels,
            "area_um2": candidate.area_um2,
        },
        goal=design.ppa_goal,
        max_regression=design.max_regression,
    )
    reason = (
        "PPA win"
        if report["win"]
        else "no PPA win (area/delay did not improve within tolerance)"
    )
    return PpaScore(
        win=bool(report["win"]),
        reason=reason,
        area_delta=report.get("area_delta"),
        delay_delta=report.get("delay_delta"),
        improved_area=bool(report.get("improved_area")),
        improved_delay=bool(report.get("improved_delay")),
        baseline={
            "cells": baseline.cells,
            "wires": baseline.wires,
            "levels": baseline.levels,
            "area_um2": baseline.area_um2,
            "pdk": baseline.pdk,
        },
        candidate={
            "cells": candidate.cells,
            "wires": candidate.wires,
            "levels": candidate.levels,
            "area_um2": candidate.area_um2,
            "pdk": candidate.pdk,
        },
    )


@ChiaFunction(resources={"yosys": 1})
def yosys_lec(
    design: PpaDesign,
    rewrite: RewriteResult,
    reprove: ReproveResult,
) -> LecResult:
    """EQY gold-vs-gate after a snapshot-clean rewrite. Failure is recorded, not a gate."""
    design, rewrite, reprove = unwrap(design), unwrap(rewrite), unwrap(reprove)
    started = time.time()
    workdir = os.path.join(design.workspace, f"lec_{rewrite.rewrite_mode or 'optimise'}")
    if not rewrite.ok or not rewrite.ports_ok:
        return LecResult(
            ok=True,
            equivalent=False,
            inconclusive=False,
            snapshot_clean=False,
            module=design.module,
            reason="skipped: rewrite rejected",
            skipped=True,
            workspace=workdir,
            elapsed_s=round(time.time() - started, 2),
        )
    if not reprove.ok:
        return LecResult(
            ok=True,
            equivalent=False,
            inconclusive=False,
            snapshot_clean=False,
            module=design.module,
            reason="skipped: rewrite is not snapshot-clean",
            skipped=True,
            workspace=workdir,
            elapsed_s=round(time.time() - started, 2),
        )
    ns = _hack_nodes()
    report = ns.run_lec(
        list(design.sources),
        list(rewrite.sources),
        design.module,
        workdir=workdir,
        gold_includes=_include_dirs_of(design),
        gate_includes=_include_dirs_of(rewrite),
        parameters=design.parameters or None,
    )
    return LecResult(
        ok=bool(report.get("ok")),
        equivalent=bool(report.get("equivalent")),
        inconclusive=bool(report.get("inconclusive")),
        snapshot_clean=True,
        module=design.module,
        reason=str(report.get("reason") or ""),
        partitions_failed=int(report.get("partitions_failed") or 0),
        frontend=str(report.get("frontend") or ""),
        elapsed_s=round(time.time() - started, 2),
        workspace=workdir,
        log_tail=str(report.get("log_tail") or "")[-2000:],
    )
