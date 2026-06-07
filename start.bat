@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "DEMO_MODE=0"
set "CHECK_ONLY=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--demo" set "DEMO_MODE=1"
if /I "%~1"=="--check" set "CHECK_ONLY=1"
shift
goto parse_args

:args_done
if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=8000"
set "PYTHON_EXE=.venv\Scripts\python.exe"

if "%DEMO_MODE%"=="1" (
  set "APP_ENV=demo"
  set "AI_REQUIRE_IMAGE_FUSION=false"
  set "API_ACCESS_TOKEN="
)

echo [Festival Poster] One-click startup
if "%DEMO_MODE%"=="1" (
  echo [Festival Poster] Mode: demo fallback
) else (
  echo [Festival Poster] Mode: normal .env configuration
)

if "%CHECK_ONLY%"=="1" (
  echo [Festival Poster] Script check passed.
  exit /b 0
)

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

set "PORT_CANDIDATES=%PORT% 18085 18086 18087"
for %%P in (%PORT_CANDIDATES%) do (
  netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul
  if errorlevel 1 (
    set "PORT=%%P"
    goto port_ready
  )
)

echo [Festival Poster] No available local port found in: %PORT_CANDIDATES%
pause
exit /b 1

:port_ready
echo [Festival Poster] Backend serves the frontend in one app.
echo [Festival Poster] Opening http://%HOST%:%PORT%/
start "" "http://%HOST%:%PORT%/"

echo [Festival Poster] Close this window to stop the local app.
"%PYTHON_EXE%" -m uvicorn app.main:app --host %HOST% --port %PORT% --reload

endlocal
