@echo off
setlocal
title JobPulse Data Reset
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\compose-reset.ps1" -Layout JobPulse
if errorlevel 1 (
  echo.
  echo JobPulse data reset failed. Keep this window open and send the error text to support.
  pause
  exit /b 1
)
echo.
echo JobPulse was rebuilt from empty data volumes.
pause
