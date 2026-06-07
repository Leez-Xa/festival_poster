@echo off
setlocal

cd /d "%~dp0"

set "APP_ENV=demo"
set "AI_REQUIRE_IMAGE_FUSION=false"
set "API_ACCESS_TOKEN="
set "HOST=127.0.0.1"
set "PORT=8000"
set "PYTHON_EXE=.venv\Scripts\python.exe"

echo [Festival Poster] Preparing local demo...

if not exist "%PYTHON_EXE%" (
  echo [Festival Poster] Creating Python virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    py -3 -m venv .venv
  )
)

if not exist "%PYTHON_EXE%" (
  echo [Festival Poster] Python virtual environment was not created.
  echo Please install Python 3.11+ and run this file again.
  pause
  exit /b 1
)

echo [Festival Poster] Installing local dependencies...
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [Festival Poster] Dependency installation failed.
  pause
  exit /b 1
)

echo [Festival Poster] Opening http://%HOST%:%PORT%/
start "" "http://%HOST%:%PORT%/"

echo [Festival Poster] Starting backend and frontend single-entry app...
echo [Festival Poster] Close this window to stop the local demo.
"%PYTHON_EXE%" -m uvicorn app.main:app --host %HOST% --port %PORT%

endlocal
