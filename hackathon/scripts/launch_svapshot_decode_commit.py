#!/usr/bin/env python3
"""Stage 0 SVApshot+VC Formal on DecodeUnit and the commit/exception bind.

Reuses already-emitted MegaBOOM Verilog. Does not start Ray (Gemini attach
still owns 8265) and does not re-elaborate Chipyard.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

# Scaffold writes to $SVAPSHOT_ROOT if set; the run root must win.
os.environ.pop("SVAPSHOT_ROOT", None)
os.environ.pop("VC_FORMAL_HOME", None)
os.environ.pop("VC_STATIC_HOME", None)

from nodes.emit_verilog import EmitResult
from nodes.snapshot import FrozenContract, generate_and_freeze


def _freeze(emit: EmitResult, module: str, target_yield: float) -> FrozenContract:
    print(
        f"  [stage] {module}: SVApshot main.py "
        f"({os.environ.get('SVAPSHOT_LLM_MODEL', 'gpt-5.6-luna')})",
        flush=True,
    )
    contract = generate_and_freeze(
        emit, module, target_yield=target_yield, formal_tool="vcformal"
    )
    print(
        f"  [snap] {module}: {contract.snapshot_worthy}/{contract.total} "
        f"worthy ({contract.yield_ratio:.0%}) accepted={contract.accepted} "
        f"— {contract.reason}",
        flush=True,
    )
    return contract


def main() -> int:
    wanted = [name for name in sys.argv[1:] if not name.startswith("-")]
    generated = os.path.join(HERE, "runs", "rvv_emit", "generated")
    bind_src = os.path.join(HERE, "fixtures", "boom_ports", "CommitExceptionPorts.sv")
    shutil.copy2(bind_src, os.path.join(generated, "CommitExceptionPorts.sv"))

    jobs = (
        ("DecodeUnit", os.path.join(HERE, "runs", "rvv_snap_decode")),
        ("CommitExceptionPorts", os.path.join(HERE, "runs", "rvv_snap_commit")),
    )
    if wanted:
        jobs = tuple(job for job in jobs if job[0] in wanted)
        if not jobs:
            print(f"no matching modules in {wanted}", file=sys.stderr)
            return 2
    started = time.time()
    results = []
    for module, workspace in jobs:
        os.makedirs(workspace, exist_ok=True)
        emit = EmitResult(
            workspace=workspace,
            source_root=generated,
            modules=[module],
            files=[f"{module}.sv"],
            from_chipyard=True,
            config="MegaBoomV3Config",
            notes=["reused runs/rvv_emit/generated; no re-elaborate"],
        )
        print(f"\n=== SVApshot {module} → {workspace} ===", flush=True)
        results.append(_freeze(emit, module, target_yield=0.5).__dict__)

    summary = {
        "elapsed_s": round(time.time() - started, 2),
        "contracts": results,
    }
    out = os.path.join(HERE, "runs", "rvv_snap", "launch_summary.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    print(f"\n== done in {summary['elapsed_s']}s ==\n   {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
