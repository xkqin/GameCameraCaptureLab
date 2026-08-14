#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
venv_dir="$project_dir/.venv"

if [[ -x "$venv_dir/bin/python" ]]; then
    python_exe="$venv_dir/bin/python"
else
    python_exe=""
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            python_exe="$(command -v "$candidate")"
            break
        fi
    done
    if [[ -z "$python_exe" ]]; then
        echo "Python 3.10 or newer was not found. Install python3, python3-venv, and python3-tk first." >&2
        exit 1
    fi

    echo "First launch: creating the Python environment..."
    "$python_exe" -m venv "$venv_dir"
    python_exe="$venv_dir/bin/python"
fi

"$python_exe" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required.")
PY

if ! "$python_exe" -c 'import tkinter' >/dev/null 2>&1; then
    echo "Python Tk is missing. Install python3-tk, then run this script again." >&2
    exit 1
fi

if ! "$python_exe" -c 'import PIL, obsws_python' >/dev/null 2>&1; then
    echo "Installing capture-studio dependencies..."
    "$python_exe" -m pip install --disable-pip-version-check -e "$project_dir"
fi

exec "$python_exe" -m bmw_capture_studio "$@"
