[CmdletBinding()]
param(
    [switch]$OneDir,
    [switch]$SkipTests,
    [switch]$Console
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = $PSScriptRoot
$BuildVenv = Join-Path $ProjectRoot ".build-venv"
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"

Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath $BuildPython)) {
        Write-Host "Erstelle isolierte Build-Umgebung in .build-venv ..."
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3 -m venv $BuildVenv
        }
        elseif (Get-Command python -ErrorAction SilentlyContinue) {
            & python -m venv $BuildVenv
        }
        else {
            throw "Python 3 wurde nicht gefunden. Bitte Python 3.10 oder neuer installieren."
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Die Build-Umgebung konnte nicht erstellt werden."
        }
    }

    Write-Host "Installiere Build-Abhaengigkeiten ..."
    & $BuildPython -m pip install --disable-pip-version-check -r requirements.txt -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Die Abhaengigkeiten konnten nicht installiert werden."
    }

    if (-not $SkipTests) {
        Write-Host "Fuehre Tests aus ..."
        & $BuildPython -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw "Die Tests sind fehlgeschlagen; die EXE wurde nicht gebaut."
        }
    }

    $BundleMode = if ($OneDir) { "--onedir" } else { "--onefile" }
    $ConsoleMode = if ($Console) { "--console" } else { "--windowed" }
    $PyInstallerArgs = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        $BundleMode,
        $ConsoleMode,
        "--name", "lolbuddy",
        "--add-data", "${ProjectRoot}\lolbuddy\templates;lolbuddy\templates",
        "--add-data", "${ProjectRoot}\lolbuddy\static;lolbuddy\static",
        "${ProjectRoot}\app.py"
    )

    Write-Host "Baue lolbuddy.exe ..."
    & $BuildPython @PyInstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller konnte die EXE nicht erstellen."
    }

    if ($OneDir) {
        $Result = Join-Path $ProjectRoot "dist\lolbuddy\lolbuddy.exe"
    }
    else {
        $Result = Join-Path $ProjectRoot "dist\lolbuddy.exe"
    }

    $LegacyState = Join-Path $ProjectRoot ".lolbuddy-rune-page.json"
    if ($env:LOCALAPPDATA -and (Test-Path -LiteralPath $LegacyState)) {
        $StateDirectory = Join-Path $env:LOCALAPPDATA "lolbuddy"
        $PersistentState = Join-Path $StateDirectory "rune-page.json"
        if (-not (Test-Path -LiteralPath $PersistentState)) {
            New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
            Copy-Item -LiteralPath $LegacyState -Destination $PersistentState
            Write-Host "Bestehende Runenseiten-ID wurde nach $PersistentState uebernommen."
        }
    }

    Write-Host ""
    Write-Host "Fertig: $Result" -ForegroundColor Green
}
finally {
    Pop-Location
}
