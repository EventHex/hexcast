"""Visual-frame + export: place the revoiced recording on a themed frame and
export the configured aspect ratios.

Themes (config `frame_theme`):
  float    — recording floats on a brand plate (rounded corners + shadow)
  full     — full-bleed; aspect mismatch fills edges with a blurred copy
  browser  — float + a browser chrome bar (traffic lights, URL pill)
  split    — recording docked right, brand panel (logo/title) on the left
  stack    — 9:16 layout: video on top, logo + title below (auto-picked for
             9:16 when `vertical_stack` is on and the theme is plate-based)

Backgrounds (config `bg_style`, when no wallpaper image is set):
  gradient | mesh | noise

Brand colors / logo / theme come from <proj>/config.json (web UI editable).
Usage: python3 lib/revoice/polish_export.py <project_dir>
"""
from __future__ import annotations
import os, sys, subprocess
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import config

PROJ = sys.argv[1] if len(sys.argv) > 1 else "projects/demo-revoice-test"
SRC = f"{PROJ}/revoiced.mp4"
ASSETS = f"{PROJ}/frame_assets"
os.makedirs(ASSETS, exist_ok=True)
CFG = config.load(PROJ)
RADIUS = int(CFG.get("radius", 24) or 24)
TOP = config.hex_rgb(CFG["brand_top"]); BOT = config.hex_rgb(CFG["brand_bottom"])
CANVAS = {"16x9": (1920, 1080), "9x16": (1080, 1920), "1x1": (1080, 1080)}
# shadow preset -> (alpha, gaussian blur, y-offset)
SHADOW = {"none": None, "light": (70, 20, 14), "medium": (130, 34, 22), "heavy": (185, 50, 32)}.get(CFG.get("shadow", "medium"), (130, 34, 22))
BG_IMG = CFG.get("background")
THEME = (CFG.get("frame_theme") or "float").lower()
BG_STYLE = (CFG.get("bg_style") or "gradient").lower()
VSTACK = CFG.get("vertical_stack", True)
LOGO = CFG.get("logo")


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


class _R:
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


def ff_progress(args, total_s, base=0.0, span=1.0):
    """ffmpeg with real progress -> '@@P <global_fraction>' on stdout."""
    import threading
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
                             "-progress", "pipe:1", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err = []
    threading.Thread(target=lambda: err.append(proc.stderr.read() or ""), daemon=True).start()
    last = -1.0
    for line in proc.stdout:
        if line.startswith("out_time_us="):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            frac = min(1.0, (us / 1e6) / total_s) if total_s > 0 else 0.0
            g = base + span * frac
            if g - last >= 0.01:
                last = g
                print(f"@@P {g:.4f}", flush=True)
    proc.wait()
    return _R(proc.returncode, "", "".join(err))


def _has_vtb():
    try:
        return "h264_videotoolbox" in subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    except Exception:
        return False


# Apple Silicon media engine: 3-5x faster than CPU libx264. Set REVOICE_ENC=x264 to force software.
USE_VTB = os.environ.get("REVOICE_ENC", "vtb") != "x264" and _has_vtb()


def venc(bitrate="12M"):
    if USE_VTB:
        return ["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-allow_sw", "1",
                "-realtime", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def dur(path):
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path).stdout.strip() or 0)


def src_size():
    out = sh("ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", SRC).stdout.strip()
    w, h = out.split("x"); return int(w), int(h)


def find_font(size, bold=True):
    import fonts as _fonts
    p = _fonts.path(CFG.get("font"), bold=bold)
    if p:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    cands = (["/System/Library/Fonts/Supplemental/Arial Bold.ttf"] if bold else []) + [
        "/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf"]
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _mix(c, w, t):
    return tuple(int(c[i] + (w[i] - c[i]) * t) for i in range(3))


