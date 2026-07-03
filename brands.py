"""Workspace-level brand kits: set colors/logo/voice/music once, reuse on
every video.

A brand is <data_dir>/brands/<id>/ holding brand.json (a partial project
config) plus optional logo/background/music files. Applying a brand copies
its files INTO the project dir and merges its config keys — projects stay
self-contained, so /media serving and the render pipeline are untouched.
"""
from __future__ import annotations
import json, os, re, shutil

# config keys a brand carries (files handled separately below)
BRAND_KEYS = [
    "title", "subtitle", "outro_title", "outro_subtitle", "intro_dur", "outro_dur",
    "brand_top", "brand_bottom", "logo_corner", "card_style", "card_top", "card_bottom",
    "frame_theme", "bg_style", "shadow", "radius", "padding", "vertical_stack",
    "browser_url", "transition", "voice", "lang", "music", "music_gain",
    "sfx_clicks", "sfx_zoom", "captions", "aspects", "glossary",
]
FILE_KEYS = ("logo", "background", "music")   # copied brand<->project by basename


def _root(data_dir):
    d = os.path.join(data_dir, "brands")
    os.makedirs(d, exist_ok=True)
    return d


def _bdir(data_dir, bid):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", bid or ""):
        raise ValueError("bad brand id")
    return os.path.join(_root(data_dir), bid)


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "brand").lower()).strip("-")[:40]
    return s or "brand"


def list_brands(data_dir) -> list:
    out = []
    for bid in sorted(os.listdir(_root(data_dir))):
        p = os.path.join(_root(data_dir), bid, "brand.json")
        if os.path.isfile(p):
            try:
                b = json.load(open(p, encoding="utf-8"))
                out.append({"id": bid, "name": b.get("name") or bid,
                            "brand_top": b.get("config", {}).get("brand_top"),
                            "brand_bottom": b.get("config", {}).get("brand_bottom"),
                            "has_logo": bool(b.get("config", {}).get("logo"))})
            except Exception:
                pass
    return out


def get_brand(data_dir, bid) -> dict:
    p = os.path.join(_bdir(data_dir, bid), "brand.json")
    if not os.path.isfile(p):
        raise FileNotFoundError(bid)
    return json.load(open(p, encoding="utf-8"))


def save_brand(data_dir, bid, name, config) -> dict:
    d = _bdir(data_dir, bid)
    os.makedirs(d, exist_ok=True)
    b = {"name": name or bid,
         "config": {k: v for k, v in (config or {}).items() if k in BRAND_KEYS or k in FILE_KEYS}}
    json.dump(b, open(os.path.join(d, "brand.json"), "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    return b


def create_brand(data_dir, name, config=None) -> str:
    base = _slug(name)
    bid, n = base, 2
    while os.path.exists(os.path.join(_root(data_dir), bid)):
        bid = f"{base}-{n}"; n += 1
    save_brand(data_dir, bid, name, config or {})
    return bid


def delete_brand(data_dir, bid):
    shutil.rmtree(_bdir(data_dir, bid), ignore_errors=True)


def _copy_in(src, dst_dir):
    """Copy a file into dst_dir (by basename); returns new path or None."""
    if src and os.path.isfile(src):
        dst = os.path.join(dst_dir, os.path.basename(src))
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy(src, dst)
        return dst
    return None


def apply_to_project(data_dir, bid, proj, cfgmod) -> dict:
    """Merge the brand into a project's config; brand files are copied into
    the project dir so it stays self-contained."""
    b = get_brand(data_dir, bid)
    bdir = _bdir(data_dir, bid)
    cfg = cfgmod.load(proj)
    bc = b.get("config") or {}
    for k in BRAND_KEYS:
        if k in bc:
            cfg[k] = bc[k]
    for k in FILE_KEYS:
        src = bc.get(k)
        if not src:
            continue
        if str(src).startswith(bdir + os.sep):          # brand-owned file -> copy in
            cfg[k] = _copy_in(src, proj) or cfg.get(k)
        else:                                            # shared asset (e.g. assets/music) -> keep path
            cfg[k] = src
    cfg["brand_id"] = bid
    return cfgmod.save(proj, cfg)


def from_project(data_dir, name, proj, cfgmod) -> str:
    """Save the project's current style as a new brand (files copied out)."""
    cfg = cfgmod.load(proj)
    bid = create_brand(data_dir, name)
    bdir = _bdir(data_dir, bid)
    bc = {k: cfg.get(k) for k in BRAND_KEYS if cfg.get(k) is not None}
    for k in FILE_KEYS:
        src = cfg.get(k)
        if src and str(src).startswith(os.path.abspath(proj) + os.sep):
            cp = _copy_in(src, bdir)
            if cp:
                bc[k] = cp
        elif src:
            bc[k] = src                                  # repo asset / default logo: reference as-is
    save_brand(data_dir, bid, name, bc)
    return bid


def seed_default(data_dir, assets_dir):
    """First run: ship EventHex as the example brand (the first client)."""
    if os.listdir(_root(data_dir)):
        return
    logo = os.path.join(assets_dir, "eventhex-logo-white.png")
    save_brand(data_dir, "eventhex", "EventHex (example)", {
        "title": "EventHex", "subtitle": "Product Demo",
        "outro_title": "EventHex", "outro_subtitle": "Thanks for watching",
        "brand_top": "#005DBC", "brand_bottom": "#081428",
        "card_style": "gradient", "frame_theme": "float", "bg_style": "gradient",
        "logo": logo if os.path.exists(logo) else None,
    })
