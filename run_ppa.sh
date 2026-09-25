#!/bin/bash
# Run the CHIA PPA loop on a local single-machine Ray instance.
#
#   ./run_ppa.sh --design sargantana          offline: VCF / Yosys / LLM / reprove replayed
#   ./run_ppa.sh --real --design sargantana   real gate + snapshot + rewrite + PPA
#   ./run_ppa.sh --real --design ptw --reuse-snapshot snapshot_library/ptw/latest
#
# Extra arguments are forwarded to chia_ppa_loop.py.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SVAPSHOT_ROOT="${SVAPSHOT_ROOT:-${SVAPSHOT_CHECKOUT:-$HERE/svapshot}}"
CHECKOUT="${SVAPSHOT_ROOT}"
if [[ -n "${CHIA_PYTHON:-}" && -x "${CHIA_PYTHON}" ]]; then
    PY="$CHIA_PYTHON"
elif [[ -x "$HERE/chia_env/bin/python" ]]; then
    PY="$HERE/chia_env/bin/python"
elif [[ -x "$CHECKOUT/chia_env/bin/python" ]]; then
    PY="$CHECKOUT/chia_env/bin/python"
else
    PY="python3"
fi
export SVAPSHOT_ROOT
export PYTHONPATH="$HERE:${SVAPSHOT_ROOT}:${PYTHONPATH:-}"
export PYTHONUNBUFFERED=1
export SVAPSHOT_MAX_ASSERTIONS="${SVAPSHOT_MAX_ASSERTIONS:-50}"
export SVAPSHOT_PYTHON="${SVAPSHOT_PYTHON:-$PY}"
export SVAPSHOT_LLM_MODEL="${SVAPSHOT_LLM_MODEL:-gemini-2.5-pro}"
export SVAPSHOT_VCF_DOCKER="${SVAPSHOT_VCF_DOCKER:-vcformal-dev}"

if [[ -n "${VC_FORMAL_HOME:-${VC_STATIC_HOME:-}}" ]]; then
    export VC_FORMAL_HOME="${VC_FORMAL_HOME:-$VC_STATIC_HOME}"
    export PATH="$VC_FORMAL_HOME/bin:$PATH"
fi

BYPASS=("--bypass-config" "$HERE/bypass_ppa.yaml")
ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--real" ]]; then
        BYPASS=()
    else
        ARGS+=("$arg")
    fi
done

cd "$HERE"
exec "$PY" chia_ppa_loop.py \
    --profile-dir "$HERE/runs/profile_ppa" \
    "${BYPASS[@]}" \
    ${ARGS[@]+"${ARGS[@]}"}
