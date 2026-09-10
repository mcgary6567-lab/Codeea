@echo off
REM Online Quran College - Digital Operating System (Windows launcher)
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)

if not exist ".env" copy .env.example .env >nul

if not exist "data\oqc.db" (
  echo Creating database and loading demo data...
  .venv\Scripts\python.exe seed.py
)

echo.
echo ============================================================
echo   Online Quran College - Digital Operating System
echo   http://localhost:8000   or   http://127.0.0.1:8000
echo   Sign in: admin@oqc.local  /  Admin@12345
echo ============================================================
echo.
echo Opening your browser in a few seconds...

REM Open the browser once the server is accepting connections.
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start "" http://127.0.0.1:8000/login"

.venv\Scripts\python.exe run.py
