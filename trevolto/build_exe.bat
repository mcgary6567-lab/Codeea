@echo off
REM ============================================================
REM  Build Trevolto.exe on Windows (run this ON your PC).
REM  Produces dist\Trevolto.exe — a single, double-clickable file.
REM ============================================================
setlocal
cd /d "%~dp0"

where py >nul 2>&1 && (set "PYTHON=py -3") || (set "PYTHON=python")

echo [1/3] Installing dependencies + PyInstaller...
%PYTHON% -m pip install --upgrade --disable-pip-version-check pip
%PYTHON% -m pip install --disable-pip-version-check -r requirements.txt pyinstaller pillow

echo [2/3] Fetching brand logo + building the executable...
%PYTHON% fetch_logo.py
%PYTHON% -m PyInstaller --noconfirm --clean trading_bot.spec

echo [3/3] Done.
if exist "dist\Trevolto.exe" (
    echo.
    echo  SUCCESS  ->  dist\Trevolto.exe
    echo  Double-click it, or copy it anywhere. No Python needed to run it.
    echo.
) else (
    echo.
    echo  Build did not produce dist\Trevolto.exe — see the output above.
    echo.
)
pause
endlocal
