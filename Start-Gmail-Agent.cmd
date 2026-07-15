@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Gmail Compliance Agent
set "UV_CACHE_DIR=%LOCALAPPDATA%\uv-cache-gmail-agent"
set "UV_LINK_MODE=copy"
set "PYTHONUNBUFFERED=1"
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%USERPROFILE%\.local\bin;%PATH%"

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo Created .env with safe plan-only defaults.
)

where uv >nul 2>&1
if errorlevel 1 (
  echo uv is missing. Opening guided setup...
  call "%~dp0Setup-Gmail-Agent.cmd" --no-launch
  if errorlevel 1 exit /b 1
)

if not exist ".venv\Scripts\gmail-agent.exe" (
  echo The project environment is missing. Repairing it now...
  uv sync --locked --extra dev
  if errorlevel 1 (
    echo.
    echo Automatic environment repair failed. Run Setup-Gmail-Agent.cmd for details.
    pause
    exit /b 1
  )
)

if not exist ".node\node-v22.22.3-win-x64\node.exe" (
  echo The Reflex frontend runtime is missing. Repairing it now...
  uv run --no-sync python scripts\install_node.py
  if errorlevel 1 (
    echo.
    echo Automatic frontend runtime repair failed. Run Setup-Gmail-Agent.cmd for details.
    pause
    exit /b 1
  )
)

set "PATH=%CD%\.node\node-v22.22.3-win-x64;%PATH%"

echo Checking startup requirements...
uv run --no-sync compliance-agent doctor
if errorlevel 1 (
  echo.
  echo Startup checks failed. Edit .env or run Setup-Gmail-Agent.cmd.
  pause
  exit /b 1
)

echo.
echo Starting Gmail Compliance Agent...
echo The secure local console will open in your browser automatically.
echo Keep this window open while you use the console. Press Ctrl+C to stop.
echo.

uv run --no-sync gmail-agent
set "exit_code=%ERRORLEVEL%"

if not "%exit_code%"=="0" (
  echo.
  echo The console stopped with exit code %exit_code%.
  pause
)

exit /b %exit_code%
