#!/bin/bash
# Host python3 has no ensurepip/venv. Install a user-local uv + CPython.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/scripts/download.sh"
UV_DIR="$HERE/toolchains/uv"
UV="$UV_DIR/uv"
VENV="$HERE/.venv"
mkdir -p "$UV_DIR"

if [[ ! -x "$UV" ]]; then
    TGZ="$HERE/toolchains/uv-x86_64-unknown-linux-gnu.tar.gz"
    _download \
        "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz" \
        "$TGZ"
    tar -C "$UV_DIR" --strip-components=1 -xzf "$TGZ"
    rm -f "$TGZ"
fi
"$UV" --version

if [[ ! -x "$VENV/bin/python" ]]; then
    "$UV" python install 3.11
    "$UV" venv --python 3.11 "$VENV"
fi
"$UV" pip install --python "$VENV/bin/python" -U pip wheel
"$UV" pip install --python "$VENV/bin/python" 'chialoops==1.0.1' pytest google-cloud-compute
"$VENV/bin/python" -c "import chia; print('chialoops', chia.__path__[0])"
