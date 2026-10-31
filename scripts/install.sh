#!/usr/bin/env bash
# Installs the app for this user: its environment, the engines and the menu entry.
# Safe to run again. Nothing is installed outside this folder and ~/.local/share.
#
# Usage: scripts/install.sh [--no-demucs | --only-menu]
#   --no-demucs   skips Demucs, which takes about 800 MB
#   --only-menu   only creates the menu entry, for example after moving this folder
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPLICATIONS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"

check_requirements() {
    local missing=()
    command -v uv >/dev/null || {
        echo "Falta o uv: https://docs.astral.sh/uv/getting-started/installation/" >&2
        exit 1
    }
    command -v ffmpeg >/dev/null || missing+=(ffmpeg)
    # Read in full: `ldconfig | grep -q` fails under pipefail when grep stops at the first match.
    local libraries
    libraries="$(PATH="$PATH:/sbin:/usr/sbin" ldconfig -p)"
    [[ "$libraries" == *libportaudio.so.2* ]] || missing+=(libportaudio2)
    [[ "$libraries" == *libxcb-cursor.so.0* ]] || missing+=(libxcb-cursor0)
    if ((${#missing[@]})); then
        echo "Faltam pacotes do sistema. Instale-os com:" >&2
        echo "  sudo apt install ${missing[*]}" >&2
        exit 1
    fi
}

install_menu_entry() {
    # These characters would need escaping in the Exec line of a .desktop file.
    if [[ "$ROOT" == *[\"\$\`\\%]* ]]; then
        echo "A pasta do projeto não pode ter estes caracteres no caminho: \" \$ \` \\ %" >&2
        exit 1
    fi
    mkdir -p "$APPLICATIONS" "$ICONS/scalable/apps"
    cp "$ROOT/assets/spleeter-gui.svg" "$ICONS/scalable/apps/spleeter-gui.svg"
    # The environment's Python directly: going through `uv run` would slow every opening.
    cat >"$APPLICATIONS/spleeter-gui.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Spleeter GUI
Comment=Separar músicas em stems e mixá-los
Exec="$ROOT/.venv/bin/python" -m spleeter_gui
Icon=spleeter-gui
Terminal=false
Categories=AudioVideo;Audio;
StartupNotify=true
StartupWMClass=spleeter-gui
DESKTOP
    # Only caches: the entry works without them, so a failure here is not an error.
    update-desktop-database "$APPLICATIONS" 2>/dev/null || true
    gtk-update-icon-cache -q -f -t "$ICONS" 2>/dev/null || true
}

engines=(all)
case "${1:-}" in
    "") ;;
    --no-demucs) engines=(spleeter) ;;
    --only-menu)
        install_menu_entry
        echo "Atalho criado no menu."
        exit 0
        ;;
    *)
        sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \?//' >&2
        exit 2
        ;;
esac

check_requirements
(cd "$ROOT" && uv sync --no-dev)
"$ROOT/scripts/setup_engines.sh" "${engines[@]}"
install_menu_entry
echo
echo "Pronto. Abra o Spleeter GUI pelo menu de aplicativos."
echo "Cada modelo é baixado na primeira vez em que for usado."
