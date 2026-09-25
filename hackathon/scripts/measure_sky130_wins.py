#!/usr/bin/env python3
"""Sky130 HD Yosys PPA for packaged original vs optimised RTL."""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HACK = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(HACK, "..", ".."))
sys.path.insert(0, HACK)

os.environ.pop("SVAPSHOT_YOSYS_GENERIC", None)

from nodes.ppa_yosys import better, measure_ppa  # noqa: E402

CASES = {
    "div_unit": {
        "top": "div_unit",
        "order": [
            "fpnew_pkg.sv",
            "riscv_pkg.sv",
            "drac_pkg.sv",
            "div_4bits.sv",
            "div_unit.sv",
        ],
    },
    "ptw": {
        "top": "ptw",
        "order": ["riscv_pkg.sv", "mmu_pkg.sv", "pseudoLRU.sv", "ptw_arb.sv", "ptw.sv"],
    },
    "tlb": {
        "top": "tlb",
        "order": ["mmu_pkg.sv", "pseudoLRU.sv", "tlb.sv"],
    },
}


def _sources(module: str, kind: str) -> list[str]:
    root = os.path.join(REPO, "date2027", "optimise", "cases", module, f"{kind}_rtl")
    out = []
    for name in CASES[module]["order"]:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            out.append(path)
    return out


def main() -> int:
    out_dir = os.path.join(REPO, "chia_svapshot", "runs", "ppa_sky130_packaged")
    os.makedirs(out_dir, exist_ok=True)
    report = {"pdk": "sky130_fd_sc_hd tt_025C_1v80", "cases": {}}
    for module, spec in CASES.items():
        orig = measure_ppa(
            _sources(module, "original"),
            spec["top"],
            workdir=os.path.join(out_dir, module, "original"),
        )
        opt = measure_ppa(
            _sources(module, "optimised"),
            spec["top"],
            workdir=os.path.join(out_dir, module, "optimised"),
        )
        decision = better(orig, opt, goal="area_or_delay", max_regression=0.05)
        report["cases"][module] = {
            "original": orig,
            "optimised": opt,
            "score": decision,
        }
        print(
            f"{module}: orig cells={orig.get('cells')} area={orig.get('area_um2')} "
            f"ltp={orig.get('levels')} | opt cells={opt.get('cells')} "
            f"area={opt.get('area_um2')} ltp={opt.get('levels')} win={decision['win']}",
            flush=True,
        )
    path = os.path.join(out_dir, "SKY130_PACKAGED.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(f"wrote {path}", flush=True)
    return 0 if all(row["score"]["win"] for row in report["cases"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
