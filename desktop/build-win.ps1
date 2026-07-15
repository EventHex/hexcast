# Build the unsigned Windows desktop app. Run from desktop\ in PowerShell:
#   powershell -ExecutionPolicy Bypass -File build-win.ps1
# PyInstaller does not cross-compile — this must run ON Windows.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot                 # -> desktop\

Write-Host "==> Building the editor (React) first"
Push-Location ..\editor
npm install
npm run build
Pop-Location

Write-Host "==> Python packaging env"
# PyInstaller is happiest on Python 3.11/3.12.
python -m pip install --upgrade pip
python -m pip install pyinstaller
python -m pip install -r ..\requirements.txt
# Native window backend on Windows: pywebview drives the Edge WebView2 runtime
# through pythonnet (imported as `clr`). macOS uses pyobjc instead.
python -m pip install "pywebview>=5.0" "pythonnet>=3.0"

Write-Host "==> ffmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Warning "ffmpeg not on PATH - the app can't render without it. Install it (winget install Gyan.FFmpeg) or drop ffmpeg.exe at desktop\bin\ffmpeg.exe"
}

Write-Host "==> PyInstaller"
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist)  { Remove-Item -Recurse -Force dist }
pyinstaller hexcast.spec

Write-Host ""
Write-Host "Done. App: desktop\dist\HexCast\HexCast.exe"
Write-Host "Run it from a terminal the first time so boot logs are visible:"
Write-Host "    .\dist\HexCast\HexCast.exe"
Write-Host ""
Write-Host "The native window needs the Microsoft WebView2 Runtime."
Write-Host "  - Windows 11: preinstalled."
Write-Host "  - Windows 10: install the Evergreen runtime from"
Write-Host "    https://developer.microsoft.com/microsoft-edge/webview2/"
Write-Host "Without it, the app falls back to opening in the default browser."
