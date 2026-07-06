#!/usr/bin/env bash
# Build the unsigned macOS desktop app. Run from the repo root or desktop/.
set -euo pipefail
cd "$(dirname "$0")"                      # -> desktop/

echo "==> Building the editor (React) first"
( cd ../editor && npm install && npm run build )

echo "==> Python packaging env"
# PyInstaller is happiest on Python 3.11/3.12. If you're on 3.13/3.14, create a
# 3.12 venv:  python3.12 -m venv .pack && source .pack/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller
python3 -m pip install -r ../requirements.txt

echo "==> ffmpeg"
command -v ffmpeg >/dev/null || { echo "!! ffmpeg not on PATH — install it (brew install ffmpeg) or place a static binary at desktop/bin/ffmpeg"; }

echo "==> PyInstaller"
rm -rf build dist
pyinstaller hexcast.spec

echo ""
echo "Done. App: desktop/dist/HexCast.app"
echo "Run:  open desktop/dist/HexCast.app     (or ./dist/HexCast/HexCast for logs)"
echo ""
echo "This build is UNSIGNED — Gatekeeper will warn. For testing, right-click the"
echo ".app > Open, or:  xattr -dr com.apple.quarantine desktop/dist/HexCast.app"
echo "Signing/notarization (later) needs an Apple Developer ID; see README.md."
