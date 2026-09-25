"""Bypass providers for the CHIA PPA loop.

A bypassed node is still dispatched through Ray with its real resource
label. Providers here return synthetic payloads so the graph can be
developed without a VC Formal licence, Yosys, or Vertex credits.
"""

from __future__ import annotations

import json
import os
import shutil

from .ppa_rtl import DESIGNER_MARKER, extract_ports
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


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def vcf_elaborate_provider(tag, data_path, *args, **kwargs) -> ElabResult:
    design: PpaDesign = unwrap(args[0])
    return ElabResult(
        ok=True,
        module=design.module,
        returncode=0,
        elapsed_s=0.0,
        workspace=os.path.join(design.workspace, "vcf_elab_base"),
        log_tail=f"[bypass] elaborate {design.module} tag={tag!r}",
        bypassed=True,
    )


def yosys_ppa_provider(tag, data_path, *args, **kwargs) -> PpaMeasure:
    staged = unwrap(args[0])
    label = unwrap(args[1]) if len(args) > 1 else "baseline"
    cells = 100 if label == "baseline" else 95
    return PpaMeasure(
        ok=True,
        label=label,
        module=getattr(staged, "module", ""),
        cells=cells,
        wires=cells * 2,
        levels=12 if label == "baseline" else 11,
        frontend="bypass",
        elapsed_s=0.0,
        workspace=getattr(staged, "workspace", "") or getattr(staged, "tree", ""),
        log_tail=f"[bypass] yosys {label} tag={tag!r}",
        bypassed=True,
    )


def generate_provider(tag, data_path, *args, **kwargs) -> FrozenSnapshot:
    design: PpaDesign = unwrap(args[0])
    frozen = os.path.join(design.workspace, "contract", "frozen")
    os.makedirs(frozen, exist_ok=True)
    prop = os.path.join(frozen, f"{design.module}_prop.sv")
    metrics_path = os.path.join(frozen, "snapshot_metrics.json")
    body = (
        f"module {design.module}_prop;\n"
        f"{DESIGNER_MARKER}\n"
        f"p_bypass: assert property (1'b1);\n"
        "endmodule\n"
    )
    with open(prop, "w", encoding="utf-8") as handle:
        handle.write(body)
    metrics = {
        "synthetic": True,
        "qualification": {
            "total_properties": 1,
            "proved_non_vacuous": 1,
            "properties": {"p_bypass": "proved_non_vacuous"},
        },
        "properties": {"p_bypass": "proved_non_vacuous"},
    }
    with open(metrics_path, "w", encoding="utf-8") as handle:
        json.dump(metrics, handle, indent=2)
        handle.write("\n")
    return FrozenSnapshot(
        ok=True,
        module=design.module,
        directory=frozen,
        prop_file=prop,
        metrics_file=metrics_path,
        snapshot_worthy=1,
        total=1,
        yield_ratio=1.0,
        reason="1/1 properties are snapshot-worthy (bypass)",
        workspace=design.workspace,
        elapsed_s=0.0,
        bypassed=True,
    )


def rewrite_provider(tag, data_path, *args, **kwargs) -> RewriteResult:
    design: PpaDesign = unwrap(args[0])
    attempt = unwrap(args[1]) if len(args) > 1 else 0
    rewrite_mode = unwrap(kwargs.get("rewrite_mode") or "")
    if not rewrite_mode and len(args) > 6:
        rewrite_mode = unwrap(args[6]) or ""
    rewrite_mode = rewrite_mode or "optimise"
    tree = os.path.join(design.workspace, f"candidate_rtl_{rewrite_mode}_{attempt}")
    if os.path.isdir(tree):
        shutil.rmtree(tree)
    shutil.copytree(design.tree, tree)
    rtl_path = os.path.join(tree, "modules", f"{design.module}.sv")
    text = _read(rtl_path)
    if "// chia-ppa-bypass" not in text:
        with open(rtl_path, "a", encoding="utf-8") as handle:
            handle.write("\n// chia-ppa-bypass: identity rewrite\n")
    sources = []
    for dirname in ("packages", "modules"):
        folder = os.path.join(tree, dirname)
        if os.path.isdir(folder):
            sources.extend(
                os.path.join(folder, name)
                for name in sorted(os.listdir(folder))
                if name.endswith((".sv", ".v", ".svh"))
            )
    ports = extract_ports(_read(rtl_path), design.module)
    return RewriteResult(
        ok=True,
        attempt=int(attempt),
        module=design.module,
        tree=tree,
        rtl_path=rtl_path,
        sources=sources,
        parameters=design.parameters,
        ports_ok=len(ports) == len(design.ports),
        reason="bypass identity rewrite (ports frozen)",
        rewrite_mode=rewrite_mode,
        elapsed_s=0.0,
        bypassed=True,
    )


def retain_provider(tag, data_path, *args, **kwargs) -> RetainResult:
    from .ppa_nodes import retain_snapshot

    return retain_snapshot(*args, **kwargs)


def reprove_provider(tag, data_path, *args, **kwargs) -> ReproveResult:
    retained: RetainResult = unwrap(args[2])
    design: PpaDesign = unwrap(args[0])
    kept = retained.kept
    ok = bool(retained.ok and kept > 0)
    return ReproveResult(
        ok=ok,
        module=design.module,
        snapshot_worthy=kept if ok else 0,
        total=kept,
        failed=0 if ok else 1,
        reason="all retained properties proved (bypass)" if ok else retained.reason,
        feedback="" if ok else retained.reason,
        prop_file=retained.prop_file,
        metrics={"synthetic": True},
        elapsed_s=0.0,
        bypassed=True,
    )


def score_provider(tag, data_path, *args, **kwargs) -> PpaScore:
    from .ppa_nodes import score_ppa

    return score_ppa(*args, **kwargs)


def lec_provider(tag, data_path, *args, **kwargs) -> LecResult:
    """Identity rewrite in the offline path is bit-equivalent."""
    return _lec_identity(tag, *args)


def _lec_identity(tag, *args) -> LecResult:
    design: PpaDesign = unwrap(args[0])
    rewrite: RewriteResult = unwrap(args[1])
    reprove: ReproveResult = unwrap(args[2])
    clean = bool(reprove.ok and rewrite.ok and rewrite.ports_ok)
    return LecResult(
        ok=True,
        equivalent=clean,
        inconclusive=False,
        snapshot_clean=clean,
        module=design.module,
        reason=(
            "bypass identity rewrite is LEC-equivalent"
            if clean
            else "skipped: rewrite is not snapshot-clean (bypass)"
        ),
        skipped=not clean,
        bypassed=True,
        workspace=os.path.join(design.workspace, f"lec_{rewrite.rewrite_mode or 'optimise'}"),
        elapsed_s=0.0,
    )


PROVIDERS = {
    "vcf_elaborate": vcf_elaborate_provider,
    "yosys_ppa": yosys_ppa_provider,
    "svapshot_generate_and_freeze": generate_provider,
    "llm_rtl_rewrite": rewrite_provider,
    "svapshot_reprove": reprove_provider,
    "yosys_lec": lec_provider,
}


def register(bypass) -> None:
    """Attach providers. The YAML decides which names are actually bypassed."""
    for name, provider in PROVIDERS.items():
        bypass.set_provider(name, provider)
