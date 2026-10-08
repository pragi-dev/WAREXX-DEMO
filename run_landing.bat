@echo off
REM The PUBLIC LANDING PAGE on Windows, with its lead API, for development:
REM   http://localhost:5174/      the landing page
REM   http://127.0.0.1:8003/      the lead API its forms post to (emails saved
REM                               as .eml files under lead-api\outbox unless
REM                               LEAD_MAIL_BACKEND=smtp is set in .env)
REM Neither touches the WAREXX app, its database or its API.
cd /d "%~dp0"

REM Server-side settings (LEAD_*) from landing-demo\.env, if there is one.
if exist "%~dp0.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0.env") do (
    if not "%%A"=="" if not defined %%A set "%%A=%%B"
  )
)
set "PY=python"
where python >nul 2>nul || set "PY=py"
start "WAREXX lead API" %PY% "%~dp0lead-api\server.py"

cd /d "%~dp0frontend"
if not exist "node_modules" (
  echo ==^> Installing
  call npm ci || exit /b 1
)
call npm run dev:landing
