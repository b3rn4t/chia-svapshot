#!/bin/bash
# Install the Antigravity CLI (agy). Sign-in is interactive: run `agy` after.
# https://docs.chialoops.ai/en/latest/user_guides/google_auth.html
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/scripts/download.sh"

if command -v agy >/dev/null 2>&1 || [[ -x "${HOME}/.local/bin/agy" ]]; then
    echo "agy already at $(command -v agy 2>/dev/null || echo "${HOME}/.local/bin/agy")"
    exit 0
fi

INSTALLER="$(mktemp)"
_download https://antigravity.google/cli/install.sh "$INSTALLER"
bash "$INSTALLER"
rm -f "$INSTALLER"
export PATH="${HOME}/.local/bin:${PATH}"
if ! command -v agy >/dev/null 2>&1; then
    echo "installed to ~/.local/bin — add it to PATH, then run: agy" >&2
    exit 1
fi
echo "next: agy   # browser OAuth; pick location=global so Gemini Pro works"
