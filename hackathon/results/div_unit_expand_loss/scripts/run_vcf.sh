#!/usr/bin/env bash
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
mkdir -p "$work/ft_div_unit/sva" "$work/vcf_projs/div_unit"

if [[ "$rtl" == original ]]; then
  rtl_dir="$ROOT/original_rtl"
  prop="$ROOT/sva/original/div_unit_prop.sv"
else
  rtl_dir="$ROOT/llm_rtl"
  prop="$ROOT/sva/new/div_unit_prop.sv"
fi

cp -a "$rtl_dir"/. "$work/"
cp "$prop" "$work/ft_div_unit/sva/div_unit_prop.sv"
cp "$ROOT/bind/div_unit_bind.svh" "$work/ft_div_unit/sva/div_unit_bind.svh"

python3 - <<PY
from pathlib import Path
work = Path("$work")
names = ["fpnew_pkg.sv", "riscv_pkg.sv", "drac_pkg.sv", "div_4bits.sv", "div_unit.sv"]
abs_files = []
for name in names:
    p = work / name
    if p.is_file():
        abs_files.append(str(p.resolve()))
prop = (work / "ft_div_unit/sva/div_unit_prop.sv").resolve()
bind = (work / "ft_div_unit/sva/div_unit_bind.svh").resolve()
vc = work / "ft_div_unit/files_vcf.vc"
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
tcl = work / "ft_div_unit/FPV_vcf.tcl"
tcl.write_text("""# Replay FPV for div_unit
set top div_unit
set_fml_appmode FPV
set files_vcf [file join [file dirname [info script]] files_vcf.vc]
suppress_message SM_UST
analyze -format sverilog -vcs "-f $files_vcf"
elaborate -sva $top
set_fml_var fml_vacuity_on true
set_fml_var fml_witness_on true
create_clock clk_i -period 100
set_change_at -default -posedge -clock clk_i
create_reset rstn_i -sense low
set report_dir "vcf_projs/div_unit/reports"
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
LOG="$work/vcf_projs/div_unit/vcf.log"
mkdir -p "$(dirname "$LOG")"

run_vcf() {
  if [[ -f /.dockerenv ]]; then
    export TERM="${TERM:-vt100}"
    export VC_FORMAL_HOME="$VCF_HOME"
    export VC_STATIC_HOME="${VC_STATIC_HOME:-$VCF_HOME}"
    export PATH="$VCF_HOME/bin:$PATH"
    (cd "$work" && vcf -session "vcf_projs/div_unit/vcst_rtdb" -no_restore -f "ft_div_unit/FPV_vcf.tcl" -batch)
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
    -session "vcf_projs/div_unit/vcst_rtdb" \
    -no_restore \
    -f "ft_div_unit/FPV_vcf.tcl" \
    -batch
}

run_vcf < /dev/null > "$LOG" 2>&1 || true
echo "VC Formal log: $LOG"
