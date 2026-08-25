@echo off
REM ══════════════════════════════════════════════════════════
REM  IRIS one-shot installer for Windows
REM  Creates a virtual environment, installs everything, and
REM  registers IRIS to start with Windows.
REM ══════════════════════════════════════════════════════════
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo Python 3.11+ is required. Get it from https://python.org and re-run.
    pause & exit /b 1
)

echo [1/4] Creating virtual environment...
python -m venv .venv || (echo venv creation failed & pause & exit /b 1)

echo [2/4] Installing IRIS core...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q

echo [3/4] Installing desktop, voice and content extras...
pip install -r requirements-desktop.txt -q
pip install pycaw comtypes -q

echo [4/4] Registering autostart...
python -m iris autostart enable

echo.
echo  Done! Starting IRIS now...
echo  (Later, just run:  .venv\Scripts\python -m iris)
python -m iris
