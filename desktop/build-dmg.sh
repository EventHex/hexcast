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

echo "==> ScreenCaptureKit recorder (Swift)"
if xcrun --find swiftc >/dev/null 2>&1; then
  xcrun swiftc -O -target arm64-apple-macos14.0 \
    -framework ScreenCaptureKit -framework AVFoundation -framework CoreMedia \
    -framework CoreGraphics -framework AppKit \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker recorder/Info.plist \
    recorder/HexCastRecorder.swift -o bin/hexcast-recorder \
    && echo "   built bin/hexcast-recorder" \
    || echo "!! swiftc failed — window recording falls back to whole-screen ffmpeg"
else
  echo "!! swiftc not found — window recording falls back to whole-screen ffmpeg"
fi

echo "==> PyInstaller"
rm -rf build dist
pyinstaller --noconfirm hexcast.spec

APP="dist/HexCast.app"
[ -d "$APP" ] || { echo "BUILD FAILED: $APP not produced"; exit 1; }

echo "==> DMG (styled install window, drag to Applications)"
VOL="HexCast"
RW="dist/hexcast-rw.dmg"
MNT="/Volumes/$VOL"
rm -f "$RW" dist/HexCast.dmg
hdiutil detach "$MNT" >/dev/null 2>&1 || true
# writable image seeded with the app, then dressed up in Finder
hdiutil create -volname "$VOL" -srcfolder "$APP" -fs HFS+ -format UDRW -ov "$RW" >/dev/null
hdiutil attach "$RW" -noautoopen >/dev/null
mkdir -p "$MNT/.background"
cp assets/dmg-background.png "$MNT/.background/bg.png"
ln -s /Applications "$MNT/Applications"
# Finder view: background + icon positions matching the arrow (needs Automation
# permission; non-fatal so the build still yields a working DMG if it's denied).
osascript <<EOF 2>/dev/null || echo "   (Finder styling skipped — grant Automation permission for a prettier window)"
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {320, 140, 960, 610}
    set vopts to the icon view options of container window
    set arrangement of vopts to not arranged
    set icon size of vopts to 80
    set background picture of vopts to file ".background:bg.png"
    set position of item "HexCast.app" of container window to {180, 300}
    set position of item "Applications" of container window to {460, 300}
    update without registering applications
    delay 1
    close
  end tell
end tell
EOF
sync
hdiutil detach "$MNT" >/dev/null
hdiutil convert "$RW" -format UDZO -o dist/HexCast.dmg >/dev/null
rm -f "$RW"

echo ""
echo "Done  ->  desktop/dist/HexCast.dmg"
echo "Open it, drag HexCast to Applications. First launch (unsigned):"
echo "  right-click HexCast > Open, or:  xattr -dr com.apple.quarantine /Applications/HexCast.app"
