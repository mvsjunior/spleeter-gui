#!/usr/bin/env bash
# Creates the engine environments. Safe to run again: uv skips what is installed.
#
# Usage: scripts/setup_engines.sh [spleeter|demucs]
#   With no argument, installs both engines.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

create_venv() {
    local venv="$1"
    [[ -x "$venv/bin/python" ]] || uv venv --python 3.11 "$venv"
}

install_spleeter() {
    local dir="$ROOT/engines/spleeter"
    create_venv "$dir/.venv"
    uv pip install --python "$dir/.venv" -r "$dir/requirements.txt"
    # Its metadata pins tensorflow==2.12.1 and typer<0.4, which are not what runs here.
    uv pip install --python "$dir/.venv" --no-deps spleeter==2.4.2
}

install_demucs() {
    local dir="$ROOT/engines/demucs"
    create_venv "$dir/.venv"
    uv pip install --python "$dir/.venv" \
        --index https://download.pytorch.org/whl/cpu \
        --default-index https://pypi.org/simple \
        --index-strategy unsafe-best-match \
        -r "$dir/requirements.txt"
}

case "${1:-all}" in
    spleeter) install_spleeter ;;
    demucs) install_demucs ;;
    all)
        install_spleeter
        install_demucs
        ;;
    *)
        sed -n '4,5p' "${BASH_SOURCE[0]}" >&2
        exit 2
        ;;
esac
