"""Click-driven zoom targets from the recorder's events.json.

The Chrome extension logs interaction events (click/scroll/move) with
timestamps in seconds relative to the recording start — the same clock as
whisper.json segment times. A click is ground truth for where the presenter
is working, so it beats the AI-vision grid guess whenever one exists.

click_targets(events_path, segs) -> list aligned to segs:
  {"zoom": True, "cx": 0..1, "cy": 0..1, "scale": float, "speed": 3,
   "reason": "click"}  for segments with a click, else None (caller decides
   whether to fall back to AI vision).
"""
from __future__ import annotations
import json, os

# clicks slightly BEFORE a narration segment usually cause what it describes
LEAD = 0.8      # seconds of look-behind
SCALE_ONE = 1.6  # single click: tight zoom on the target
SCALE_MANY = 1.4  # spread-out clicks: wider framing around their centroid


def _load_events(path):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    evs = data.get("events") or []
    out = []
    for e in evs:
        if (e.get("ty") or e.get("type")) != "click":
            continue
        try:
            t, x, y = float(e["t"]), float(e["x"]), float(e["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            out.append((t, x, y))
    out.sort()
    return out


def click_targets(events_path, segs):
    if not events_path or not os.path.exists(events_path):
        return [None] * len(segs)
    clicks = _load_events(events_path)
    if not clicks:
        return [None] * len(segs)
    out = []
    for s in segs:
        win = [(x, y) for t, x, y in clicks if s["start"] - LEAD <= t <= s["end"]]
        if not win:
            out.append(None)
            continue
        if len(win) == 1:
            cx, cy, scale = win[0][0], win[0][1], SCALE_ONE
        else:
            cx = sum(x for x, _ in win) / len(win)
            cy = sum(y for _, y in win) / len(win)
            spread = max(max(x for x, _ in win) - min(x for x, _ in win),
                         max(y for _, y in win) - min(y for _, y in win))
            scale = SCALE_ONE if spread < 0.15 else SCALE_MANY
        out.append({"zoom": True, "cx": round(min(1.0, max(0.0, cx)), 3),
                    "cy": round(min(1.0, max(0.0, cy)), 3),
                    "scale": scale, "speed": 3, "reason": "click"})
    return out
