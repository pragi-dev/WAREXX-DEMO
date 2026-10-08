@echo off
REM The INTERACTIVE DEMO on Windows: the WAREXX software with a sample business
REM and no server or database behind it (see README.md in this folder).
REM Serves it on http://localhost:8002/ - the landing page's "Book a demo" form
REM opens this address in development (VITE_DEMO_URL, see .env.example).
REM
REM   run_demo_static.bat           build it if needed, then serve it
REM   run_demo_static.bat --build   rebuild first (after changing the demo or its data)
cd /d "%~dp0frontend"

if not exist "node_modules" (
  echo ==^> Installing
  call npm ci || exit /b 1
)
if "%1"=="--build" goto build
if exist "dist\demo\index.html" goto serve
:build
echo ==^> Building the demo
call npm run build:demo || exit /b 1

:serve
echo.
echo ==^> Demo on http://localhost:8002/   (Ctrl-C to stop)
call npm run preview:demo
