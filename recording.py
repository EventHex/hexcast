"""Native screen / window recording for the desktop app.

Primary path: the bundled Swift ScreenCaptureKit helper (`hexcast-recorder`) —
it lets the user pick a whole screen OR a single window (and excludes HexCast's
own window), plus an optional mic, capturing straight into a project. This is
the payoff of the desktop app: no browser extension.

Fallback (helper missing, e.g. a dev checkout that hasn't compiled it): ffmpeg
avfoundation, whole-screen only. Both stop cleanly on SIGINT so the mp4 gets a
proper moov atom.

Device tokens are opaque strings the matching backend understands:
  helper : "screen:<displayID>", "window:<windowID>", "mic:<uniqueID>"
  ffmpeg : "avf-screen:<index>", "avf-mic:<index>"
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
            return {"screens": d.get("screens", []),
                    "windows": d.get("windows", []),
                    "mics": d.get("mics", [])}
        except Exception:
            pass  # fall back to ffmpeg enumeration
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
            _active.update(proc=proc, path=raw_path, started=time.time())
            return
        _ffmpeg_start(raw_path, target, mic, fps)


def stop() -> dict | None:
    with _lock:
        if not _active:
            return None
        proc, path = _active["proc"], _active["path"]
        # SIGINT -> the helper / ffmpeg finalizes the container (moov atom).
        try:
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
    _active.update(proc=proc, path=raw_path, started=time.time())
