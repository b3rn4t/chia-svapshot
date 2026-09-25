#!/bin/bash
# Build Spike into toolchains/spike. Host has no gcc; use Docker when present.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="$HERE/toolchains/spike"
if [[ -x "$PREFIX/bin/spike" ]]; then
    "$PREFIX/bin/spike" -h >/dev/null
    echo "spike already at $PREFIX/bin/spike"
    exit 0
fi
if command -v spike >/dev/null 2>&1; then
    echo "using host spike: $(command -v spike)"
    exit 0
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "no gcc and no docker — cannot build spike" >&2
    exit 1
fi

mkdir -p "$PREFIX"
docker run --rm \
    -v "$PREFIX:/opt/spike" \
    ubuntu:22.04 \
    bash -lc '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    build-essential ca-certificates git \
    device-tree-compiler \
    libboost-dev libboost-regex-dev libboost-system-dev \
    python3
git clone --depth 1 https://github.com/riscv-software-src/riscv-isa-sim.git /tmp/spike
mkdir /tmp/spike/build
cd /tmp/spike/build
../configure --prefix=/opt/spike
make -j"$(nproc)"
make install
'

if [[ ! -x "$PREFIX/bin/dtc" ]]; then
    docker run --rm -v "$PREFIX/bin:/out" ubuntu:22.04 bash -lc '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq device-tree-compiler
        cp "$(command -v dtc)" /out/dtc
    '
fi
"$PREFIX/bin/spike" -h >/dev/null
echo "spike installed at $PREFIX/bin/spike"
