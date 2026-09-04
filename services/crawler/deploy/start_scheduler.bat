@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0.."

rem Local launcher: DB credentials must come from the environment; never
rem hardcode passwords in this repository.
if "%MYSQL_PASSWORD%"=="" (
    echo [ERROR] MYSQL_PASSWORD is not set. Set it in your environment before running. 1>&2
    exit /b 1
)

set DB_USER=root
set DB_HOST=127.0.0.1
set DB_PORT=3306
set DB_NAME=recruitment_analysis
set MYSQL_HOST=127.0.0.1
set MYSQL_PORT=3306
set MYSQL_USER=root
set MYSQL_DATABASE=recruitment_analysis
set CRAWLER_LEASE_SECONDS=5400
set CRAWLER_MAX_RETRIES=2

python -m deploy.scheduler
