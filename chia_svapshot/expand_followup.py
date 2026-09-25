"""After a snapshot-clean expand, describe the new function and assert it.

The implementer writes a short report of what the rewrite added. That
report is the only new information the SVA model sees. New assertions are
kept only when their cone of influence covers a design signal the frozen
snapshot did not already observe — the added function did not exist when
the snapshot was generated, so a useful assertion must widen coverage.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, List

from chia_svapshot.ppa_rtl import extract_assertions


_ASSERT_SPLIT = re.compile(
    r"(?=^\s*\w+\s*:\s*assert\s+property)",
    re.M,
)


def _vertex(model: str, prompt: str) -> str:
    try:
        from vertex_llm import generate as vertex_generate
    except ImportError:
        root = os.environ.get("SVAPSHOT_ROOT") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        sys.path.insert(0, os.path.join(root, "src", "core"))
        from vertex_llm import generate as vertex_generate  # type: ignore
    return vertex_generate(model, prompt, max_output_tokens=8192)


def _new_assertions(text: str) -> List[str]:
    chunks = _ASSERT_SPLIT.split(text or "")
    return [chunk.strip() for chunk in chunks if "assert property" in chunk]


def _cone_metrics(assertions: List[str], rtl: str, module: str) -> Dict[str, object]:
    try:
        from coi import analyse_snapshot_cones
    except ImportError:
        root = os.environ.get("SVAPSHOT_ROOT") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        sys.path.insert(0, os.path.join(root, "src", "analysis"))
        from coi import analyse_snapshot_cones  # type: ignore
    if not assertions:
        return {"coi_coverage": 0.0, "union_cone_size": 0, "uncovered_signals": []}
    metrics = analyse_snapshot_cones(assertions, rtl, module)["metrics"]
    return {
        "coi_coverage": metrics["coi_coverage"],
        "union_cone_size": metrics["union_cone_size"],
        "design_signal_count": metrics["design_signal_count"],
        "uncovered_signals": metrics["uncovered_signals"],
    }


def expand_coverage_step(
    *,
    module: str,
    baseline_rtl: str,
    candidate_rtl: str,
    frozen_prop: str,
    workspace: str,
    model: str,
) -> Dict[str, object]:
    """Write the implementer report, generate assertions, keep coverage-widening ones."""
    os.makedirs(workspace, exist_ok=True)
    report_prompt = (
        f"You rewrote SystemVerilog module `{module}` in expand mode: "
        "every old behaviour stayed, and one new capability was added on the "
        "existing ports.\n\n"
        "Write a short report (plain prose, no code) that names:\n"
        "1. The new capability in one sentence.\n"
        "2. The ports and internal signals that implement it.\n"
        "3. The temporal condition under which the new behaviour is visible.\n"
        "Do not describe behaviour that already existed in the baseline.\n\n"
        "BASELINE:\n```systemverilog\n"
        f"{baseline_rtl[:12000]}\n```\n\nCANDIDATE:\n```systemverilog\n"
        f"{candidate_rtl[:12000]}\n```\n"
    )
    report = _vertex(model, report_prompt).strip()
    report_path = os.path.join(workspace, "expand_functionality.md")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(report)
        handle.write("\n")

    sva_prompt = (
        f"Write SystemVerilog assertions for module `{module}` that check "
        "ONLY the new capability in the report below. The frozen snapshot "
        "already covers the old function; do not restate it.\n"
        "Each assertion must be `name: assert property (...);` with a clock "
        "and disable iff that match the module's existing clock and reset.\n"
        "Return only the assertions.\n\n"
        f"REPORT:\n{report}\n\n"
        "CANDIDATE RTL (truncated):\n```systemverilog\n"
        f"{candidate_rtl[:12000]}\n```\n"
    )
    raw_sva = _vertex(model, sva_prompt)
    proposed = _new_assertions(raw_sva)
    frozen = extract_assertions(open(frozen_prop, encoding="utf-8", errors="replace").read())
    before = _cone_metrics(frozen, candidate_rtl, module)
    before_uncovered = set(before.get("uncovered_signals") or [])
    kept: List[str] = []
    for assertion in proposed:
        trial = _cone_metrics(frozen + kept + [assertion], candidate_rtl, module)
        newly = before_uncovered - set(trial.get("uncovered_signals") or [])
        if newly:
            kept.append(assertion)
            before_uncovered -= newly
    after = _cone_metrics(frozen + kept, candidate_rtl, module)
    prop_path = os.path.join(workspace, f"{module}_expand_prop.sv")
    with open(prop_path, "w", encoding="utf-8") as handle:
        handle.write(f"// Expand assertions for {module}. Source: {report_path}\n")
        for assertion in kept:
            handle.write(assertion.rstrip() + "\n\n")
    result = {
        "ok": bool(kept),
        "report_path": report_path,
        "prop_path": prop_path,
        "proposed": len(proposed),
        "kept": len(kept),
        "coi_before": before.get("coi_coverage"),
        "coi_after": after.get("coi_coverage"),
        "newly_covered": sorted(
            set(before.get("uncovered_signals") or [])
            - set(after.get("uncovered_signals") or [])
        ),
        "reason": (
            f"kept {len(kept)}/{len(proposed)} assertions that widened structural COI "
            f"({before.get('coi_coverage')} → {after.get('coi_coverage')})"
            if proposed
            else "SVA model returned no assert property"
        ),
    }
    with open(os.path.join(workspace, "expand_followup.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    return result