def background(CW, CH):
    """Canvas background: wallpaper image > bg_style (gradient/mesh/noise)."""
    if BG_IMG and os.path.exists(BG_IMG):
        bg = Image.open(BG_IMG).convert("RGB")
        scale = max(CW / bg.width, CH / bg.height)
        bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1), Image.LANCZOS)
        return bg.crop(((bg.width - CW) // 2, (bg.height - CH) // 2,
                        (bg.width - CW) // 2 + CW, (bg.height - CH) // 2 + CH)).convert("RGBA")
    if BG_STYLE == "mesh":
        # soft color-blob mesh: paint blurred brand-color circles on a dark base
        s = 4  # work at 1/4 res, blur, upscale — fast and smooth
        w, h = CW // s, CH // s
        img = Image.new("RGB", (w, h), _mix(BOT, (0, 0, 0), 0.25))
        d = ImageDraw.Draw(img)
        blobs = [(0.16, 0.18, 0.55, TOP), (0.88, 0.10, 0.45, _mix(TOP, (255, 255, 255), 0.35)),
                 (0.55, 0.95, 0.65, _mix(TOP, BOT, 0.4)), (0.02, 0.85, 0.40, _mix(BOT, TOP, 0.55))]
        for bx, by, br, c in blobs:
            r = int(br * w)
            d.ellipse([bx * w - r, by * h - r, bx * w + r, by * h + r], fill=c)
        img = img.filter(ImageFilter.GaussianBlur(w // 7))
        return img.resize((CW, CH), Image.LANCZOS).convert("RGBA")
    # linear gradient base (also the base for noise)
    img = Image.new("RGB", (CW, CH))
    px = img.load()
    for y in range(CH):
        c = _lerp(TOP, BOT, y / CH)
        for x in range(CW):
            px[x, y] = c
    if BG_STYLE == "noise":
        n = Image.effect_noise((CW, CH), 34).point(lambda v: min(255, max(0, v)))
        img = Image.composite(Image.new("RGB", (CW, CH), (255, 255, 255)), img,
                              n.point(lambda v: int(abs(v - 128) * 0.22)))
    return img.convert("RGBA")


def add_shadow_card(img, x0, y0, x1, y1, rad):
    if SHADOW:
        a, blur, off = SHADOW
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(lay).rounded_rectangle([x0, y0 + off, x1, y1 + off], radius=rad, fill=(0, 0, 0, a))
        img = Image.alpha_composite(img, lay.filter(ImageFilter.GaussianBlur(blur)))
    card = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], radius=rad, fill=(255, 255, 255, 255))
    return Image.alpha_composite(img, card)


def add_logo(img, CW, CH, WX, WY, WW, WH):
    """Logo pinned to a corner, sized to the FREE band above/below the window so
    it can never overlap the recording."""
    if not (LOGO and os.path.exists(LOGO)):
        return img
    try:
        lg = Image.open(LOGO).convert("RGBA")
        m = int(CW * 0.02)
        corner = CFG.get("logo_corner", "tr")
        band = (WY if "t" in corner else CH - (WY + WH)) - 2 * m
        side = (WX if "l" in corner else CW - (WX + WW)) - 2 * m
        if band >= 26:      # room above/below the window: logo can span wide
            lg.thumbnail((int(CW * 0.14), band), Image.LANCZOS)
            lx = m if "l" in corner else CW - lg.width - m
            ly = m + (band - lg.height) // 2 if "t" in corner else CH - m - band + (band - lg.height) // 2
        elif side >= 56:    # window is height-limited: use the side margin
            lg.thumbnail((side, int(CH * 0.10)), Image.LANCZOS)
            lx = m + (side - lg.width) // 2 if "l" in corner else WX + WW + m + (side - lg.width) // 2
            ly = m if "t" in corner else CH - lg.height - m
        else:               # no room anywhere — drop the logo rather than overlap
            return img
        img.alpha_composite(lg, (int(lx), int(ly)))
    except Exception:
        pass
    return img


def fit_window(CW, CH, ar, margin_frac):
    avail_w = int(CW * (1 - margin_frac)); avail_h = int(CH * (1 - margin_frac))
    if avail_w / ar <= avail_h:
        WW = avail_w; WH = int(WW / ar)
    else:
        WH = avail_h; WW = int(WH * ar)
    WW -= WW % 2; WH -= WH % 2
    return WW, WH


def rounded_mask(path, WW, WH, rad, square_top=False):
    m = Image.new("L", (WW, WH), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, WW - 1, WH - 1], radius=rad, fill=255)
    if square_top:
        d.rectangle([0, 0, WW - 1, rad], fill=255)
    m.save(path)


def margin_frac_for(ratio):
    pad = CFG.get("padding")
    if pad not in (None, ""):
        return max(2, min(24, float(pad))) / 100.0
    return 0.10 if ratio == "9x16" else 0.16


# ---- themes: each returns (plate_png, mask_png|None, WX, WY, WW, WH) ----

def theme_float(ratio, CW, CH, ar):
    WW, WH = fit_window(CW, CH, ar, margin_frac_for(ratio))
    WX = (CW - WW) // 2; WY = (CH - WH) // 2
    img = background(CW, CH)
    img = add_shadow_card(img, WX, WY, WX + WW, WY + WH, RADIUS)
    img = add_logo(img, CW, CH, WX, WY, WW, WH)
    pl = f"{ASSETS}/plate_{ratio}.png"; img.convert("RGB").save(pl)
    mk = f"{ASSETS}/mask_{ratio}.png"; rounded_mask(mk, WW, WH, RADIUS)
    return pl, mk, WX, WY, WW, WH


def theme_browser(ratio, CW, CH, ar):
    WW, WH = fit_window(CW, CH, ar, margin_frac_for(ratio))
    bar = max(34, int(WH * 0.055))
    WX = (CW - WW) // 2; WY = (CH - WH + bar) // 2   # center card = bar + window
    img = background(CW, CH)
    img = add_shadow_card(img, WX, WY - bar, WX + WW, WY + WH, RADIUS)
    d = ImageDraw.Draw(img)
    # chrome bar
    d.rounded_rectangle([WX - 2, WY - bar - 2, WX + WW + 2, WY + RADIUS], radius=RADIUS, fill=(241, 243, 246, 255))
    d.rectangle([WX - 2, WY - 4, WX + WW + 2, WY], fill=(226, 230, 235, 255))
    r = int(bar * 0.17); cyy = WY - bar // 2
    for i, c in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
        cx = WX + int(bar * 0.55) + i * int(r * 3.1)
        d.ellipse([cx - r, cyy - r, cx + r, cyy + r], fill=c)
    # URL pill
    pw = int(WW * 0.44); ph = int(bar * 0.58)
    px0 = WX + (WW - pw) // 2; py0 = WY - bar + (bar - ph) // 2
    d.rounded_rectangle([px0, py0, px0 + pw, py0 + ph], radius=ph // 2, fill=(255, 255, 255, 255))
    url = CFG.get("browser_url") or ""
    if url:
        f = find_font(int(ph * 0.52), bold=False)
        tb = d.textbbox((0, 0), url, font=f)
        d.text((px0 + (pw - (tb[2] - tb[0])) / 2, py0 + (ph - (tb[3] - tb[1])) / 2 - tb[1]),
               url, font=f, fill=(120, 130, 145))
    img = add_logo(img, CW, CH, WX, WY - bar, WW, WH + bar)
    pl = f"{ASSETS}/plate_{ratio}.png"; img.convert("RGB").save(pl)
    mk = f"{ASSETS}/mask_{ratio}.png"; rounded_mask(mk, WW, WH, RADIUS, square_top=True)
    return pl, mk, WX, WY, WW, WH


def theme_split(ratio, CW, CH, ar):
    if ratio == "9x16":
        return theme_stack(ratio, CW, CH, ar)
    marg = int(CW * 0.045)
    WW = int(CW * 0.60); WH = int(WW / ar)
    if WH > CH - 2 * marg:
        WH = CH - 2 * marg; WW = int(WH * ar)
    WW -= WW % 2; WH -= WH % 2
    WX = CW - WW - marg; WY = (CH - WH) // 2
    img = background(CW, CH)
    img = add_shadow_card(img, WX, WY, WX + WW, WY + WH, RADIUS)
    # left panel content: logo, title, accent rule, subtitle
    lx = marg; panel_w = WX - 2 * marg
    y = int(CH * 0.30)
    if LOGO and os.path.exists(LOGO):
        try:
            lg = Image.open(LOGO).convert("RGBA")
            lg.thumbnail((int(panel_w * 0.66), int(CH * 0.12)), Image.LANCZOS)
            img.alpha_composite(lg, (lx, y - lg.height - int(CH * 0.05)))
        except Exception:
            pass
    d = ImageDraw.Draw(img)
    tf = find_font(int(CH * 0.055)); sf = find_font(int(CH * 0.026), bold=False)
    title = CFG.get("title") or ""
    # wrap title to the panel
    words, lines, cur = title.split(), [], ""
    for w_ in words:
        t2 = (cur + " " + w_).strip()
        if d.textbbox((0, 0), t2, font=tf)[2] > panel_w and cur:
            lines.append(cur); cur = w_
        else:
            cur = t2
    if cur:
        lines.append(cur)
    for ln in lines[:3]:
        d.text((lx, y), ln, font=tf, fill=(255, 255, 255)); y += int(CH * 0.068)
    d.rectangle([lx, y + int(CH * 0.015), lx + int(panel_w * 0.28), y + int(CH * 0.015) + max(4, CH // 200)],
                fill=_mix(TOP, (255, 255, 255), 0.45))
    sub = CFG.get("subtitle") or ""
    if sub:
        d.text((lx, y + int(CH * 0.05)), sub, font=sf, fill=(190, 205, 225))
    pl = f"{ASSETS}/plate_{ratio}.png"; img.convert("RGB").save(pl)
    mk = f"{ASSETS}/mask_{ratio}.png"; rounded_mask(mk, WW, WH, RADIUS)
    return pl, mk, WX, WY, WW, WH


def theme_stack(ratio, CW, CH, ar):
    """Vertical: video at near-full width, brand block underneath; the whole
    group is vertically centered (slight upward bias)."""
    marg = int(CW * 0.045)
    WW = CW - 2 * marg; WW -= WW % 2
    WH = int(WW / ar); WH -= WH % 2
    gap = int(CH * 0.045)
    brand_h = int(CH * 0.06) + int(CH * 0.022) + int(CH * 0.036)  # logo + gap + title
    WX = marg; WY = max(int(CH * 0.06), int((CH - (WH + gap + brand_h)) * 0.42))
    img = background(CW, CH)
    img = add_shadow_card(img, WX, WY, WX + WW, WY + WH, RADIUS)
    y = WY + WH + gap
    if LOGO and os.path.exists(LOGO):
        try:
            lg = Image.open(LOGO).convert("RGBA")
            lg.thumbnail((int(CW * 0.34), int(CH * 0.06)), Image.LANCZOS)
            img.alpha_composite(lg, ((CW - lg.width) // 2, y))
            y += lg.height + int(CH * 0.022)
        except Exception:
            pass
    d = ImageDraw.Draw(img)
    tf = find_font(int(CH * 0.030))
    title = CFG.get("title") or ""
    tb = d.textbbox((0, 0), title, font=tf)
    d.text(((CW - (tb[2] - tb[0])) / 2, y), title, font=tf, fill=(255, 255, 255))
    pl = f"{ASSETS}/plate_{ratio}.png"; img.convert("RGB").save(pl)
    mk = f"{ASSETS}/mask_{ratio}.png"; rounded_mask(mk, WW, WH, RADIUS)
    return pl, mk, WX, WY, WW, WH


THEMES = {"float": theme_float, "browser": theme_browser, "split": theme_split, "stack": theme_stack}


def build_full(ratio, CW, CH, sw, sh_):
    """Full-bleed: same aspect -> plain scale; different aspect -> blurred
    self-background fill behind a contained foreground. Optional corner logo."""
    d = dur(SRC)
    out = f"{PROJ}/framed-{ratio}.mp4"
    src_ar = sw / sh_; can_ar = CW / CH
    inputs = ["-i", SRC]
    logo_lay = None
    if LOGO and os.path.exists(LOGO):
        try:
            lay = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
            lg = Image.open(LOGO).convert("RGBA")
            lg.thumbnail((int(CW * 0.09), int(CH * 0.06)), Image.LANCZOS)
            m = int(CW * 0.022)
            corner = CFG.get("logo_corner", "tr")
            lx = m if "l" in corner else CW - lg.width - m
            ly = m if "t" in corner else CH - lg.height - m
            # subtle watermark treatment over content
            lg.putalpha(lg.getchannel("A").point(lambda a: int(a * 0.85)))
            lay.alpha_composite(lg, (lx, ly))
            logo_lay = f"{ASSETS}/logo_{ratio}.png"; lay.save(logo_lay)
            inputs += ["-i", logo_lay]
        except Exception:
            logo_lay = None
    if abs(src_ar - can_ar) < 0.02:
        chain = f"[0:v]scale={CW}:{CH},setsar=1[v0]"
    else:
        chain = (f"[0:v]split[bgs][fgs];"
                 f"[bgs]scale={CW}:{CH}:force_original_aspect_ratio=increase,crop={CW}:{CH},"
                 f"gblur=sigma=30,eq=brightness=-0.08[bg];"
                 f"[fgs]scale={CW}:{CH}:force_original_aspect_ratio=decrease[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]")
    if logo_lay:
        chain += f";[v0][1:v]overlay=0:0[outv]"
    else:
        chain += ";[v0]null[outv]"
    for attempt in range(3):
        sh("ffmpeg", "-y", *inputs, "-filter_complex", chain, "-map", "[outv]", "-map", "0:a",
           "-t", f"{d:.3f}", *venc("14M"), "-c:a", "aac", "-ar", "44100", out)
        if abs(dur(out) - d) < 1.0:
            break
        print(f"  {ratio} attempt {attempt+1}: {dur(out):.1f}s != {d:.1f}s, retry")
    print(f"{ratio}: {out}  {CW}x{CH} full-bleed -> {dur(out):.1f}s")
    return out


def build(ratio, prog=None):
    CW, CH = CANVAS.get(ratio, (1920, 1080))
    sw, sh_ = src_size(); ar = sw / sh_
    theme = THEME
    if theme == "full":
        return build_full(ratio, CW, CH, sw, sh_)
    if ratio == "9x16" and VSTACK and theme in ("float", "browser", "split"):
        theme = "stack"
    pl, mk, WX, WY, WW, WH = THEMES.get(theme, theme_float)(ratio, CW, CH, ar)
    d = dur(SRC)
    out = f"{PROJ}/framed-{ratio}.mp4"
    # scale the video, round its corners (alphamerge with a rounded mask), float on the plate
    fc = (f"[1:v]loop=loop=-1:size=1:start=0,fps=30,setsar=1[bg];"
          f"[0:v]scale={WW}:{WH},setsar=1,format=rgba[v];[v][2:v]alphamerge[va];"
          f"[bg][va]overlay={WX}:{WY}:shortest=1[outv]")
    ffargs = ["-y", "-i", SRC, "-i", pl, "-i", mk, "-filter_complex", fc,
              "-map", "[outv]", "-map", "0:a", "-t", f"{d:.3f}", *venc("14M"),
              "-c:a", "aac", "-ar", "44100", out]
    for attempt in range(3):
        if prog:
            ff_progress(ffargs, d, base=prog[0], span=prog[1])
        else:
            sh("ffmpeg", *ffargs)
        if abs(dur(out) - d) < 1.0:
            break
        print(f"  {ratio} attempt {attempt+1}: {dur(out):.1f}s != {d:.1f}s, retry")
    print(f"{ratio}: {out}  {CW}x{CH} theme={theme} window {WW}x{WH}  -> {dur(out):.1f}s")
    return out


def main():
    # fail fast on a broken input instead of encoding garbage for minutes
    d = dur(SRC)
    if d <= 0:
        raise SystemExit(f"{SRC} is missing or has no readable duration — "
                         "re-run the render (the fx pass may have been interrupted).")
    aspects = CFG.get("aspects", ["16x9"])
    if len(aspects) == 1:
        # single aspect: stream real framing progress (85-100%)
        build(aspects[0], prog=(0.85, 0.15))
    else:
        # multiple aspects run concurrently (progress would interleave; skip it)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(aspects)) as ex:
            list(ex.map(build, aspects))
    print("@@P 1.0", flush=True)
    print("done")


if __name__ == "__main__":
    main()
