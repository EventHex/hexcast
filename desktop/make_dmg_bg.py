"""Generate the HexCast .dmg install-window background (drag-to-Applications).
Run from desktop/:  python3 make_dmg_bg.py

Finder draws a DMG background at 1 image-pixel = 1 window-POINT (it does not
scale for the window or retina), so the image size *is* the window size. We use
640x450 and lay the two icon slots + arrow in a lower band so the real app /
Applications icons (positioned by build-dmg.sh) don't collide with the text.
"""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 640, 450
BG = (243, 240, 232)
INK = (26, 24, 21)
DIM = (122, 113, 98)
ACCENT = (91, 110, 245)

# icon slots (must match the Finder positions in build-dmg.sh)
LEFT_X, RIGHT_X, ICON_Y = 180, 460, 300

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def font(names, size):
    for p in names:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


serif = font(["/System/Library/Fonts/Supplemental/Palatino.ttc",
              "/System/Library/Fonts/Times.ttc"], 34)
sans = font(["/System/Library/Fonts/Helvetica.ttc"], 15)


def centered(text, y, fnt, fill):
    w = d.textlength(text, font=fnt)
    d.text(((W - w) / 2, y), text, font=fnt, fill=fill)


# brand mark, top-center
try:
    mark = Image.open("../HexCast/icon only logo.png").convert("RGBA")
    mark.thumbnail((66, 66))
    img.paste(mark, (int((W - mark.width) / 2), 30), mark)
except Exception:
    pass

centered("Install HexCast", 112, serif, INK)
centered("Drag the app onto the Applications folder", 158, sans, DIM)

# arrow in the icon band, pointing from the app slot toward Applications
# (stops short of both 80px icons: left icon right-edge ~220, Apps left-edge ~420)
ax0, ax1 = 250, 392
d.line([(ax0, ICON_Y), (ax1, ICON_Y)], fill=ACCENT, width=6)
d.polygon([(ax1, ICON_Y - 13), (ax1 + 26, ICON_Y), (ax1, ICON_Y + 13)], fill=ACCENT)

os.makedirs("assets", exist_ok=True)
img.save("assets/dmg-background.png")
print("wrote desktop/assets/dmg-background.png", img.size)
