#!/bin/bash
# Run the SVApshot CHIA loop on a local single-machine Ray instance.
#
#   ./run_local.sh                  offline replay: the scaffolder runs for real,
#                                   seeding and proving are served by bypass
#   ./run_local.sh --real           everything runs for real (needs a VC Formal
#                                   licence and NVIDIA_API_KEY)
#   ./run_local.sh --module register --iterations 2
#
# Any extra arguments are forwarded to svapshot_loop.py.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer SVAPSHOT_ROOT; SVAPSHOT_CHECKOUT kept as a deprecated alias.
# Default is the svapshot git submodule next to this loop.
SVAPSHOT_ROOT="${SVAPSHOT_ROOT:-${SVAPSHOT_CHECKOUT:-$HERE/svapshot}}"
CHECKOUT="${SVAPSHOT_ROOT}"
if [[ -n "${CHIA_PYTHON:-}" ]]; then
    PY="$CHIA_PYTHON"
elif [[ -x "$HERE/chia_env/bin/python" ]]; then
    PY="$HERE/chia_env/bin/python"
elif [[ -x "$CHECKOUT/chia_env/bin/python" ]]; then
    PY="$CHECKOUT/chia_env/bin/python"
else
    PY="python3"
fi
export SVAPSHOT_ROOT

if [[ -n "${VC_FORMAL_HOME:-${VC_STATIC_HOME:-}}" ]]; then
    export VC_FORMAL_HOME="${VC_FORMAL_HOME:-$VC_STATIC_HOME}"
    export PATH="$VC_FORMAL_HOME/bin:$PATH"
fi

BYPASS=("--bypass-config" "$HERE/bypass.yaml")
ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--real" ]]; then
        BYPASS=()
    else
        ARGS+=("$arg")
    fi
done

# cd into this directory so `import chia` resolves to the installed package
# rather than the sibling source checkout at $CHECKOUT/chia.
cd "$HERE"
exec "$PY" svapshot_loop.py \
    --checkout "$CHECKOUT" \
    --profile-dir "$HERE/runs/profile" \
    "${BYPASS[@]}" \
    ${ARGS[@]+"${ARGS[@]}"}
