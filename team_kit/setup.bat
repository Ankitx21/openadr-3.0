@echo off
echo ============================================================
echo   Grid-Interactive Competition: OpenADR 3.0 Setup
echo ============================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.8+ from https://www.python.org/
    pause
    exit /b 1
)

echo [1/2] Checking Python environment...
python -c "import sys; print('Using Python ' + sys.version.split()[0])"

echo.
echo [2/2] Installing competition requirements (requests, pandas)...
pip install -r requirements.txt

echo.
echo ============================================================
echo   Setup Complete!
echo   To launch the team client, run:
echo      python openadr3_client.py
echo ============================================================
pause
