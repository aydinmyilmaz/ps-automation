#!/bin/bash
cd "$(dirname "$0")" || exit 1
/usr/local/opt/python@3.11/bin/python3.11 scripts/desktop_qt_app.py
