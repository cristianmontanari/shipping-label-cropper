#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

bash "$SCRIPT_DIR/shipping_label_cropper/setup_env.sh"
".venv/bin/python3" "$SCRIPT_DIR/main.py" --install-menu
