@echo off
REM ============================================================
REM  Windows launcher for the TradingView Trading Bot.
REM  Double-click this file to install deps (first run) and start.
REM ============================================================
setlocal

cd /d "%~dp0"

REM Find a usable Python (py launcher preferred on modern Windows).
where py >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    set "PYTHON=python"
)

echo Checking dependencies...
%PYTHON% -m pip install --quiet --disable-pip-version-check -r requirements.txt

echo Starting Trading Bot...
%PYTHON% main.py

if %errorlevel% neq 0 (
    echo.
    echo The bot exited with an error. See the message above.
    pause
)
endlocal
