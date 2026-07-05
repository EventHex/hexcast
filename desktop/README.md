# Remaster — desktop app (unsigned build)

Packages the whole thing into a local app: it starts the FastAPI server on
`127.0.0.1:8765` and opens the editor in your browser. All video processing +
your API keys stay on your machine (model B). Accounts/usage can point at the
central control plane via `REMASTER_AUTH_URL`.

## Build (macOS)
```bash
cd desktop
./build-mac.sh
open dist/Remaster.app
```
User data lands in `~/Remaster/projects` (the app bundle is read-only).

> Use **Python 3.11 or 3.12** for packaging — PyInstaller support for 3.13/3.14
> is still catching up. Make a venv if your default is newer:
> `python3.12 -m venv .pack && source .pack/bin/activate` then run the script.

## How it works (the tricky bits)
- **Subprocess pipeline.** The server renders by spawning
  `[sys.executable, "pipeline/xxx.py", ...]`. In a frozen bundle `sys.executable`
  is the app binary, so `launcher.py` re-dispatches: if it's launched with a
  `.py` argument it runs that script (via `runpy`) instead of the server.
- **ffmpeg** is copied into the bundle's `bin/` and put first on `PATH`.
- **Local STT (faster-whisper/torch) is excluded** to keep the download small.
  The app uses cloud STT (Groq key) or you `pip install faster-whisper` into a
  side environment. Everything else (cloud TTS/LLM/vision, click-zooms, original
  voice) works fully.

## First build usually needs a tweak or two
PyInstaller can't always see every dynamic import. If a render fails with
`ModuleNotFoundError: X`, add `"X"` to `hiddenimports` in `remaster.spec` and
rebuild. Likely candidates: a provider SDK, `email.mime`, `encodings.idna`.
Run `./dist/Remaster/Remaster` (not the .app) to see the server log while testing.

## Signing + notarization (later — needs certs)
Unsigned apps trip Gatekeeper. To ship:
- **macOS**: Apple Developer ID ($99/yr) →
  `codesign --deep --force --options runtime --sign "Developer ID Application: …" dist/Remaster.app`
  then notarize with `xcrun notarytool submit`. Bundle ffmpeg must also be signed.
- **Windows**: a code-signing cert → `signtool sign` on the built `.exe`
  (build with `pyinstaller` on Windows; the spec is cross-platform, the `.app`
  wrapper is macOS-only).

## Point at the cloud (optional)
Set before launch to use the central control plane for accounts + usage:
```bash
REMASTER_AUTH_URL=https://remaster-central-xxxx.run.app open dist/Remaster.app
```
Unset → self-contained local accounts.
