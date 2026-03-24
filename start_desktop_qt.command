#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [[ -x ".venv/bin/python" ]]; then
  exec ".venv/bin/python" scripts/desktop_qt_app.py
fi

echo "[ERROR] .venv not found."
echo "[INFO] Set up this repo with uv and Python 3.11 first:"
echo "[INFO]   uv python install 3.11"
echo "[INFO]   uv venv --python 3.11 .venv"
echo "[INFO]   source .venv/bin/activate"
echo "[INFO]   uv pip install -r requirements-desktop.txt"
exit 1
