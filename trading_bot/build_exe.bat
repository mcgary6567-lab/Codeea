@echo off
REM ============================================================
REM  Build PrometheusAICryptoBot.exe on Windows (run this ON your PC).
REM  Produces dist\PrometheusAICryptoBot.exe — a single, double-clickable file.
REM ============================================================
setlocal
cd /d "%~dp0"

where py >nul 2>&1 && (set "PYTHON=py -3") || (set "PYTHON=python")

echo [1/3] Installing dependencies + PyInstaller...
%PYTHON% -m pip install --upgrade --disable-pip-version-check pip
%PYTHON% -m pip install --disable-pip-version-check -r requirements.txt pyinstaller

echo [2/3] Building the executable (this can take a few minutes)...
%PYTHON% -m PyInstaller --noconfirm --clean trading_bot.spec

echo [3/3] Done.
if exist "dist\PrometheusAICryptoBot.exe" (
    echo.
    echo  SUCCESS  ->  dist\PrometheusAICryptoBot.exe
    echo  Double-click it, or copy it anywhere. No Python needed to run it.
    echo.
) else (
    echo.
    echo  Build did not produce dist\PrometheusAICryptoBot.exe — see the output above.
    echo.
)
pause
endlocal
