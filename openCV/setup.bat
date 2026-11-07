@echo off
REM VeriFi setup: creates .venv, installs the pinned requirements, downloads the two face models.
REM Every run_*.bat calls this first, so you never need to run it by hand. The first time takes ~2 min
REM (needs internet); after that it only checks (a few seconds, works offline).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [setup] creating the Python environment .venv ...
    python -m venv .venv || goto :fail
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check --upgrade pip
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || goto :fail
".venv\Scripts\python.exe" download_models.py --quiet || goto :fail
exit /b 0

:fail
echo.
echo [setup] FAILED - see the error above. Needs Python 3.14 on PATH and internet the first time.
exit /b 1
