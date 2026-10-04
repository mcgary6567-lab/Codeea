<#
 Online Quran College - demo laptop installer (Windows 10/11, Windows PowerShell 5.1 or newer).
   powershell -ExecutionPolicy Bypass -File install.ps1            # install + seed demo data + start
   powershell -ExecutionPolicy Bypass -File install.ps1 -Core      # clean install (no demo families)
   powershell -ExecutionPolicy Bypass -File install.ps1 -NoStart   # install only
 Creates .venv, installs requirements, writes .env (SQLite), seeds the database, puts a shortcut on the
 Desktop that runs start.bat (which starts the server and opens the browser), then starts the server.
 The laptop keeps the seed sign-ins (admin@oqc.local / Admin@12345): deploy_secure.py is not run because
 the laptop is not public. To show it over a LAN run "python run.py --host 0.0.0.0" and keep APP_ENV=development.
#>
param([switch]$Core, [switch]$NoStart)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Find-Python {
    foreach ($spec in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3"), @("python", $null))) {
        $exe = $spec[0]; $arg = $spec[1]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $pyArgs = @(); if ($arg) { $pyArgs += $arg }
            $pyArgs += @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
            $ver = (& $exe @pyArgs 2>$null | Select-Object -First 1).Trim()
            if ($ver -match '^3\.(1[1-9]|[2-9]\d)$') { return @{ Exe = $exe; Arg = $arg; Version = $ver } }
        } catch { }
    }
    return $null
}

Write-Host "`n== Online Quran College - demo install ==" -ForegroundColor Cyan
$py = Find-Python
if (-not $py) {
    Write-Host "Python 3.11+ was not found." -ForegroundColor Yellow
    Write-Host "Install it with:  winget install -e --id Python.Python.3.12   (or from https://www.python.org/downloads/)"
    Write-Host "then run this script again."
    exit 1
}
Write-Host "Using Python $($py.Version)"

# 1. virtual environment
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..."
    if ($py.Arg) { & $py.Exe $py.Arg -m venv .venv } else { & $py.Exe -m venv .venv }
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# 2. dependencies
Write-Host "Installing requirements ..."
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements.txt

# 3. configuration (SQLite, local only)
if (-not (Test-Path ".env")) {
    Write-Host "Writing .env ..."
    $secret = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    $envText = (Get-Content .env.example -Raw) -replace 'SECRET_KEY=.*', "SECRET_KEY=$secret"
    # no BOM: pydantic-settings reads the file as UTF-8
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $envText, (New-Object Text.UTF8Encoding $false))
}

# 4. database + seed (idempotent; delete data\oqc.db to start over)
if (-not (Test-Path "data\oqc.db")) {
    if ($Core) { Write-Host "Creating the database (core data only) ..."; & $venvPy seed.py --core }
    else       { Write-Host "Creating the database and loading the demo dataset (1-2 minutes) ..."; & $venvPy seed.py }
} else {
    Write-Host "data\oqc.db exists - applying migrations only"
    & $venvPy -m alembic upgrade head
}

# 5. desktop shortcut -> start.bat (starts the server and opens the browser)
$desktop  = [Environment]::GetFolderPath("Desktop")
$shortcut = Join-Path $desktop "Online Quran College.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($shortcut)
$lnk.TargetPath       = Join-Path $PSScriptRoot "start.bat"
$lnk.WorkingDirectory = $PSScriptRoot
$lnk.IconLocation     = "$env:SystemRoot\System32\imageres.dll,109"
$lnk.Description      = "Start the Online Quran College platform (http://127.0.0.1:8000)"
$lnk.Save()
Write-Host "Desktop shortcut created: $shortcut"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Installed. Open http://127.0.0.1:8000"
Write-Host "  Sign in: admin@oqc.local  /  Admin@12345   (local demo defaults)"
if (-not $Core) { Write-Host "  Demo portals: teacher1@oqc.local / Teacher@123, parent1@oqc.local / Parent@123, student1@oqc.local / Student@123" }
Write-Host "  Start later from the Desktop shortcut 'Online Quran College'."
Write-Host "============================================================" -ForegroundColor Green

if (-not $NoStart) { Start-Process -FilePath (Join-Path $PSScriptRoot "start.bat") -WorkingDirectory $PSScriptRoot }
