"""Bundled brand fonts (assets/fonts). One id -> (regular, bold) TTF pair.
Used by the card renderer, the frame exporter and the caption drawtext pass —
and mirrored by @font-face rules in the editor so preview == export.
"""
from __future__ import annotations
import os

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

FONTS = {
    "inter": ("inter-400.ttf", "inter-700.ttf"),
    "space-grotesk": ("space-grotesk-400.ttf", "space-grotesk-700.ttf"),
    "playfair-display": ("playfair-display-400.ttf", "playfair-display-700.ttf"),
    "jetbrains-mono": ("jetbrains-mono-400.ttf", "jetbrains-mono-700.ttf"),
}


def path(font_id, bold=False):
    """Absolute TTF path for a bundled font id, or None (caller falls back to
    the system-font hunt)."""
    pair = FONTS.get(font_id or "")
    if not pair:
        return None
    p = os.path.join(DIR, pair[1 if bold else 0])
    return p if os.path.exists(p) else None
