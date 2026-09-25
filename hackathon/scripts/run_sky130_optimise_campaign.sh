#!/usr/bin/env bash
# Sequential Sky130 HD optimise campaign. One VC Formal licence.
# Cap 50 assertions. Coverage + medium signoff OC/FC on reprove.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHIA="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$CHIA/.." && pwd)"
LOG="$CHIA/runs/ppa_sky130_campaign.log"
mkdir -p "$CHIA/runs"
exec > >(tee -a "$LOG") 2>&1

export SVAPSHOT_ROOT="$ROOT"
export SVAPSHOT_MAX_ASSERTIONS=50
export SVAPSHOT_COVERAGE=1
export SVAPSHOT_SIGNOFF=1
export SVAPSHOT_LLM_MODEL="${SVAPSHOT_LLM_MODEL:-gemini-3.8-flash}"
export SVAPSHOT_VCF_DOCKER="${SVAPSHOT_VCF_DOCKER:-vcformal-dev}"
unset SVAPSHOT_YOSYS_GENERIC

cd "$CHIA"

run_one() {
  local name="$1"
  shift
  local ws="$CHIA/runs/ppa_${name}_sky130"
  echo "===== start $name $(date -Is) ====="
  # MMU elaborate hung for hours last time; bound the whole job.
  local limit=2700
  if [[ "$name" == mmu_top || "$name" == bsc_mmu ]]; then
    limit=2400
  fi
  timeout --signal=TERM --kill-after=60s "${limit}"s \
    ./run_ppa.sh --real \
      --design "$name" \
      --rewrite-modes optimise \
      --rewrite-attempts 3 \
      --workspace "$ws" \
      --svapshot-model "$SVAPSHOT_LLM_MODEL" \
      --summary-json "$ws/ppa_loop_summary.json" \
      "$@" \
    && echo "===== $name OK $(date -Is) =====" \
    || echo "===== $name FAILED rc=$? $(date -Is) ====="
}

# Known-good RTL seeds so Sky130 scoring is not waiting on a new rewrite.
# Pass SKIP_SARGANTANA=1 when the divider win is already on disk.
if [[ "${SKIP_SARGANTANA:-0}" != 1 ]]; then
  run_one sargantana \
    --reuse-snapshot "$CHIA/snapshot_library/div_unit/latest" \
    --rewrite-rtl "$ROOT/date2027/optimise/cases/div_unit/optimised_rtl/div_unit.sv"
fi

run_one ptw \
  --reuse-snapshot "$CHIA/snapshot_library/ptw/latest" \
  --rewrite-rtl "$ROOT/date2027/optimise/cases/ptw/optimised_rtl/ptw.sv"

run_one tlb \
  --reuse-snapshot "$CHIA/snapshot_library/tlb/latest" \
  --rewrite-rtl "$ROOT/date2027/optimise/cases/tlb/optimised_rtl/tlb.sv"

# Wrappers: no packaged win yet; generate a 50-assert snapshot.
run_one mmu_top
run_one bsc_mmu

echo "===== campaign done $(date -Is) ====="
