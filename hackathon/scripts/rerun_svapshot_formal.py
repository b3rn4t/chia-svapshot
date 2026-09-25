#!/usr/bin/env python3
"""Re-run VC Formal on already-seeded DecodeUnit and CommitExceptionPorts."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

os.environ.pop("SVAPSHOT_ROOT", None)
os.environ.pop("VC_FORMAL_HOME", None)
os.environ.pop("VC_STATIC_HOME", None)

from chia_svapshot.state import DutSpec, Snapshot, StageResult, Verdict, unwrap
from chia_svapshot.svapshot_nodes import (
    collect_snapshot,
    score_snapshot,
    svapshot_formal_check,
)
from nodes.snapshot import FrozenContract, _svapshot_cfg


def _rerun(module: str, workspace: str, module_type: str, target_yield: float) -> FrozenContract:
    snap_ws = os.path.join(workspace, f"snap_{module}")
    dut = DutSpec(
        module=module,
        rtl_relpath=os.path.join("modules", f"{module}.sv"),
        workspace=snap_ws,
        sources=["modules"],
        includes="packages",
        module_type=module_type,
    )
    cfg = _svapshot_cfg("vcformal")
    cfg.timeout_s = int(os.environ.get("SVAPSHOT_TIMEOUT_S", "7200"))
    cfg.llm_model = os.environ.get("SVAPSHOT_LLM_MODEL", "gpt-4.1-mini")
    upstream = StageResult(
        stage="seed",
        module=module,
        ok=True,
        returncode=0,
        elapsed_s=0.0,
        workspace=snap_ws,
    )
    print(f"  [formal] {module} in {snap_ws}", flush=True)
    prove = unwrap(svapshot_formal_check(dut, cfg, snap_ws, upstream))
    print(
        f"  [formal] {module}: ok={prove.ok} rc={prove.returncode} "
        f"{prove.elapsed_s}s",
        flush=True,
    )
    if not prove.ok:
        print(prove.stdout_tail, flush=True)
        print(prove.stderr_tail, flush=True)
    snapshot: Snapshot = unwrap(collect_snapshot(dut, snap_ws, prove))
    verdict: Verdict = unwrap(score_snapshot(snapshot, target_yield, 0))
    frozen_dir = os.path.join(workspace, "contract", "frozen")
    os.makedirs(frozen_dir, exist_ok=True)
    prop_dest = os.path.join(frozen_dir, f"{module}_prop.sv")
    metrics_dest = os.path.join(frozen_dir, "snapshot_metrics.json")
    if snapshot.prop_file and os.path.isfile(snapshot.prop_file):
        shutil.copy2(snapshot.prop_file, prop_dest)
    with open(metrics_dest, "w") as handle:
        json.dump(snapshot.metrics or {"qualification": {}}, handle, indent=2)
        handle.write("\n")
    contract = FrozenContract(
        module=module,
        directory=frozen_dir,
        prop_file=prop_dest,
        metrics_file=metrics_dest,
        snapshot_worthy=snapshot.snapshot_worthy,
        total=snapshot.total,
        yield_ratio=snapshot.yield_ratio,
        accepted=verdict.accepted,
        reason=verdict.reason,
    )
    print(
        f"  [snap] {module}: {contract.snapshot_worthy}/{contract.total} "
        f"worthy ({contract.yield_ratio:.0%}) accepted={contract.accepted} "
        f"— {contract.reason}",
        flush=True,
    )
    return contract


def main() -> int:
    started = time.time()
    jobs = (
        ("DecodeUnit", os.path.join(HERE, "runs", "rvv_snap_decode"), "combinational"),
        (
            "CommitExceptionPorts",
            os.path.join(HERE, "runs", "rvv_snap_commit"),
            "sequential",
        ),
    )
    results = []
    for module, workspace, module_type in jobs:
        print(f"\n=== formal {module} ===", flush=True)
        results.append(_rerun(module, workspace, module_type, 0.5).__dict__)
    out = os.path.join(HERE, "runs", "rvv_snap", "formal_summary.json")
    with open(out, "w") as handle:
        json.dump(
            {"elapsed_s": round(time.time() - started, 2), "contracts": results},
            handle,
            indent=2,
        )
        handle.write("\n")
    print(f"\n== formal done ==\n   {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
