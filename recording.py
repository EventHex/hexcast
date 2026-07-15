"""Native screen / window recording for the desktop app.

Primary path: the bundled Swift ScreenCaptureKit helper (`hexcast-recorder`) —
it lets the user pick a whole screen OR a single window (and excludes HexCast's
own window), plus an optional mic, capturing straight into a project. This is
the payoff of the desktop app: no browser extension.

Fallback (helper missing, e.g. a dev checkout that hasn't compiled it): ffmpeg
avfoundation on macOS, whole-screen only. Both stop cleanly on SIGINT so the
mp4 gets a proper moov atom.

Windows: there is no ScreenCaptureKit, so recording runs through ffmpeg's
gdigrab (whole screen or a single window by title) plus dshow for the mic.
ffmpeg is finalized by writing "q" to its stdin (SIGINT isn't deliverable to a
child on Windows), which still yields a clean moov atom.

Device tokens are opaque strings the matching backend understands:
  helper  : "screen:<displayID>", "window:<windowID>", "mic:<uniqueID>"
  avf     : "avf-screen:<index>", "avf-mic:<index>"
  gdigrab : "gdi-screen:desktop", "gdi-window:<title>", "dshow-mic:<name>"
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time

IS_WIN = os.name == "nt"

_active: dict = {}
_lock = threading.Lock()


def _helper() -> str | None:
    """Locate the ScreenCaptureKit helper binary, or None to use ffmpeg."""
    p = os.environ.get("HEXCAST_RECORDER")
    if p and os.path.exists(p):
        return p
    w = shutil.which("hexcast-recorder")
    if w:
        return w
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(here, "bin", "hexcast-recorder"),
              os.path.join(here, "desktop", "bin", "hexcast-recorder")):
        if os.path.exists(c):
            return c
    return None


def list_devices() -> dict:
    """-> {screens:[{index,name}], windows:[{index,name}], mics:[{index,name}]}.
    `index` is the opaque token to pass back to start()."""
    h = _helper()
    if h:
        try:
            p = subprocess.run([h, "list"], capture_output=True, text=True, timeout=15)
            d = json.loads(p.stdout)
            return {"permission": d.get("permission", True),
                    "screens": d.get("screens", []),
                    "windows": d.get("windows", []),
                    "mics": d.get("mics", [])}
        except Exception:
            pass  # fall back to ffmpeg enumeration
    if IS_WIN:
        return _win_list()
    return _ffmpeg_list()


def is_recording() -> bool:
    return bool(_active)


def elapsed() -> float:
    return round(time.time() - _active["started"], 1) if _active else 0.0


def start(raw_path: str, target: str, mic=None, fps: int = 30) -> None:
    with _lock:
        if _active:
            raise RuntimeError("already recording")
        target = str(target or "")
        h = _helper()
        if h and (target.startswith("screen:") or target.startswith("window:")):
            cmd = [h, "record", "--target", target, "--out", raw_path, "--fps", str(fps)]
            if mic and str(mic).startswith("mic:"):
                cmd += ["--mic", str(mic)]
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # the helper prints {"recording":true} once capture actually begins;
            # anything else means it failed (usually a permission denial).
            line = proc.stdout.readline().decode("utf-8", "ignore")
            if '"recording"' not in line:
                err = (proc.stderr.read() or b"").decode("utf-8", "ignore")[-300:]
                try:
                    proc.kill()
                except Exception:
                    pass
                raise RuntimeError("could not start capture — grant Screen Recording "
                                   "permission to HexCast in System Settings. " + err)
            _active.update(proc=proc, path=raw_path, started=time.time(), stop="sigint")
            return
        if IS_WIN:
            _win_start(raw_path, target, mic, fps)
            return
        _ffmpeg_start(raw_path, target, mic, fps)


def stop() -> dict | None:
    with _lock:
        if not _active:
            return None
        proc, path = _active["proc"], _active["path"]
        # Ask the child to finalize the container (write the moov atom):
        #  - ffmpeg on Windows: SIGINT can't be delivered to a child, so send
        #    "q" on stdin, which ffmpeg treats as a graceful quit.
        #  - helper / avfoundation ffmpeg on macOS: SIGINT.
        try:
            if _active.get("stop") == "q" and proc.stdin:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            else:
                proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        _active.clear()
        ok = os.path.exists(path) and os.path.getsize(path) > 0
        return {"ok": ok, "path": path}


# ---- ffmpeg avfoundation fallback (whole-screen only) --------------------

def _ffmpeg_list() -> dict:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation",
                        "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True)
    screens, mics, section = [], [], None
    for line in p.stderr.splitlines():
        low = line.lower()
        if "video devices" in low:
            section = "v"; continue
        if "audio devices" in low:
            section = "a"; continue
        m = re.search(r"\]\s*\[(\d+)\]\s+(.+?)\s*$", line)
        if not m:
            continue
        idx, name = int(m.group(1)), m.group(2).strip()
        if section == "v" and "screen" in name.lower():
            screens.append({"index": f"avf-screen:{idx}", "name": name})
        elif section == "a":
            mics.append({"index": f"avf-mic:{idx}", "name": name})
    return {"screens": screens, "windows": [], "mics": mics}


def _ffmpeg_start(raw_path: str, target: str, mic, fps: int) -> None:
    if not target.startswith("avf-screen:"):
        raise RuntimeError("window capture needs the ScreenCaptureKit helper (not bundled)")
    screen_index = target.split(":", 1)[1]
    audio = mic.split(":", 1)[1] if (mic and str(mic).startswith("avf-mic:")) else "none"
    spec = f"{screen_index}:{audio}"
    # avfoundation screen capture reports a bogus 1000k tbr — pin a constant
    # output rate or ffmpeg duplicates frames and can't finalize on stop.
    cmd = ["ffmpeg", "-y", "-hide_banner", "-f", "avfoundation",
           "-capture_cursor", "1", "-framerate", str(fps), "-i", spec,
           "-fps_mode", "cfr", "-r", str(fps),
           "-c:v", "h264_videotoolbox", "-b:v", "6M", "-pix_fmt", "yuv420p"]
    if audio != "none":
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += [raw_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(0.8)
    if proc.poll() is not None:
        err = (proc.stderr.read() or b"").decode("utf-8", "ignore")[-300:]
        raise RuntimeError("could not start capture — grant Screen Recording "
                           "permission to HexCast in System Settings. " + err)
    _active.update(proc=proc, path=raw_path, started=time.time(), stop="sigint")


# ---- Windows: ffmpeg gdigrab (screen or window) + dshow mic ---------------

def _win_windows() -> list[str]:
    """Visible top-level window titles, HexCast's own window excluded. gdigrab
    captures a window by its title, so the title is the device token."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    titles: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        t = (buf.value or "").strip()
        if t and t.lower() != "program manager" and "hexcast" not in t.lower():
            titles.append(t)
        return True

    user32.EnumWindows(_cb, 0)
    seen, out = set(), []
    for t in titles:                       # dedupe, keep first-seen order
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _dshow_mics() -> list[dict]:
    """Audio capture devices from ffmpeg's DirectShow enumeration."""
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-f", "dshow",
                            "-list_devices", "true", "-i", "dummy"],
                           capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    mics, section = [], None
    for line in p.stderr.splitlines():
        low = line.lower()
        if "audio devices" in low:
            section = "a"; continue
        if "video devices" in low:
            section = "v"; continue
        if "alternative name" in low:      # skip the @device_cm_… alias line
            continue
        m = re.search(r'"([^"]+)"', line)
        if m and section == "a":
            name = m.group(1)
            mics.append({"index": f"dshow-mic:{name}", "name": name})
    return mics


