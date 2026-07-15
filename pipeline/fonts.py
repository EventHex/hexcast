"""Bundled brand fonts (assets/fonts). One id -> (regular, bold) TTF pair.
Used by the card renderer, the frame exporter and the caption drawtext pass —
and mirrored by @font-face rules in the editor so preview == export.
"""
from __future__ import annotations
import os
import sys

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

# System-font fallbacks per OS, tried when no bundled brand font is selected.
_SYS = {
    "darwin": {
        "broad": ["/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                  "/Library/Fonts/Arial Unicode.ttf"],
        "regular": ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf",
                    "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf"],
        "bold": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf"],
    },
    "win32": {
        "broad": [r"C:\Windows\Fonts\arialuni.ttf", r"C:\Windows\Fonts\seguisym.ttf",
                  r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"],
        "regular": [r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
                    r"C:\Windows\Fonts\tahoma.ttf"],
        "bold": [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"],
    },
    "linux": {
        "broad": ["/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
        "regular": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
        "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    },
}


def system_fallbacks(bold: bool = False, broad: bool = False) -> list[str]:
    """Ordered absolute font paths to try when no bundled brand font is set, for
    the current OS. `broad` prefers wide Unicode coverage (non-Latin captions).
    Always ends with a bundled brand TTF (ships in the app on every platform) so
    text never falls through to a bitmap default and ffmpeg drawtext on Windows
    always gets a real fontfile."""
    key = "darwin" if sys.platform == "darwin" else ("win32" if os.name == "nt" else "linux")
    tbl = _SYS[key]
    out = list(tbl["broad"]) if broad else []
    out += list(tbl["bold"] if bold else tbl["regular"])
    out.append(os.path.join(DIR, "inter-700.ttf" if bold else "inter-400.ttf"))
    return out

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
