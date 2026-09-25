#!/bin/bash
# wget/curl helper — this box has wget but not curl.
_download() {
    local url="$1" dest="$2"
    mkdir -p "$(dirname "$dest")"
    if command -v curl >/dev/null 2>&1; then
        curl -L --fail --retry 3 -o "$dest" "$url"
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$dest" "$url"
    else
        echo "need curl or wget to fetch $url" >&2
        return 1
    fi
}
