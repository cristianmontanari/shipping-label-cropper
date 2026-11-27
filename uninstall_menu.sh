#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -x ".venv/bin/python3" ]; then
    ".venv/bin/python3" "$SCRIPT_DIR/main.py" --uninstall-menu
else
    if [ "$(uname -s)" = "Darwin" ]; then
        rm -rf "$HOME/Library/Services/Crop Shipping Label.workflow"
        rm -rf "$HOME/Library/Services/Crop Label 10x15.workflow"
        if [ -x "/System/Library/CoreServices/pbs" ]; then
            /System/Library/CoreServices/pbs -update >/dev/null 2>&1 || true
        fi
        echo "Finder Quick Action removed successfully."
    else
        XDG_DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
        rm -f "$XDG_DATA/applications/shipping-label-cropper.desktop"
        rm -f "$XDG_DATA/applications/croppdf-10x15.desktop"
        rm -f "$XDG_DATA/nautilus/scripts/Crop Shipping Label"
        rm -f "$XDG_DATA/nautilus/scripts/Crop Label 10x15"
        rm -f "$XDG_DATA/kio/servicemenus/shipping-label-cropper-servicemenu.desktop"
        rm -f "$XDG_DATA/kio/servicemenus/croppdf-10x15-servicemenu.desktop"
        if command -v update-desktop-database >/dev/null 2>&1 && [ -d "$XDG_DATA/applications" ]; then
            update-desktop-database "$XDG_DATA/applications" >/dev/null 2>&1 || true
        fi
        echo "File manager menu removed successfully."
    fi
fi
