# PyInstaller spec for the Remaster desktop app (macOS .app / onedir).
# Build:  cd desktop && pyinstaller remaster.spec
#
# This bundles the FastAPI app + pipeline + editor build + assets + a copy of
# ffmpeg, and produces a launcher that also serves as the pipeline-subprocess
# interpreter (see launcher.py). Heavy optional local-STT deps are excluded to
# keep the download reasonable — the app uses cloud STT (Groq) or the user adds
# faster-whisper themselves.
import os, shutil
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(os.getcwd(), ".."))   # repo root (run from desktop/)

# --- bundle a copy of ffmpeg into bin/ ---
_ff = shutil.which("ffmpeg")
_ffbins = [(_ff, "bin")] if _ff else []
if not _ff:
    print("WARNING: ffmpeg not found on PATH — the app can't render without it. "
          "Install ffmpeg, or drop a static binary at desktop/bin/ffmpeg.")

# --- source + data the server reads at runtime ---
datas = [
    (os.path.join(ROOT, "app.py"), "."),
    (os.path.join(ROOT, "auth.py"), "."),
    (os.path.join(ROOT, "brands.py"), "."),
    (os.path.join(ROOT, "providers"), "providers"),
    (os.path.join(ROOT, "tools"), "tools"),
    (os.path.join(ROOT, "pipeline"), "pipeline"),
    (os.path.join(ROOT, "editor", "dist"), "editor/dist"),
    (os.path.join(ROOT, "assets"), "assets"),
]

hiddenimports = [
    "uvicorn", "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.lifespan.on",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.websockets_impl",
    "fastapi", "starlette", "dotenv", "multipart", "PIL", "requests",
    # app + pipeline modules imported dynamically / via sys.path
    "app", "auth", "brands", "config", "cerebras_clean", "zoom_decide",
    "timeline_fx", "fonts", "events_zoom",
    # native desktop window (pywebview + macOS WebKit backend)
    "webview", "webview.platforms.cocoa", "bottle", "proxy_tools",
    "objc", "Foundation", "AppKit", "WebKit", "Quartz", "Cocoa",
    "Security", "UniformTypeIdentifiers",
] + collect_submodules("providers") + collect_submodules("tools")

excludes = ["torch", "faster_whisper", "ctranslate2", "tkinter", "matplotlib", "numpy.tests"]

a = Analysis(["launcher.py"], pathex=[ROOT], binaries=_ffbins, datas=datas,
             hiddenimports=hiddenimports, excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Remaster",
          console=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Remaster")

# macOS .app wrapper
app = BUNDLE(coll, name="Remaster.app", icon=None, bundle_identifier="ai.eventhex.remaster",
             info_plist={"CFBundleShortVersionString": "0.1.0", "NSHighResolutionCapable": True})
