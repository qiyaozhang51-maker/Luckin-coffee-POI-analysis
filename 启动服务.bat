@echo off
cd /d "%~dp0"
title Starting Luckin Services...

REM Prefer the bundled portable Node.js (offline package) if present,
REM so the frontend can run even on machines without Node installed.
if exist "%~dp0deploy\node\node.exe" (
    set "PATH=%~dp0deploy\node;%PATH%"
)

echo.
echo ============================================
echo   Luckin Spatial Analysis - Starting All
echo ============================================
echo.

python manage.py start

echo.
echo ============================================
echo   Press any key to close this window.
echo ============================================
pause >nul
