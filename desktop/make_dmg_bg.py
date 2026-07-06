"""Generate the HexCast .dmg install-window background (drag-to-Applications).
Run from desktop/:  python3 make_dmg_bg.py
Writes assets/dmg-background.png at 2x (1200x800); the Finder window is 600x400,
so it renders crisp on retina. Icon slots: app at (150,236), Applications at
(450,236) in window coords — the arrow + caption sit between them.
"""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 800                      # 2x of the 600x400 Finder window
BG = (243, 240, 232)                  # warm oat paper
INK = (26, 24, 21)
DIM = (138, 129, 114)
ACCENT = (91, 110, 245)               # HexCast periwinkle

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def font(path_names, size):
    for p in path_names:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


serif = font(["/System/Library/Fonts/Supplemental/Palatino.ttc",
              "/System/Library/Fonts/Times.ttc"], 60)
sans = font(["/System/Library/Fonts/Helvetica.ttc",
             "/System/Library/Fonts/SFNS.ttf"], 30)
sans_sm = font(["/System/Library/Fonts/Helvetica.ttc"], 25)


def centered(text, y, fnt, fill):
    w = d.textlength(text, font=fnt)
    d.text(((W - w) / 2, y), text, font=fnt, fill=fill)


# brand mark (the icon-only logo), small, top-center
try:
    mark = Image.open("../HexCast/icon only logo.png").convert("RGBA")
    m = 150
    mark.thumbnail((m, m))
    img.paste(mark, (int((W - mark.width) / 2), 70), mark)
except Exception:
    pass

centered("Install HexCast", 250, serif, INK)
centered("Drag the app onto the Applications folder", 340, sans_sm, DIM)

# arrow between the two icon slots (window x 150 -> 450, *2 = 300 -> 900; y 236*2=472)
ay = 472
d.line([(430, ay), (770, ay)], fill=ACCENT, width=10)
d.polygon([(770, ay - 26), (830, ay), (770, ay + 26)], fill=ACCENT)

os.makedirs("assets", exist_ok=True)
img.save("assets/dmg-background.png")
print("wrote desktop/assets/dmg-background.png", img.size)
