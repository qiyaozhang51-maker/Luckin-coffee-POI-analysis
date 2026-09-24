@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   Luckin Spatial Analysis - Offline One-Click Deployment
echo ============================================================
echo.

REM ============================================================
REM  Step 1: Check Python 3.11
REM ============================================================
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.11 first:
    echo   https://www.python.org/downloads/release/python-3119/
    echo   IMPORTANT: check "Add python.exe to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo [OK] Python %PYVER% detected.

echo %PYVER% | findstr /b "3.11" >nul
if errorlevel 1 (
    echo [WARN] The offline wheels are built for Python 3.11, but you have %PYVER%.
    echo        Please install Python 3.11, or install deps online via:
    echo        python -m pip install -r deploy\requirements-runtime.txt
    echo.
)

REM ============================================================
REM  Step 2: Install backend Python dependencies (offline wheels)
REM ============================================================
echo.
echo [1/4] Installing backend Python dependencies (offline wheels)...
python -m pip install --no-index --find-links "%~dp0deploy\wheels" -r "%~dp0deploy\requirements-runtime.txt"
if errorlevel 1 (
    echo [WARN] Offline install failed. Trying online install as fallback...
    python -m pip install -r "%~dp0deploy\requirements-runtime.txt"
    if errorlevel 1 (
        echo [ERROR] Dependency install failed. Check your Python/pip.
        pause
        exit /b 1
    )
)

REM ============================================================
REM  Step 3: Check PostgreSQL 15 service
REM ============================================================
echo.
echo [2/4] Checking PostgreSQL 15 service...
sc query postgresql-x64-15 >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PostgreSQL 15 service "postgresql-x64-15" not found.
    echo   Please install PostgreSQL 15.8 first:
    echo   https://get.enterprisedb.com/postgresql/postgresql-15.8-1-windows-x64.exe
    echo   - Set superuser password to: your_db_password_here
    echo   - Port: 5432
    echo   Then install PostGIS (see deploy\postgis\ and README_部署说明.md).
    echo.
    pause
    exit /b 1
)
net start postgresql-x64-15 >nul 2>&1
echo [OK] PostgreSQL service is running.

REM ============================================================
REM  Step 4: Init database and restore backup
REM ============================================================
echo.
echo [3/4] Initializing database and restoring data (1-3 min)...
set "PGPASSWORD=your_db_password_here"
set "PSQL=C:\Program Files\PostgreSQL\15\bin\psql.exe"
if not exist "%PSQL%" (
    echo [ERROR] psql.exe not found at:
    echo   %PSQL%
    echo   Please edit this script and fix the PSQL path to your PostgreSQL.
    pause
    exit /b 1
)

"%PSQL%" -U postgres -h localhost -p 5432 -tAc "SELECT 1 FROM pg_roles WHERE rolname='luckin'" | findstr "1" >nul
if errorlevel 1 (
    echo   Creating role 'luckin' (superuser)...
    "%PSQL%" -U postgres -h localhost -p 5432 -c "CREATE ROLE luckin SUPERUSER LOGIN PASSWORD 'your_db_password_here';"
)

"%PSQL%" -U postgres -h localhost -p 5432 -tAc "SELECT 1 FROM pg_database WHERE datname='luckin_spatial'" | findstr "1" >nul
if errorlevel 1 (
    echo   Creating database 'luckin_spatial'...
    "%PSQL%" -U postgres -h localhost -p 5432 -c "CREATE DATABASE luckin_spatial OWNER luckin;"
)

"%PSQL%" -U luckin -h localhost -p 5432 -d luckin_spatial -tAc "SELECT to_regclass('public.stores')" | findstr "stores" >nul
if errorlevel 1 (
    echo   Restoring backup data (this may take 1-3 minutes)...
    "%PSQL%" -U luckin -h localhost -p 5432 -d luckin_spatial -f "%~dp0data\luckin_spatial_backup.sql"
    if errorlevel 1 (
        echo [ERROR] Database restore failed. See messages above.
        pause
        exit /b 1
    )
) else (
    echo   Database already contains data, skip restore.
)

echo.
echo [4/4] All components are ready.
echo.
echo ============================================================
echo   Installation complete!
echo   Now double-click 启动服务.bat to start the platform.
echo ============================================================
echo.
pause