def _win_list() -> dict:
    return {"permission": True,
            "screens": [{"index": "gdi-screen:desktop", "name": "Entire screen"}],
            "windows": [{"index": f"gdi-window:{t}", "name": t} for t in _win_windows()],
            "mics": _dshow_mics()}


def _win_start(raw_path: str, target: str, mic, fps: int) -> None:
    if target.startswith("gdi-window:"):
        src = "title=" + target.split(":", 1)[1]
    else:                                  # gdi-screen:desktop
        src = "desktop"
    use_mic = bool(mic and str(mic).startswith("dshow-mic:"))
    # gdigrab has no hardware encoder guarantee -> libx264 (in the essentials
    # build). "q" on stdin later finalizes the file, so keep stdin a PIPE.
    cmd = ["ffmpeg", "-y", "-hide_banner", "-f", "gdigrab",
           "-framerate", str(fps), "-draw_mouse", "1", "-i", src]
    if use_mic:
        cmd += ["-f", "dshow", "-i", "audio=" + str(mic).split(":", 1)[1]]
    cmd += ["-fps_mode", "cfr", "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", "6M", "-pix_fmt", "yuv420p"]
    if use_mic:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += [raw_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    time.sleep(1.0)
    if proc.poll() is not None:
        err = (proc.stderr.read() or b"").decode("utf-8", "ignore")[-400:]
        raise RuntimeError("could not start screen capture. " + err)
    _active.update(proc=proc, path=raw_path, started=time.time(), stop="q")
