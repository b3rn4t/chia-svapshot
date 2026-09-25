#!/bin/bash
# Official CHIA hackathon GCP steps for `chia up` + Gemini ADC.
# Does not launch instances. Does not store credentials in the repo.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${HOME}/google-cloud-sdk/bin:${PATH}"
# shellcheck disable=SC1091
source "$HERE/scripts/env.sh"

PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
if [[ -z "${PROJECT}" || "${PROJECT}" == "(unset)" ]]; then
    echo "set GOOGLE_CLOUD_PROJECT or: gcloud config set project <id>" >&2
    exit 1
fi
export GOOGLE_CLOUD_PROJECT="$PROJECT"

VENV_PY="${HERE}/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
    UV="${HERE}/toolchains/uv/uv"
    if [[ -x "$UV" ]]; then
        "$UV" pip install --python "$VENV_PY" google-cloud-compute
    else
        "$VENV_PY" -m pip install google-cloud-compute
    fi
    "$VENV_PY" -c "import google.cloud.compute; print('google-cloud-compute ok')"
else
    echo "hackathon .venv missing — run ./setup_env.sh first" >&2
    exit 1
fi

gcloud auth application-default set-quota-project "$PROJECT"
gcloud services enable compute.googleapis.com --project "$PROJECT"
gcloud services enable aiplatform.googleapis.com --project "$PROJECT"

echo
echo "GCP cluster auth ready for project $PROJECT"
echo "  ADC: $HOME/.config/gcloud/application_default_credentials.json"
echo "  SSH key for cluster.yaml: ${HOME}/.ssh/id_ed25519"
if ssh-add -l >/dev/null 2>&1; then
    echo "  ssh-agent: has keys (chia up will not prompt)"
else
    echo "  ssh-agent: empty — run: ssh-add ${HOME}/.ssh/id_ed25519"
fi
echo
echo "Not done here (interactive / account):"
echo "  1. agy OAuth:  ./scripts/install_agy.sh && agy   # pick location=global"
echo "  2. Tailscale:  https://tailscale.com  then export TS_AUTHKEY=... for cluster_gcp.yaml"
echo "  3. chia up cluster.yaml            # this machine only"
echo "     chia up cluster_gcp.yaml        # provisions GCE VMs — costs credits"
