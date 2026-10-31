#!/usr/bin/env bash
# Removes what install.sh created. Models, history and preferences only go after confirmation.
# This folder stays, so that it can be installed again.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$HOME/.local/share/spleeter-gui"
CONFIG="$HOME/.config/spleeter-gui"
ICONS="$HOME/.local/share/icons/hicolor"

confirm() {
    local answer=""
    read -r -p "$1 [s/N] " answer || true
    [[ "$answer" == [sS] ]]
}

size_of() {
    du -sh "$1" 2>/dev/null | cut -f1
}

if pgrep -f "$ROOT/.venv/bin/python -m spleeter_gui" >/dev/null; then
    echo "O Spleeter GUI está aberto. Feche-o antes de desinstalar." >&2
    exit 1
fi

rm -f "$HOME/.local/share/applications/spleeter-gui.desktop"
rm -f "$ICONS/scalable/apps/spleeter-gui.svg"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache -q -f -t "$ICONS" 2>/dev/null || true
rm -rf "$ROOT/.venv" "$ROOT/engines/spleeter/.venv" "$ROOT/engines/demucs/.venv"
echo "Removidos o atalho, o ícone e os ambientes Python."

if [[ -d "$DATA/models" ]]; then
    if confirm "Remover também os modelos baixados ($(size_of "$DATA/models"))?"; then
        rm -rf "$DATA/models"
        echo "Modelos removidos."
    else
        echo "Modelos mantidos em $DATA/models"
    fi
fi

if [[ -e "$DATA/history.db" || -d "$CONFIG" ]]; then
    if confirm "Remover também o histórico e as preferências?"; then
        rm -f "$DATA/history.db" "$DATA"/history.db.corrompido
        rm -rf "$CONFIG"
        echo "Histórico e preferências removidos."
    else
        echo "Histórico e preferências mantidos."
    fi
fi

rm -f "$DATA/app.lock"
rmdir "$DATA" 2>/dev/null || true
echo "Desinstalado. As músicas separadas ficam onde você as salvou."
