#!/bin/bash
# PATH for the hackathon loop. Sourced by setup_env.sh and run_local.sh.
# Does not require sudo: every binary lives under toolchains/ or .venv.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${HOME}/.local/bin:${HOME}/google-cloud-sdk/bin:${PATH}"
export PATH="$HERE/toolchains/uv:$PATH"
export PATH="$HERE/toolchains/xpack-riscv-none-elf-gcc-14.3.0-1/bin:${PATH}"
export PATH="$HERE/toolchains/oss-cad-suite/bin:${PATH}"
export PATH="$HERE/toolchains/spike/bin:${PATH}"
export RISCV_PREFIX="${RISCV_PREFIX:-$HERE/toolchains/xpack-riscv-none-elf-gcc-14.3.0-1/bin/riscv-none-elf-}"
# shellcheck disable=SC1091
[[ -f "$HERE/.env.gcp" ]] && source "$HERE/.env.gcp"
if [[ -z "${GOOGLE_CLOUD_PROJECT:-}" ]] && command -v gcloud >/dev/null 2>&1; then
    GOOGLE_CLOUD_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
    export GOOGLE_CLOUD_PROJECT
fi
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-global}"
export SVAPSHOT_ROOT="${SVAPSHOT_ROOT:-$(cd "$HERE/../.." && pwd)}"
export PYTHONPATH="$HERE:$HERE/..:${PYTHONPATH:-}"
# Only auto-pick Chipyard after build-setup.sh created the conda env.
if [[ -z "${CHIPYARD_ROOT:-}" && -d "${HOME}/chipyard/.conda-env" ]]; then
    export CHIPYARD_ROOT="${HOME}/chipyard"
fi
