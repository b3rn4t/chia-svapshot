#!/bin/bash
# User-local Yosys + SymbiYosys + boolector (oss-cad-suite). No sudo.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/scripts/download.sh"
DEST="$HERE/toolchains"
VER="${OSS_CAD_SUITE_VERSION:-2026-08-31}"
STAMP="${VER//-/}"
NAME="oss-cad-suite-linux-x64-${STAMP}"
URL="https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${VER}/${NAME}.tgz"

if [[ -x "$DEST/oss-cad-suite/bin/sby" && -x "$DEST/oss-cad-suite/bin/yosys" ]]; then
    "$DEST/oss-cad-suite/bin/yosys" -V | head -1
    "$DEST/oss-cad-suite/bin/sby" --version | head -1
    exit 0
fi

mkdir -p "$DEST"
TGZ="$DEST/${NAME}.tgz"
_download "$URL" "$TGZ"
rm -rf "$DEST/oss-cad-suite"
tar -C "$DEST" -xzf "$TGZ"
rm -f "$TGZ"
"$DEST/oss-cad-suite/bin/yosys" -V | head -1
"$DEST/oss-cad-suite/bin/sby" --version | head -1
echo "export PATH=$DEST/oss-cad-suite/bin:\$PATH"
