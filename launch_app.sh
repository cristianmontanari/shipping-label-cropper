#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
    bash "$SCRIPT_DIR/shipping_label_cropper/setup_env.sh"
fi

exec "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/main.py" "$@"
