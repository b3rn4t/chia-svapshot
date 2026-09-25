#!/usr/bin/env bash
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
order = ["fpnew_pkg.sv", "riscv_pkg.sv", "drac_pkg.sv", "div_4bits.sv", "div_unit.sv"]
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
"$yosys_bin" -Q -T -p "plugin -i slang; read_slang --single-unit --top div_unit $quoted; proc; opt; synth -top div_unit; stat; ltp -noff" | tee "$out"
