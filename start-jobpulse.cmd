@echo off
setlocal
title JobPulse Starter
rem Resolve the batch file itself to an absolute directory. PowerShell can
rem invoke a relative .cmd through cmd.exe with a shortened %0 value.
for %%I in ("%~f0") do set "SCRIPT_DIR=%%~dpI"
rem Usage:
rem   start-jobpulse.cmd            rebuild from source, then start
rem   start-jobpulse.cmd /offline   skip the rebuild and the network after
rem                                 importing the candidate offline packages
rem   start-jobpulse.cmd /full      start core plus JD/CV, semantic and crawler profiles
set "OFFLINE="
set "FULL="
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="/offline" set "OFFLINE=1"
if /i "%~1"=="/full" set "FULL=1"
shift
goto parse_args
:args_done
if defined OFFLINE (
  if defined FULL (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\start-offline-full.ps1"
  ) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\compose-up.ps1" -Layout JobPulse -Offline
  )
) else (
  if defined FULL (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\compose-up.ps1" -Layout JobPulse -Build -Full
  ) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\compose-up.ps1" -Layout JobPulse -Build
  )
)
if errorlevel 1 (
  echo.
  echo JobPulse failed to start. Keep this window open and send the error text to support.
  pause
  exit /b 1
)
echo.
rem Read the actual frontend port from infra\.env (compose-up.ps1 uses the
rem same value); the old hardcoded 3000 could belong to another stack.
set "FRONTEND_PORT=3000"
for /f "usebackq tokens=1,* delims==" %%a in ("%SCRIPT_DIR%infra\.env") do if /i "%%a"=="MAIN_FRONTEND_PORT" set "FRONTEND_PORT=%%b"
for /f "delims=" %%p in ("%FRONTEND_PORT%") do set "FRONTEND_PORT=%%p"
echo Opening http://localhost:%FRONTEND_PORT% in your default browser.
start "" "http://localhost:%FRONTEND_PORT%"
pause
