@echo off
cd /d %~dp0
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\desktop_qt_app.py
  goto :eof
)

echo [ERROR] .venv not found.
echo [INFO] Set up this repo with uv and Python 3.11 first:
echo [INFO]   uv python install 3.11
echo [INFO]   uv venv --python 3.11 .venv
echo [INFO]   .venv\Scripts\activate
echo [INFO]   uv pip install -r requirements-desktop.txt
exit /b 1
