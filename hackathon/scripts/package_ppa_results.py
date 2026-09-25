#!/usr/bin/env python3
"""Build design_mode_win|loss directories for the CHIA PPA hackathon."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
SAFE = RUNS / "safe"


def _copy_tree_sv(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".sv", ".svh", ".v"}:
            shutil.copy2(path, dest / path.name)


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


YOSYS_SH = r'''#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

rtl="${1:?usage: $0 original|new}"
if [[ "$rtl" == original ]]; then
  rtl_dir="$ROOT/original_rtl"
elif [[ "$rtl" == new ]]; then
  rtl_dir="$ROOT/llm_rtl"
else
  echo "usage: $0 original|new" >&2
  exit 2
fi

yosys_bin="${YOSYS:-}"
if [[ -z "$yosys_bin" ]]; then
  for cand in \
    "$ROOT/../../toolchains/oss-cad-suite/bin/yosys" \
    "$HOME/ai-sva/chia_svapshot/hackathon/toolchains/oss-cad-suite/bin/yosys" \
    "$(command -v yosys || true)"
  do
    if [[ -n "$cand" && -x "$cand" ]]; then
      yosys_bin="$cand"
      break
    fi
  done
fi
if [[ -z "${yosys_bin:-}" ]]; then
  echo "error: yosys not found; set YOSYS=..." >&2
  exit 1
fi

mapfile -t sources < <(python3 - <<PY
from pathlib import Path
rtl = Path("$rtl_dir")
order = __ORDER__
found = []
for name in order:
    p = rtl / name
    if p.is_file():
        found.append(str(p))
print("\n".join(found))
PY
)

quoted=""
for s in "${sources[@]}"; do
  quoted+=" $(printf %q "$s")"
done

out="yosys_${rtl}.log"
# shellcheck disable=SC2086
"$yosys_bin" -Q -T -p "plugin -i slang; read_slang --single-unit --top __TOP__ $quoted; proc; opt; synth -top __TOP__; stat; ltp -noff" | tee "$out"
'''

VCF_SH = r'''#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
rtl="${1:?usage: $0 original|new}"
if [[ "$rtl" != original && "$rtl" != new ]]; then
  echo "usage: $0 original|new" >&2
  exit 2
fi

work="$ROOT/formal_${rtl}"
rm -rf "$work"
mkdir -p "$work/ft___TOP__/sva" "$work/vcf_projs/__TOP__"

if [[ "$rtl" == original ]]; then
  rtl_dir="$ROOT/original_rtl"
  prop="$ROOT/sva/original/__TOP___prop.sv"
else
  rtl_dir="$ROOT/llm_rtl"
  prop="$ROOT/sva/new/__TOP___prop.sv"
fi

cp -a "$rtl_dir"/. "$work/"
cp "$prop" "$work/ft___TOP__/sva/__TOP___prop.sv"
cp "$ROOT/bind/__TOP___bind.svh" "$work/ft___TOP__/sva/__TOP___bind.svh"

python3 - <<PY
from pathlib import Path
work = Path("$work")
names = __ORDER__
abs_files = []
for name in names:
    p = work / name
    if p.is_file():
        abs_files.append(str(p.resolve()))
prop = (work / "ft___TOP__/sva/__TOP___prop.sv").resolve()
bind = (work / "ft___TOP__/sva/__TOP___bind.svh").resolve()
vc = work / "ft___TOP__/files_vcf.vc"
lines = [
    "+verilog2001ext+.v",
    "+verilog2001ext+.vh",
    "+systemverilogext+.sv",
    "+systemverilogext+.svh",
    "+libext+.v",
    "+libext+.sv",
    "+librescan",
    f"+incdir+{work}",
    f"-y {work}",
]
lines.extend(abs_files)
lines.append(str(prop))
lines.append(str(bind))
vc.write_text("\n".join(lines) + "\n")
tcl = work / "ft___TOP__/FPV_vcf.tcl"
tcl.write_text("""# Replay FPV for __TOP__
set top __TOP__
set_fml_appmode FPV
set files_vcf [file join [file dirname [info script]] files_vcf.vc]
suppress_message SM_UST
analyze -format sverilog -vcs "-f $files_vcf"
elaborate -sva $top
set_fml_var fml_vacuity_on true
set_fml_var fml_witness_on true
create_clock __CLK__ -period 100
set_change_at -default -posedge -clock __CLK__
create_reset __RST__ -sense low
set report_dir "vcf_projs/__TOP__/reports"
file mkdir $report_dir
set command_time_budget "1H"
if {[info exists env(SVAPSHOT_FML_MAX_TIME)]} { set command_time_budget $env(SVAPSHOT_FML_MAX_TIME) }
set property_time_budget "5M"
if {[info exists env(SVAPSHOT_FML_PROPERTY_TIME)]} { set property_time_budget $env(SVAPSHOT_FML_PROPERTY_TIME) }
catch {set_fml_var fml_max_time $command_time_budget}
catch {set_fml_var fml_property_time_limit $property_time_budget}
if {[catch {check_fv -block} check_fv_error]} {
    echo "CHECK_FV_FAILED: $check_fv_error"
}
catch {redirect -file $report_dir/properties.rpt {report_fv -verbose}}
""")
print(f"wrote {vc} and {tcl}")
PY

VCF_HOME="${VC_FORMAL_HOME:-${VC_STATIC_HOME:-}}"
CONTAINER="${SVAPSHOT_VCF_DOCKER:-vcformal-dev}"
LOG="$work/vcf_projs/__TOP__/vcf.log"
mkdir -p "$(dirname "$LOG")"

run_vcf() {
  if [[ -f /.dockerenv ]]; then
    export TERM="${TERM:-vt100}"
    export VC_FORMAL_HOME="$VCF_HOME"
    export VC_STATIC_HOME="${VC_STATIC_HOME:-$VCF_HOME}"
    export PATH="$VCF_HOME/bin:$PATH"
    (cd "$work" && vcf -session "vcf_projs/__TOP__/vcst_rtdb" -no_restore -f "ft___TOP__/FPV_vcf.tcl" -batch)
    return
  fi
  if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -qx true; then
    echo "error: $CONTAINER is not running" >&2
    exit 1
  fi
  docker exec \
    -e TERM=vt100 \
    -e TERMINFO=/usr/share/terminfo \
    -e VC_FORMAL_HOME="$VCF_HOME" \
    -e VC_STATIC_HOME="$VCF_HOME" \
    -e HOME="${HOME}" \
    -e USER="${USER}" \
    -w "$work" \
    "$CONTAINER" \
    "$VCF_HOME/bin/vcf" \
    -session "vcf_projs/__TOP__/vcst_rtdb" \
    -no_restore \
    -f "ft___TOP__/FPV_vcf.tcl" \
    -batch
}

run_vcf < /dev/null > "$LOG" 2>&1 || true
echo "VC Formal log: $LOG"
'''


def _write_scripts(dest: Path, top: str, clk: str, rst: str, order: list[str]) -> None:
    scripts = dest / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    order_lit = json.dumps(order)
    yosys = (
        YOSYS_SH.replace("__TOP__", top)
        .replace("__ORDER__", order_lit)
    )
    vcf = (
        VCF_SH.replace("__TOP__", top)
        .replace("__CLK__", clk)
        .replace("__RST__", rst)
        .replace("__ORDER__", order_lit)
    )
    (scripts / "run_yosys.sh").write_text(yosys)
    (scripts / "run_vcf.sh").write_text(vcf)
    for name, kind in (
        ("run_yosys_original.sh", "original"),
        ("run_yosys_new.sh", "new"),
        ("run_vcf_original.sh", "original"),
        ("run_vcf_new.sh", "new"),
    ):
        tool = "run_yosys.sh" if "yosys" in name else "run_vcf.sh"
        (scripts / name).write_text(
            "#!/usr/bin/env bash\n"
            f'exec "$(dirname "$0")/{tool}" {kind} "$@"\n'
        )
    for path in scripts.iterdir():
        path.chmod(0o755)


def package(
    dest: Path,
    *,
    top: str,
    mode: str,
    verdict: str,
    baseline_rtl: Path,
    llm_rtl: Path,
    bind: Path,
    sva_original: Path,
    sva_new: Path,
    order: list[str],
    result: dict,
    clk: str = "clk_i",
    rst: str = "rstn_i",
) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    _copy_tree_sv(baseline_rtl, dest / "original_rtl")
    _copy_tree_sv(llm_rtl, dest / "llm_rtl")
    _copy_file(bind, dest / "bind" / bind.name)
    _copy_file(sva_original, dest / "sva" / "original" / sva_original.name)
    _copy_file(sva_new, dest / "sva" / "new" / sva_new.name)
    _write_scripts(dest, top, clk, rst, order)
    payload = {
        "directory": dest.name,
        "module": top,
        "execution_type": mode,
        "verdict": verdict,
        **result,
    }
    (dest / "RESULT.json").write_text(json.dumps(payload, indent=2) + "\n")


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "results"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    div_order = [
        "fpnew_pkg.sv",
        "riscv_pkg.sv",
        "drac_pkg.sv",
        "div_4bits.sv",
        "div_unit.sv",
    ]
    ptw_order = [
        "riscv_pkg.sv",
        "mmu_pkg.sv",
        "pseudoLRU.sv",
        "ptw_arb.sv",
        "ptw.sv",
    ]

    cases = [
        {
            "name": "div_unit_optimise_win",
            "top": "div_unit",
            "mode": "optimise",
            "verdict": "win",
            "baseline": RUNS / "ppa_div_unit_opt" / "baseline_rtl",
            "llm": RUNS / "ppa_div_unit_opt" / "candidate_rtl_optimise_1",
            "bind": RUNS / "ppa_div_unit_opt" / "reprove_1" / "reprove_div_unit" / "ft_div_unit" / "sva" / "div_unit_bind.svh",
            "sva_orig": RUNS / "ppa_div_unit_opt" / "contract" / "frozen" / "div_unit_prop.sv",
            "sva_new": RUNS / "ppa_div_unit_opt" / "contract" / "retained" / "optimise" / "div_unit_prop.sv",
            "order": div_order,
            "result": {
                "outcome": "ppa_win_snapshot_pass",
                "headline": "better PPA and passing snapshot",
                "model": "gemini-3.1-pro-preview",
                "baseline_ppa": {"cells": 9257, "wires": 7357, "levels": 243},
                "candidate_ppa": {"cells": 6531, "wires": 5400, "levels": 243},
                "snapshot": "30/30 retained properties proved",
                "workspace": "chia_svapshot/runs/ppa_div_unit_opt",
            },
        },
        {
            "name": "div_unit_expand_loss",
            "top": "div_unit",
            "mode": "expand",
            "verdict": "loss",
            "baseline": RUNS / "ppa_div_unit_opt" / "baseline_rtl",
            "llm": RUNS / "ppa_div_unit_opt" / "candidate_rtl_expand_0",
            "bind": RUNS / "ppa_div_unit_opt" / "reprove_0" / "reprove_div_unit" / "ft_div_unit" / "sva" / "div_unit_bind.svh",
            "sva_orig": RUNS / "ppa_div_unit_opt" / "contract" / "frozen" / "div_unit_prop.sv",
            "sva_new": RUNS / "ppa_div_unit_opt" / "contract" / "retained" / "expand" / "div_unit_prop.sv",
            "order": div_order,
            "result": {
                "outcome": "snapshot_pass_no_ppa",
                "headline": "",
                "model": "gemini-3.1-pro-preview",
                "baseline_ppa": {"cells": 9257, "wires": 7357, "levels": 243},
                "candidate_ppa": {"cells": 10307, "wires": 8409, "levels": 245},
                "snapshot": "30/30 retained properties proved",
                "workspace": "chia_svapshot/runs/ppa_div_unit_opt",
            },
        },
        {
            "name": "div_unit_trim_win",
            "top": "div_unit",
            "mode": "trim",
            "verdict": "win",
            "baseline": RUNS / "ppa_div_unit_opt" / "baseline_rtl",
            "llm": SAFE / "div_unit_trim_ppa_win" / "candidate_rtl_trim_0",
            "bind": RUNS / "ppa_sargantana" / "reprove_0" / "reprove_div_unit" / "ft_div_unit" / "sva" / "div_unit_bind.svh",
            "sva_orig": SAFE / "div_unit_trim_ppa_win" / "contract" / "frozen" / "div_unit_prop.sv",
            "sva_new": SAFE / "div_unit_trim_ppa_win" / "contract" / "retained" / "trim" / "div_unit_prop.sv",
            "order": div_order,
            "result": {
                "outcome": "ppa_win_snapshot_pass",
                "headline": "better PPA and passing snapshot",
                "model": "gemini-2.5-pro",
                "baseline_ppa": {"cells": 9257, "wires": 7357, "levels": 243},
                "candidate_ppa": {"cells": 6556, "wires": 4786, "levels": 242},
                "snapshot": "13/13 retained properties proved (17 dropped)",
                "workspace": "chia_svapshot/runs/ppa_sargantana",
            },
        },
        {
            "name": "ptw_optimise_loss",
            "top": "ptw",
            "mode": "optimise",
            "verdict": "loss",
            "baseline": RUNS / "ppa_ptw_opt" / "baseline_rtl",
            "llm": RUNS / "ppa_ptw_opt" / "candidate_rtl_optimise_2",
            "bind": RUNS / "ppa_ptw_opt" / "reprove_2" / "reprove_ptw" / "ft_ptw" / "sva" / "ptw_bind.svh",
            "sva_orig": RUNS / "ppa_ptw_opt" / "contract" / "frozen" / "ptw_prop.sv",
            "sva_new": RUNS / "ppa_ptw_opt" / "contract" / "retained" / "optimise" / "ptw_prop.sv",
            "order": ptw_order,
            "result": {
                "outcome": "snapshot_pass_no_ppa",
                "headline": "",
                "model": "gemini-3.1-pro-preview",
                "baseline_ppa": {"cells": 2813, "levels": 22},
                "candidate_ppa": {"cells": 2858, "levels": 22},
                "snapshot": "43/43 retained properties proved",
                "workspace": "chia_svapshot/runs/ppa_ptw_opt",
            },
        },
        {
            "name": "ptw_expand_loss",
            "top": "ptw",
            "mode": "expand",
            "verdict": "loss",
            "baseline": RUNS / "ppa_ptw_opt" / "baseline_rtl",
            "llm": RUNS / "ppa_ptw_opt" / "candidate_rtl_expand_0",
            "bind": RUNS / "ppa_ptw_opt" / "reprove_0" / "reprove_ptw" / "ft_ptw" / "sva" / "ptw_bind.svh",
            "sva_orig": RUNS / "ppa_ptw_opt" / "contract" / "frozen" / "ptw_prop.sv",
            "sva_new": RUNS / "ppa_ptw_opt" / "contract" / "retained" / "expand" / "ptw_prop.sv",
            "order": ptw_order,
            "result": {
                "outcome": "snapshot_fail",
                "headline": "failing snapshot on new design",
                "model": "gemini-3.1-pro-preview",
                "baseline_ppa": {"cells": 2813, "levels": 22},
                "candidate_ppa": {"cells": 3148, "levels": 22},
                "snapshot": "0/43 retained properties proved",
                "workspace": "chia_svapshot/runs/ppa_ptw_opt",
            },
        },
        {
            "name": "ptw_trim_win",
            "top": "ptw",
            "mode": "trim",
            "verdict": "win",
            "baseline": RUNS / "ppa_ptw_opt" / "baseline_rtl",
            "llm": RUNS / "ppa_ptw_opt" / "candidate_rtl_trim_0",
            "bind": RUNS / "ppa_ptw_opt" / "reprove_0" / "reprove_ptw" / "ft_ptw" / "sva" / "ptw_bind.svh",
            "sva_orig": RUNS / "ppa_ptw_opt" / "contract" / "frozen" / "ptw_prop.sv",
            "sva_new": RUNS / "ppa_ptw_opt" / "contract" / "retained" / "trim" / "ptw_prop.sv",
            "order": ptw_order,
            "result": {
                "outcome": "snapshot_fail",
                "headline": "failing snapshot on new design",
                "model": "gemini-3.1-pro-preview",
                "baseline_ppa": {"cells": 2813, "levels": 22},
                "candidate_ppa": {"cells": 582, "levels": 13},
                "snapshot": "0/37 retained properties proved",
                "workspace": "chia_svapshot/runs/ppa_ptw_opt",
            },
        },
    ]

    index = []
    for case in cases:
        dest = out / case["name"]
        for key in ("baseline", "llm", "bind", "sva_orig", "sva_new"):
            if not Path(case[key]).exists():
                raise SystemExit(f"missing {key} for {case['name']}: {case[key]}")
        package(
            dest,
            top=case["top"],
            mode=case["mode"],
            verdict=case["verdict"],
            baseline_rtl=case["baseline"],
            llm_rtl=case["llm"],
            bind=case["bind"],
            sva_original=case["sva_orig"],
            sva_new=case["sva_new"],
            order=case["order"],
            result=case["result"],
        )
        index.append(
            {
                "directory": case["name"],
                "module": case["top"],
                "execution_type": case["mode"],
                "verdict": case["verdict"],
                "outcome": case["result"]["outcome"],
                "headline": case["result"]["headline"],
            }
        )
        print(dest)

    (out / "INDEX.json").write_text(json.dumps(index, indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    main()
