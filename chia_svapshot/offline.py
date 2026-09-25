"""Bypass providers that stand in for the licence- and credit-hungry stages.

CHIA's bypass mechanism replaces a node's *computation* while still dispatching
the call through Ray, so scheduling, placement, retries and profiling are all
exercised for real. That is exactly what you want when developing the loop
itself: ``svapshot_prove`` holds a VC Formal licence and takes minutes, and
``svapshot_seed`` spends LLM credits, but neither is interesting while you are
debugging the graph.

Everything produced here is marked ``bypassed=True`` and the synthetic metrics
carry ``"synthetic": true``, so a replayed run can never be mistaken for a real
proof.
"""

from __future__ import annotations

import json
import os
import random
from typing import Any, Dict

from .state import DutSpec, StageResult, unwrap

SYNTHETIC_SEED_SVA = """\
// Synthetic seed properties produced by the CHIA bypass provider.
p_divisor_passthrough: assert property (divisor_o == divisor_i);
p_remainder_bounded:   assert property (divisor_i != 0 |-> remanent_o < divisor_i);
p_no_x_on_outputs:     assert property (!$isunknown({remanent_o, dividend_quotient_o}));
"""


def _sva_dir(dut: DutSpec, run_root: str) -> str:
    path = os.path.join(run_root, f"ft_{dut.module}", "sva")
    os.makedirs(path, exist_ok=True)
    return path


def seed_provider(tag, data_path, *args, **kwargs) -> StageResult:
    """Stand in for the initial assertion-generation stage.

    Signature is fixed by CHIA: ``(tag, data_path, *original_call_args)``.
    """
    dut: DutSpec = unwrap(args[0])
    run_root: str = unwrap(args[2])
    sva_dir = _sva_dir(dut, run_root)

    seed_text = kwargs.get("seed_text") or SYNTHETIC_SEED_SVA
    with open(os.path.join(sva_dir, "base"), "w") as handle:
        handle.write(seed_text)

    return StageResult(
        stage="seed",
        module=dut.module,
        ok=True,
        returncode=0,
        elapsed_s=0.0,
        workspace=run_root,
        stdout_tail=f"[bypass] served synthetic seed assertions for tag {tag!r}",
        artifacts=sorted(os.listdir(sva_dir)),
        detail={"synthetic": True, "properties": seed_text.count("assert property")},
        bypassed=True,
    )


def prove_provider(tag, data_path, *args, **kwargs) -> StageResult:
    """Stand in for the agent + formal-tool stage.

    Writes a ``snapshot_metrics.json`` shaped like the real one so the
    downstream ``collect_snapshot`` and ``score_snapshot`` nodes exercise their
    real parsing and decision code. The yield improves with each iteration so
    the loop's stopping condition is actually reachable in a replay.
    """
    dut: DutSpec = unwrap(args[0])
    cfg = unwrap(args[1])
    run_root: str = unwrap(args[2])
    sva_dir = _sva_dir(dut, run_root)

    iteration = 0
    if tag and "iter" in str(tag):
        try:
            iteration = int(str(tag).split("iter")[1].split("_")[0])
        except (IndexError, ValueError):
            iteration = 0

    rng = random.Random(f"{dut.module}:{iteration}")
    total = 8 + iteration
    # A first pass typically proves only a minority of what it generates; each
    # repair round is modelled as recovering part of the gap.
    proved = min(total, int(total * (0.375 + 0.22 * iteration)) + rng.randint(0, 1))
    vacuous = max(0, (total - proved) // 3)
    failing = max(0, total - proved - vacuous - 1)
    inconclusive = max(0, total - proved - vacuous - failing)

    prop_path = os.path.join(sva_dir, f"{dut.module}_prop.sv")
    if not os.path.isfile(prop_path):
        lines = [f"module {dut.module}_prop;"]
        lines += [
            f"  p_synthetic_{i}: assert property (1'b1); // bypass placeholder"
            for i in range(total)
        ]
        lines.append("endmodule")
        with open(prop_path, "w") as handle:
            handle.write("\n".join(lines) + "\n")

    metrics: Dict[str, Any] = {
        "synthetic": True,
        "module": dut.module,
        "method": "svapshot",
        "model": getattr(cfg, "llm_model", ""),
        "formal_tool": getattr(cfg, "formal_tool", ""),
        "qualification": {
            "total_properties": total,
            "proved_non_vacuous": proved,
            "proved_vacuous": vacuous,
            "failing_property_mismatch": failing,
            "failing_missing_assumption": 0,
            "inconclusive": inconclusive,
            "snapshot_yield": round(proved / total, 4) if total else 0.0,
        },
    }
    with open(os.path.join(sva_dir, "snapshot_metrics.json"), "w") as handle:
        json.dump(metrics, handle, indent=2)

    return StageResult(
        stage="prove",
        module=dut.module,
        ok=True,
        returncode=0,
        elapsed_s=0.0,
        workspace=run_root,
        stdout_tail=(
            f"[bypass] served synthetic proof results for tag {tag!r}: "
            f"{proved}/{total} snapshot-worthy"
        ),
        artifacts=sorted(os.listdir(sva_dir)),
        detail=metrics,
        bypassed=True,
    )


def scaffold_provider(tag, data_path, *args, **kwargs) -> StageResult:
    """Stand in for harness scaffolding when even that cannot run."""
    dut: DutSpec = unwrap(args[0])
    run_root: str = unwrap(args[2])
    _sva_dir(dut, run_root)
    return StageResult(
        stage="scaffold",
        module=dut.module,
        ok=True,
        returncode=0,
        elapsed_s=0.0,
        workspace=run_root,
        stdout_tail=f"[bypass] skipped the scaffolder for tag {tag!r}",
        detail={"synthetic": True},
        bypassed=True,
    )


PROVIDERS = {
    "svapshot_scaffold": scaffold_provider,
    "svapshot_seed": seed_provider,
    "svapshot_prove": prove_provider,
}


def register(bypass) -> None:
    """Attach every provider to a :class:`chia.base.bypass.Bypass` instance.

    Registering a provider does not enable the bypass; the YAML config decides
    which functions are actually bypassed, so an unlisted node still runs for
    real even though a provider exists for it.
    """
    for name, provider in PROVIDERS.items():
        bypass.set_provider(name, provider)
