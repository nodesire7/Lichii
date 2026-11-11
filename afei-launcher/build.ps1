$ErrorActionPreference = 'Stop'
$launcher = $PSScriptRoot
$icon = Join-Path $launcher '..\dist\lichii.ico'
Push-Location $launcher
try {
    python -m PyInstaller --noconfirm --clean --windowed --onefile --name Lichii-AFEI --icon $icon --add-data "$icon;." launcher.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
} finally {
    Pop-Location
}
