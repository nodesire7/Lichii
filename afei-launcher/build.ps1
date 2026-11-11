param([string]$QtDir = $env:QT_ROOT_DIR)

$ErrorActionPreference = 'Stop'
$launcher = $PSScriptRoot
$icon = Join-Path $launcher '..\dist\lichii.ico'
$python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
$cmake = (Get-Command cmake.exe -CommandType Application -All | Where-Object { $_.Source -notmatch '[\\/]msys' } | Select-Object -First 1).Source
if (-not $QtDir -or -not (Test-Path -LiteralPath (Join-Path $QtDir 'lib/cmake/Qt6/Qt6Config.cmake'))) {
    throw 'Use -QtDir with the Qt 6.8.3 MSVC 2022 x64 installation directory.'
}
$overlayBuild = Join-Path $launcher 'build/qt-overlay'
& $cmake -S (Join-Path $launcher 'qt-overlay') -B $overlayBuild -G 'Visual Studio 17 2022' -A x64 "-DCMAKE_PREFIX_PATH=$QtDir"
if ($LASTEXITCODE -ne 0) { throw 'Could not configure the Qt translation plugin' }
& $cmake --build $overlayBuild --config Release --parallel
if ($LASTEXITCODE -ne 0) { throw 'Could not build the Qt translation plugin' }
$overlayDll = Join-Path $overlayBuild 'generic/afei-translation.dll'
$oldPath = $env:Path
Push-Location $launcher
try {
    # Keep third-party ICU and other DLLs on the caller's PATH out of the package.
    $env:Path = "$(Split-Path -Parent $python);${env:SystemRoot}\System32;${env:SystemRoot}"
    & $python -m PyInstaller --noconfirm --clean --windowed --onefile --name Lichii-AFEI --icon $icon --add-data "$icon;." --add-data "afei-fritchii-zh_CN.qm;." --add-data "fritchii-v5.4-notes.zh_CN.md;." --add-data "$overlayDll;generic" launcher.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
} finally {
    $env:Path = $oldPath
    Pop-Location
}
