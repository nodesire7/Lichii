$ErrorActionPreference = 'Stop'
$launcher = $PSScriptRoot
$icon = Join-Path $launcher '..\dist\lichii.ico'
$python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
$oldPath = $env:Path
Push-Location $launcher
try {
    # Keep third-party ICU and other DLLs on the caller's PATH out of the package.
    $env:Path = "$(Split-Path -Parent $python);${env:SystemRoot}\System32;${env:SystemRoot}"
    & $python -m PyInstaller --noconfirm --clean --windowed --onefile --name Lichii-AFEI --icon $icon --add-data "$icon;." --add-data "afei-fritchii-zh_CN.qm;." launcher.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
} finally {
    $env:Path = $oldPath
    Pop-Location
}
