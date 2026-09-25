#!/usr/bin/env python3
"""A minimal CHIA loop that drives SVApshot over a Sargantana RTL module.

The loop is the CHIA unit of orchestration: a plain Python script whose function
calls form a task graph. Reading top to bottom, it does this once per iteration:

    stage_rtl_dut ─▶ prepare_run_root ─▶ svapshot_scaffold ─▶ svapshot_seed
                                                                    │
                                                                    ▼
        score_snapshot ◀── collect_snapshot ◀────────────── svapshot_prove
               │
               ├─ accepted ──▶ done
               └─ rejected ──▶ agent (via SVApshotTool over MCP) ──▶ next iteration

The first six arrows are *programmatic* edges: the loop dispatches each node
with ``chia_remote(...)`` and passes the resulting ``ObjectRef`` into the next
node, which is how ordering and data flow are expressed to Ray. The last arrow
is an *agentic* edge: the loop hands an LLM a tool server and a prompt, and the
model decides what to call.

Run it::

    ./run_local.sh                       # local Ray, offline replay via bypass
    ./run_local.sh --real                # real scaffolding + VC Formal + LLM
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional

import ray
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chia_svapshot import offline
from chia_svapshot.dut_prep import stage_rtl_dut
from chia_svapshot.state import Snapshot, SvapshotConfig, Verdict, unwrap
from chia_svapshot.svapshot_nodes import (
    collect_snapshot,
    patch_package_closure,
    prepare_run_root,
    score_snapshot,
    svapshot_formal_check,
    svapshot_prove,
    svapshot_scaffold,
    svapshot_seed,
)
from chia_svapshot.svapshot_tool import SVApshotTool

REPAIR_PROMPT = """\
You are improving a formal-verification snapshot for the SystemVerilog module \
`{module}`.

A snapshot is a regression contract: only properties that the formal tool proves
*non-vacuously* count. The last SVApshot run reported:

{feedback}

Use the `{tool}_read_rtl` tool to read the design and `{tool}_read_properties` to
see what was generated. `{tool}_formal_log_tail` shows why properties failed.
When you have a better set of assertions, submit them with
`{tool}_propose_properties`.

Write assertions that are specific to this module's actual behaviour. Avoid
tautologies: a property that cannot fail proves nothing and will be rejected as
vacuous.
"""


def build_llm(name: Optional[str]):
    """Instantiate one of CHIA's LLM backends by short name, or None."""
    if not name:
        return None
    if name == "copilot":
        from chia.models.copilot import CopilotLLM

        return CopilotLLM()
    if name == "claude":
        from chia.models.claude import ClaudeCodeLLM

        return ClaudeCodeLLM(model="claude-sonnet-4-6", resume_session=True)
    if name == "opencode":
        from chia.models.opencode import OpenCodeLLM

        return OpenCodeLLM("opencode/big-pickle")
    if name.startswith("nvidia:"):
        from chia.models.openai_providers import NvidiaLLM

        return NvidiaLLM(model=name.split(":", 1)[1])
    raise SystemExit(f"unknown --llm backend: {name}")


def why(stage) -> str:
    """One line explaining a stage failure, from the most specific source available.

    The stage driver reports an exception or a per-sub-step breakdown in its JSON
    payload; that is far more useful than the tail of a stdout stream that is
    mostly SVApshot's own progress banners.
    """
    detail = stage.detail or {}
    if detail.get("error"):
        return str(detail["error"])[:200]
    steps = detail.get("steps") or {}
    failed = [name for name, ok in steps.items() if not ok]
    if failed:
        reason = steps.get("reason") or ""
        return f"failed sub-step(s): {', '.join(failed)} {reason}".strip()[:200]
    for text in (stage.stderr_tail, stage.stdout_tail):
        if text and text.strip():
            return text.strip().splitlines()[-1][:200]
    return f"returncode {stage.returncode}"


def describe(snapshot: Snapshot, verdict: Verdict) -> str:
    return (
        f"{snapshot.snapshot_worthy}/{snapshot.total} snapshot-worthy "
        f"({verdict.yield_ratio:.0%}) | proved={snapshot.proved} "
        f"vacuous={snapshot.vacuous} failing={snapshot.failed} "
        f"inconclusive={snapshot.inconclusive}"
    )


