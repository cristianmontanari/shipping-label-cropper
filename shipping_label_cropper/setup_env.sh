#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [ -x ".venv/bin/python3" ]; then
    if ".venv/bin/python3" -c "import pymupdf, PIL, tkinter" >/dev/null 2>&1; then
        exit 0
    fi
fi

find_python() {
    local candidates=(
        python3
        python3.14
        python3.13
        python3.12
        python3.11
        python3.10
        /opt/homebrew/bin/python3
        /usr/local/bin/python3
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3
    )
    local found_without_tk=""
    local bin resolved
    for bin in "${candidates[@]}"; do
        resolved="$(command -v "$bin" 2>/dev/null || true)"
        if [ -z "$resolved" ] && [ -x "$bin" ]; then
            resolved="$bin"
        fi
        [ -n "$resolved" ] || continue

        if "$resolved" -c "import sys, venv, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
            echo "$resolved"
            return 0
        fi
        if [ -z "$found_without_tk" ]; then
            if "$resolved" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
                found_without_tk="$resolved"
            fi
        fi
    done

    if [ -n "$found_without_tk" ]; then
        echo "" >&2
        echo "[ERROR] Found Python ($found_without_tk), but 'tkinter' or 'venv' is missing." >&2
        if [ "$(uname -s)" = "Darwin" ]; then
            echo "On macOS with Homebrew, install Tk support with:" >&2
            echo "  brew install python-tk@3.12" >&2
            echo "Or download the official Python installer from https://www.python.org/downloads/" >&2
        else
            echo "On Linux, install the system Tk & venv packages:" >&2
            echo "  Debian/Ubuntu: sudo apt install python3-tk python3-venv" >&2
            echo "  Fedora:        sudo dnf install python3-tkinter" >&2
            echo "  Arch:          sudo pacman -S tk" >&2
        fi
        return 1
    fi

    echo "" >&2
    echo "[ERROR] Python 3.10+ with tkinter was not found." >&2
    echo "Install Python 3.10+ from your package manager or https://www.python.org/downloads/" >&2
    return 1
}

PYTHON_EXE="$(find_python)"
echo "Found Python: $PYTHON_EXE"

if [ ! -x ".venv/bin/python3" ]; then
    rm -rf ".venv"
    echo "Creating virtual environment..."
    "$PYTHON_EXE" -m venv .venv
fi

echo "Installing dependencies..."
".venv/bin/python3" -m pip install -r requirements.txt

if ! ".venv/bin/python3" -c "import pymupdf, PIL, tkinter" >/dev/null 2>&1; then
    echo "" >&2
    echo "[ERROR] Dependencies were not installed properly in .venv." >&2
    exit 1
fi
