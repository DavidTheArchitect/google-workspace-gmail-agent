@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Gmail Compliance Agent Setup
set "UV_CACHE_DIR=%LOCALAPPDATA%\uv-cache-gmail-agent"
set "UV_LINK_MODE=copy"
set "PYTHONUNBUFFERED=1"
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%USERPROFILE%\.local\bin;%PATH%"

echo Gmail Compliance Agent setup
echo.

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo Created .env with safe plan-only defaults.
) else (
  echo Existing .env preserved.
)

where uv >nul 2>&1
if errorlevel 1 (
  where winget >nul 2>&1
  if errorlevel 1 (
    echo.
    echo uv and WinGet were not found.
    echo Install uv from https://docs.astral.sh/uv/getting-started/installation/ and run setup again.
    pause
    exit /b 1
  )

  echo.
  choice /C YN /N /M "Install uv using the official astral-sh.uv WinGet package? [Y/N] "
  if errorlevel 2 exit /b 1
  winget install --id=astral-sh.uv -e --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo uv installation failed. Review the WinGet message above.
    pause
    exit /b 1
  )
)

echo.
echo Creating or repairing the project environment...
uv sync --locked --extra dev
if errorlevel 1 (
  echo.
  echo Environment setup failed. Review the uv message above.
  pause
  exit /b 1
)

echo.
uv run --no-sync compliance-agent doctor
if errorlevel 1 (
  echo.
  echo Configuration needs attention. Edit .env, then run setup again.
  pause
  exit /b 1
)

echo.
echo Setup complete. You can now double-click Start-Gmail-Agent.cmd.

if /I "%~1"=="--no-launch" exit /b 0
choice /C YN /N /M "Start Gmail Compliance Agent now? [Y/N] "
if errorlevel 2 exit /b 0
call "%~dp0Start-Gmail-Agent.cmd"
exit /b %ERRORLEVEL%
