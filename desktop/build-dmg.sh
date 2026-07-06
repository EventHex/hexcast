#!/usr/bin/env bash
# Build the unsigned macOS app AND wrap it in a drag-to-Applications .dmg.
#   cd desktop && ./build-dmg.sh
set -euo pipefail
cd "$(dirname "$0")"                      # -> desktop/
PY=${PYTHON:-python3.11}                  # PyInstaller-friendly (not 3.14)

echo "==> Python packaging venv ($($PY --version 2>&1))"
[ -d .pack ] || "$PY" -m venv .pack
# shellcheck disable=SC1091
source .pack/bin/activate
pip install --upgrade pip -q
pip install -q pyinstaller pywebview          # pywebview = native desktop window
pip install -q -r ../requirements.txt

echo "==> Editor build"
[ -d ../editor/dist ] || ( cd ../editor && npm install && npm run build )

command -v ffmpeg >/dev/null || echo "!! ffmpeg not on PATH — the app won't render. brew install ffmpeg"

echo "==> PyInstaller"
rm -rf build dist
pyinstaller --noconfirm hexcast.spec

APP="dist/HexCast.app"
[ -d "$APP" ] || { echo "BUILD FAILED: $APP not produced"; exit 1; }

echo "==> DMG (drag to Applications)"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f dist/HexCast.dmg
hdiutil create -volname "HexCast" -srcfolder "$STAGE" -ov -format UDZO dist/HexCast.dmg >/dev/null
rm -rf "$STAGE"

echo ""
echo "Done  ->  desktop/dist/HexCast.dmg"
echo "Open it, drag HexCast to Applications. First launch (unsigned):"
echo "  right-click HexCast > Open, or:  xattr -dr com.apple.quarantine /Applications/HexCast.app"
