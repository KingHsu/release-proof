@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

if not exist ".venv\Scripts\python.exe" (
  echo First launch: preparing the local ReleaseProof environment...
  py -3.11 -m venv .venv >nul 2>nul
  if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv >nul 2>nul
  if not exist ".venv\Scripts\python.exe" python -m venv .venv >nul 2>nul
  if not exist ".venv\Scripts\python.exe" (
    echo Python 3.11 or 3.12 was not found.
    pause
    exit /b 2
  )
  ".venv\Scripts\python.exe" -m pip install -e .
  if errorlevel 1 (
    echo Environment setup failed. Check the network and try again.
    pause
    exit /b 2
  )
)

".venv\Scripts\python.exe" -m release_proof
if errorlevel 1 (
  echo.
  echo ReleaseProof exited with an error.
  pause
)
