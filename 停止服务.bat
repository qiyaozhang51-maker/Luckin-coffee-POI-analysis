@echo off
cd /d "%~dp0"
title Stopping Luckin Services...

echo.
echo ============================================
echo   Luckin Spatial Analysis - Stopping All
echo ============================================
echo.

python manage.py stop

echo.
echo ============================================
echo   Press any key to close this window.
echo ============================================
pause >nul
