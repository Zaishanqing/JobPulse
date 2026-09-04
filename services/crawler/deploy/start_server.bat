@echo off
cd /d "%~dp0.."

rem Local launcher: DB credentials must come from the environment; never
rem hardcode passwords in this repository.
if "%DB_PASSWORD%"=="" (
    echo [ERROR] DB_PASSWORD is not set. Set it in your environment before running. 1>&2
    exit /b 1
)
set DB_USER=root
python -m uvicorn unified_api.main:app --host 0.0.0.0 --port 8001
