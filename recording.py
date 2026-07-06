"""Native screen + mic recording via the bundled ffmpeg (macOS avfoundation).

The whole point of the desktop app: capture straight into a project, no browser
extension. One recording at a time per process (the desktop app is single-user).
Screen capture needs macOS Screen Recording permission for the app — the first
capture triggers the system prompt.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time

_active: dict = {}
_lock = threading.Lock()


def list_devices() -> dict:
    """Parse avfoundation devices -> {screens:[{index,name}], mics:[{index,name}]}."""
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
            screens.append({"index": idx, "name": name})
        elif section == "a":
            mics.append({"index": idx, "name": name})
    return {"screens": screens, "mics": mics}


def is_recording() -> bool:
    return bool(_active)


def elapsed() -> float:
    return round(time.time() - _active["started"], 1) if _active else 0.0


def start(raw_path: str, screen_index: int, mic_index=None, fps: int = 30) -> None:
    with _lock:
        if _active:
            raise RuntimeError("already recording")
        audio = str(mic_index) if mic_index is not None else "none"
        spec = f"{screen_index}:{audio}"
        # avfoundation screen capture reports a bogus 1000k input tbr, so ffmpeg
        # duplicates frames wildly unless we pin a constant output rate. Without
        # this the encoder backs up and can't finalize on stop (no moov atom).
        cmd = ["ffmpeg", "-y", "-hide_banner", "-f", "avfoundation",
               "-capture_cursor", "1", "-framerate", str(fps), "-i", spec,
               "-fps_mode", "cfr", "-r", str(fps),
               "-c:v", "h264_videotoolbox", "-b:v", "6M", "-pix_fmt", "yuv420p"]
        if mic_index is not None:
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += [raw_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        # avfoundation fails fast if the screen permission is denied — surface that
        time.sleep(0.8)
        if proc.poll() is not None:
            err = (proc.stderr.read() or b"").decode("utf-8", "ignore")[-300:]
            raise RuntimeError("could not start capture — grant Screen Recording "
                               "permission to HexCast in System Settings. " + err)
        _active.update(proc=proc, path=raw_path, started=time.time())


def stop() -> dict | None:
    with _lock:
        if not _active:
            return None
        proc, path = _active["proc"], _active["path"]
        # SIGINT makes ffmpeg finalize the container (write the moov atom); a
        # plain terminate/kill leaves an unplayable file with no moov.
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        _active.clear()
        ok = os.path.exists(path) and os.path.getsize(path) > 0
        return {"ok": ok, "path": path}