def main() -> int:
    import os

    root = os.environ.get("SVAPSHOT_ROOT") or "/aisva"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", default="div_4bits", help="Sargantana module to snapshot")
    parser.add_argument(
        "--source-root",
        default=os.path.join(root, "benchmarks", "sargantana"),
        help="DUT tree (default: $SVAPSHOT_ROOT/benchmarks/sargantana)",
    )
    parser.add_argument(
        "--checkout",
        default=root,
        help="SVApshot checkout (default: $SVAPSHOT_ROOT or /aisva)",
    )
    parser.add_argument(
        "--workspace",
        default=os.path.join(root, "chia_svapshot", "runs", "div_4bits"),
    )
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--target-yield", type=float, default=0.75)
    parser.add_argument("--formal-tool", default="vcformal", choices=["vcformal", "jaspergold"])
    parser.add_argument("--svapshot-python", default="python3")
    parser.add_argument("--svapshot-model", default="meta/llama-3.3-70b-instruct")
    parser.add_argument("--execution", default="validation", choices=["golden", "validation"])
    parser.add_argument("--assertion-source", default="rtl", choices=["rtl", "file", "empty"])
    parser.add_argument(
        "--seed-file",
        default=None,
        help="SVA to seed the first iteration with (used when --assertion-source empty)",
    )
    parser.add_argument(
        "--prove-mode",
        default="agent",
        choices=["agent", "formal"],
        help="'agent' runs SVApshot's LLM repair loop; 'formal' only proves the "
        "properties already in the file",
    )
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--llm", default=None, help="CHIA agent backend for the repair edge")
    parser.add_argument("--bypass-config", default=None, help="YAML enabling offline replay")
    parser.add_argument("--ray-address", default=None, help="'auto' to join a chia cluster")
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--summary-json", default=None)
    args = parser.parse_args()

    # ------------------------------------------------------------------ setup
    if args.ray_address:
        ray.init(address=args.ray_address)
    else:
        # A single-machine stand-in for a cluster: the same logical worker
        # labels the cluster.yaml advertises, just all on this box. `formal: 1`
        # is deliberately a single unit — it models one VC Formal licence, so
        # Ray serialises prove stages even if the loop fans out.
        ray.init(
            resources={"svapshot_cpu": 4, "llm": 2, "formal": 1},
            ignore_reinit_error=True,
            log_to_driver=False,
        )

    if args.profile_dir:
        from chia.trace.profiler import start_collector

        os.makedirs(args.profile_dir, exist_ok=True)
        start_collector(log_dir=args.profile_dir)

    # Providers are always registered; the YAML decides which nodes they serve.
    bypass = Bypass(yaml_path=args.bypass_config)
    offline.register(bypass)

    cfg = SvapshotConfig(
        svapshot_root=args.checkout,
        interpreter=args.svapshot_python,
        llm_model=args.svapshot_model,
        execution=args.execution,
        verbosity="low",
        formal_tool=args.formal_tool,
        assertion_source=args.assertion_source,
        timeout_s=args.timeout,
    )

    print(f"== SVApshot × CHIA :: {args.module} ==")
    print(f"   workspace   {args.workspace}")
    print(f"   bypass      {args.bypass_config or 'disabled (everything runs for real)'}")
    print(f"   agent       {args.llm or 'disabled (no repair edge)'}")
    print()

    started = time.time()

    # --------------------------------------------------------- staging nodes
    # Both dispatches return immediately; the refs are the graph's edges.
    dut_ref = stage_rtl_dut.chia_remote(args.module, args.source_root, args.workspace)
    run_root_ref = prepare_run_root.chia_remote(dut_ref, cfg)

    dut = get(dut_ref)
    run_root = get(run_root_ref)
    print(
        f"[stage] {dut.module}: {dut.line_count} lines, {dut.module_type}, "
        f"packages={dut.packages or 'none'}"
    )
    print(f"[stage] origin {dut.origin}")

    llm = build_llm(args.llm)
    history: List[dict] = []
    seed_text = ""
    if args.seed_file:
        with open(args.seed_file) as handle:
            seed_text = handle.read()
        print(f"[stage] seeding from {args.seed_file}")
    verdict: Optional[Verdict] = None
    snapshot: Optional[Snapshot] = None

    # ------------------------------------------------------------- the loop
    for iteration in range(args.iterations):
        tag = f"iter{iteration}"
        print(f"\n--- iteration {iteration} ---")

        # Each node receives the previous stage's ObjectRef. Ray blocks the
        # downstream task until that ref resolves, so this chain *is* the
        # dependency graph; no explicit sequencing is needed.
        scaffold_ref = svapshot_scaffold.chia_remote(
            dut, cfg, run_root, _chia_tag=tag
        )
        patch_ref = patch_package_closure.chia_remote(dut, run_root, scaffold_ref)
        seed_ref = svapshot_seed.chia_remote(
            dut, cfg, run_root, patch_ref, seed_text=seed_text, _chia_tag=tag
        )
        # Same resource, same position in the graph — only the work differs.
        prove_node = (
            svapshot_prove if args.prove_mode == "agent" else svapshot_formal_check
        )
        prove_ref = prove_node.chia_remote(dut, cfg, run_root, seed_ref, _chia_tag=tag)
        snapshot_ref = collect_snapshot.chia_remote(dut, run_root, prove_ref)
        verdict_ref = score_snapshot.chia_remote(
            snapshot_ref, args.target_yield, iteration
        )

        # First blocking call of the iteration: everything above ran as a graph.
        # get() unwraps the profiler's result wrapper for a single ref but not
        # for a list of them, so the unwrap is applied explicitly here.
        scaffold, patch, seed, prove, snapshot, verdict = (
            unwrap(value)
            for value in get(
                [scaffold_ref, patch_ref, seed_ref, prove_ref, snapshot_ref, verdict_ref]
            )
        )

        for stage in (scaffold, patch, seed, prove):
            mark = "ok " if stage.ok else "FAIL"
            note = " (bypassed)" if stage.bypassed else ""
            print(f"  [{mark}] {stage.stage:<9} {stage.elapsed_s:>6.1f}s{note}")
            if not stage.ok:
                print(f"         {why(stage)}")

        print(f"  [snap] {describe(snapshot, verdict)}")
        print(f"  [vrdct] {verdict.reason}")

        history.append(
            {
                "iteration": iteration,
                "stages": {
                    s.stage: {
                        "ok": s.ok,
                        "elapsed_s": s.elapsed_s,
                        "bypassed": s.bypassed,
                        "returncode": s.returncode,
                    }
                    for s in (scaffold, patch, seed, prove)
                },
                "total": snapshot.total,
                "snapshot_worthy": snapshot.snapshot_worthy,
                "yield": round(verdict.yield_ratio, 4),
                "accepted": verdict.accepted,
                "reason": verdict.reason,
            }
        )

        if verdict.accepted:
            break
        if iteration == args.iterations - 1:
            print("  [loop] iteration budget exhausted")
            break

        # ------------------------------------------------------ agentic edge
        if llm is None:
            print("  [loop] no agent configured; re-running with the same seed")
            continue

        tool = SVApshotTool(
            f"svapshot_{dut.module}",
            dut,
            cfg,
            run_root,
            task_options={"resources": {"svapshot_cpu": 1}},
        )
        try:
            prompt = REPAIR_PROMPT.format(
                module=dut.module, feedback=verdict.feedback, tool=tool.name
            )
            response = get(llm.prompt.chia_remote(llm, prompt, tools=[tool]))
            print(f"  [agent] {response.result.strip()[:400]}")
            history[-1]["agent_response"] = response.result[:2000]
        finally:
            tool.stop()

    # ------------------------------------------------------------- reporting
    elapsed = time.time() - started
    print(f"\n== done in {elapsed:.1f}s ==")
    if snapshot is not None and verdict is not None:
        print(f"   {describe(snapshot, verdict)}")
        print(f"   accepted: {verdict.accepted}")
        if snapshot.prop_file:
            print(f"   contract: {snapshot.prop_file}")
        for note in snapshot.notes:
            print(f"   note: {note}")

    summary = {
        "module": args.module,
        "workspace": args.workspace,
        "origin": dut.origin,
        "module_type": dut.module_type,
        "packages": dut.packages,
        "elapsed_s": round(elapsed, 2),
        "bypass_config": args.bypass_config,
        "agent": args.llm,
        "target_yield": args.target_yield,
        "accepted": bool(verdict and verdict.accepted),
        "iterations": history,
    }
    out = args.summary_json or os.path.join(args.workspace, "loop_summary.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as handle:
        json.dump(summary, handle, indent=2)
    print(f"   summary:  {out}")

    ray.shutdown()
    return 0 if (verdict and verdict.accepted) else 1


if __name__ == "__main__":
    sys.exit(main())
