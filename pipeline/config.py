"""Per-project config that drives the revoice pipeline + framing. Editable from
the web UI. Stored at <proj>/config.json; missing keys fall back to defaults."""
from __future__ import annotations
import os, json

DEFAULTS = {
    "title": "EventHex",
    "subtitle": "Product Demo",
    "outro_title": "EventHex",
    "outro_subtitle": "Thanks for watching",
    "intro_dur": 2.5,            # intro card length (seconds)
    "outro_dur": 2.5,            # outro card length (seconds)
    "brand_top": "#005DBC",      # gradient top
    "brand_bottom": "#081428",   # gradient bottom
    "logo": None,                # path to a logo PNG (transparent), optional
    "logo_corner": "tr",         # tl | tr | bl | br
    "voice": "en-IN-Chirp3-HD-Aoede",
    "zoom": True,                # global auto-zoom on/off
    "captions": True,            # burn narration captions onto the video
    "music": None,               # path to a background-music file, optional
    "music_gain": -14,           # music level in dB (beds are -14 LUFS; sits under the narration)
    "sfx_clicks": True,          # click sound at each recorded click (needs events.json)
    "sfx_zoom": False,           # whoosh at each zoom-in
    "aspects": ["16x9"],         # export sizes; add "9x16"/"1x1" in the editor
    "card_style": "gradient",    # intro/outro/scene card look: gradient | diagonal | radial | accent | minimal
    "card_top": None,            # card background top color; None = brand_top
    "card_bottom": None,         # card background bottom color; None = brand_bottom
    # --- visuals (P2) ---
    "radius": 24,                # window corner radius (px on canvas)
    "padding": None,             # None = auto per aspect; else 4..22 (% margin)
    "shadow": "medium",          # none | light | medium | heavy
    "background": None,          # path to a wallpaper image; else bg_style
    "frame_theme": "float",      # float | full | browser | split
    "bg_style": "gradient",      # gradient | mesh | noise (when no wallpaper)
    "vertical_stack": True,      # 9:16 uses the stacked layout (video top, brand below)
    "browser_url": None,         # text in the browser theme's URL pill
    # --- voice / narration ---
    "original_voice": False,     # keep the recorded audio instead of revoicing
    "transition": "none",        # scene transition: none | dissolve | slide
    "lang": "English",           # target narration language (translate + voice)
    "glossary": [
        "EventHex",
        "Trupeer (always spell it 'Trupeer' — mis-transcriptions like Trappia/Trappier/Dropear all mean Trupeer)",
        "Model Context Protocol (MCP)", "Chrome extension", "website builder", "AI wizard",
    ],
}


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_LOGO = os.path.join(_ROOT, "assets", "eventhex-logo-white.png")
if os.path.exists(_LOGO):
    DEFAULTS["logo"] = _LOGO


def load(proj):
    cfg = dict(DEFAULTS)
    p = f"{proj}/config.json"
    if os.path.exists(p):
        try:
            cfg.update(json.load(open(p)))
        except Exception:
            pass
    # tolerate stale absolute paths in configs written before the repo move:
    # missing logo -> default; builtin music -> remap by basename; else drop
    if cfg.get("logo") and not os.path.exists(cfg["logo"]):
        cfg["logo"] = DEFAULTS["logo"]
    if cfg.get("music") and not os.path.exists(cfg["music"]):
        alt = os.path.join(_ROOT, "assets", "music", os.path.basename(cfg["music"]))
        cfg["music"] = alt if os.path.exists(alt) else None
    if cfg.get("background") and not os.path.exists(cfg["background"]):
        cfg["background"] = None
    return cfg


def save(proj, cfg):
    merged = dict(DEFAULTS); merged.update(cfg or {})
    json.dump(merged, open(f"{proj}/config.json", "w"), indent=1, ensure_ascii=False)
    return merged


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def voiced_sig(segments, voice):
    """Fingerprint of what the rendered narration was voiced from. Stored in
    script.json by build_revoice; the fx-only fast path is refused when the
    current script text no longer matches (captions would desync from audio)."""
    import hashlib
    key = "|".join([voice or ""] + [(s.get("en") or "") for s in segments])
    return hashlib.sha1(key.encode()).hexdigest()[:12]
