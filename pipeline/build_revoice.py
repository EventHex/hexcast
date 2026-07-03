"""Revoice pipeline v2: whisper-timed segments -> Gemini clean+translate (English,
brand glossary) -> Chirp3-HD TTS per segment -> segment-align with SPEED CAP
(0.8x-1.6x, pad the shorter stream) -> intro/outro cards -> concat.

Usage: python3 pipeline/build_revoice.py <project_dir> [--from-script]
  expects <project_dir>/raw.webm and <project_dir>/whisper.json
  writes  <project_dir>/script.json (editable) and <project_dir>/revoiced.mp4
  --from-script: skip Gemini, reuse the (hand-edited) script.json's english text
"""
from __future__ import annotations
import os, sys, json, subprocess, hashlib, time
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root: tools.* imports
from PIL import Image, ImageDraw, ImageFont
from providers.tts import synth as tts_synth, resolve_provider as tts_provider
import config as _cfgdef

PROJ = sys.argv[1] if len(sys.argv) > 1 else "projects/demo-revoice-test"
FROM_SCRIPT = "--from-script" in sys.argv
SCRIPT_ONLY = "--script-only" in sys.argv
FX_ONLY = "--fx-only" in sys.argv   # re-apply zoom/captions/music on base.mp4 (no re-voice)
# the recording may be webm/mp4/mov/mkv (the extension picks whichever exists)
RAW = next((f"{PROJ}/raw.{e}" for e in ("webm", "mp4", "mov", "mkv") if os.path.exists(f"{PROJ}/raw.{e}")), f"{PROJ}/raw.webm")
# defaults live in config.DEFAULTS (single source); main() overrides from the
# project's config.json. These module values only matter for standalone runs.
VOICE = _cfgdef.DEFAULTS["voice"]
GLOSSARY = _cfgdef.DEFAULTS["glossary"]
TITLE = _cfgdef.DEFAULTS["title"]
SUBTITLE = _cfgdef.DEFAULTS["subtitle"]
SPEED_MIN, SPEED_MAX = 0.7, 2.0
TOP = _cfgdef.hex_rgb(_cfgdef.DEFAULTS["brand_top"]); BOT = _cfgdef.hex_rgb(_cfgdef.DEFAULTS["brand_bottom"]); LOGO = None
CTOP, CBOT = TOP, BOT   # intro/outro card background (falls back to brand colors)
CAPTIONS = True; MUSIC = None; MUSIC_GAIN = _cfgdef.DEFAULTS["music_gain"]
ORIGINAL_VOICE = False; TRANSITION = "none"; LANG = "English"
CARD_STYLE = _cfgdef.DEFAULTS.get("card_style", "gradient")


def _h(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


class _R:
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


def ff_progress(args, total_s, base=0.0, span=1.0):
    """Run ffmpeg emitting real progress: prints '@@P <global_fraction>' lines
    to stdout (flushed) so the server can show a live progress bar. `args` is
    the ffmpeg argv WITHOUT the leading 'ffmpeg'; base/span map this encode's
    0..1 into a slice of the whole export's 0..1."""
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


def ffprobe(path, *args):
    return sh("ffprobe", "-v", "error", *args, path).stdout.strip()


def dur(path):
    return float(ffprobe(path, "-show_entries", "format=duration", "-of", "csv=p=0") or 0)


def zoom_filter(cx, cy, scale, seg_len, W, H, fps=30, speed=3):
    """Smooth zoom via zoompan: ease IN, hold, ease OUT back to 1.0. Constant WxH
    output, pans toward normalized (cx,cy). speed 1-5 sets the ease duration
    (higher = snappier); 3 ~= 0.5s ramp."""
    total = max(1, int(round(seg_len * fps)))
    ramp_s = max(0.15, 1.05 - float(speed) * 0.18)  # speed 1->0.87s ... 5->0.15s
    rampf = max(1, int(ramp_s * fps))
    z = (f"(1+{scale-1:.3f}*max(0,min(1,min(on/{rampf},({total}-on)/{rampf}))))")
    x = f"max(0,min(iw*zoom-{W},iw*{cx:.3f}*zoom-{W}/2))"
    y = f"max(0,min(ih*zoom-{H},ih*{cy:.3f}*zoom-{H}/2))"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps}"


# Arial Unicode first: broad glyph coverage so non-Latin captions (Hindi/Arabic/Tamil/CJK…) render.
CAP_FONT = next((p for p in ["/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                             "/Library/Fonts/Arial Unicode.ttf",
                             "/System/Library/Fonts/Supplemental/Arial.ttf",
                             "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"]
                 if os.path.exists(p)), None)


def _wrap(text, width=42, max_lines=3):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + len(w) + 1 > width:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:max_lines])


def caption_filter(txt_path, W, H):
    """drawtext reading a UTF-8 textfile (dodges escaping). Boxed, bottom-centered,
    sized to the source height so it scales down cleanly inside the framed window."""
    fs = max(20, int(H * 0.030))
    kv = [f"textfile='{txt_path}'", "fontcolor=white", f"fontsize={fs}",
          "box=1", "boxcolor=black@0.5", f"boxborderw={int(fs*0.4)}",
          "line_spacing=6", "x=(w-text_w)/2", f"y=h-text_h-{int(H*0.055)}"]
    if CAP_FONT:
        kv.insert(0, f"fontfile='{CAP_FONT}'")
    return "drawtext=" + ":".join(kv)


def find_font(size, bold=True):
    """Card text font: the project's bundled font first, then the system hunt."""
    import fonts as _fonts
    p = _fonts.path(globals().get("FONT_ID"), bold=bold)
    if p:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    for p in ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf",
              "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return ImageFont.load_default()


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _card_bg(W, H, style):
    """Background plate per card style. gradient|diagonal|radial|accent|minimal."""
    img = Image.new("RGB", (W, H), CBOT)
    px = img.load()
    if style == "diagonal":
        for y in range(H):
            for x in range(0, W, 2):
                t = (x / W + y / H) / 2
                c = _lerp(CTOP, CBOT, t)
                px[x, y] = c
                if x + 1 < W: px[x + 1, y] = c
    elif style == "radial":
        cx, cy = W / 2, H * 0.42
        import math
        maxd = math.hypot(max(cx, W - cx), max(cy, H - cy))
        for y in range(H):
            for x in range(0, W, 2):
                t = min(1.0, math.hypot(x - cx, y - cy) / maxd)
                c = _lerp(CTOP, CBOT, t ** 0.8)
                px[x, y] = c
                if x + 1 < W: px[x + 1, y] = c
    elif style == "accent":
        # solid dark plate + accent side bar
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, int(W * 0.012), H], fill=CTOP)
        d.rectangle([int(W * 0.055), int(H * 0.40), int(W * 0.055) + int(W * 0.06), int(H * 0.40) + max(4, H // 180)], fill=CTOP)
    elif style == "minimal":
        img = Image.new("RGB", (W, H), (246, 248, 251))
        d = ImageDraw.Draw(img)
        d.rectangle([int(W * 0.055), int(H * 0.40), int(W * 0.055) + int(W * 0.06), int(H * 0.40) + max(4, H // 180)], fill=CTOP)
    else:  # gradient (default): vertical brand gradient
        for y in range(H):
            c = _lerp(CTOP, CBOT, y / H)
            for x in range(W):
                px[x, y] = c
    return img


def card(path, W, H, title, subtitle, style=None):
    style = style or CARD_STYLE
    img = _card_bg(W, H, style).convert("RGBA")
    left = style in ("accent", "minimal")
    dark_text = style == "minimal"
    if LOGO and os.path.exists(LOGO):
        try:
            lg = Image.open(LOGO).convert("RGBA")
            lg.thumbnail((int(W * 0.22), int(H * 0.16)), Image.LANCZOS)
            pos = (int(W * 0.055), int(H * 0.14)) if left else ((W - lg.width) // 2, int(H * 0.24))
            img.alpha_composite(lg, pos)
        except Exception:
            pass
    align = globals().get("CARD_ALIGN")
    if align in ("left", "center"):
        left = align == "left"
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    scale = float(globals().get("CARD_SCALE") or 1.0)
    tf = find_font(int(H * 0.09 * scale), bold=True); sf = find_font(int(H * 0.038 * scale), bold=False)
    import config as _c
    tcol = _c.hex_rgb(globals()["CARD_TITLE_COLOR"]) if globals().get("CARD_TITLE_COLOR") \
        else ((18, 26, 40) if dark_text else (255, 255, 255))
    scol = _c.hex_rgb(globals()["CARD_SUB_COLOR"]) if globals().get("CARD_SUB_COLOR") \
        else (CTOP if dark_text else (150, 200, 255))
    tw = d.textbbox((0, 0), title, font=tf); sw = d.textbbox((0, 0), subtitle, font=sf)
    if left:
        d.text((W * 0.055, H * 0.46), title, font=tf, fill=tcol)
        d.text((W * 0.055, H * 0.60), subtitle, font=sf, fill=scol)
    else:
        d.text(((W - (tw[2] - tw[0])) / 2, H * 0.46), title, font=tf, fill=tcol)
        d.text(((W - (sw[2] - sw[0])) / 2, H * 0.60), subtitle, font=sf, fill=scol)
    img.save(path)


def make_card_clip(out, W, H, title, subtitle, seconds=2.0):
    png = out.replace(".mp4", ".png")
    card(png, W, H, title, subtitle)
    sh("ffmpeg", "-y", "-loop", "1", "-t", f"{seconds}", "-i", png,
       "-f", "lavfi", "-t", f"{seconds}", "-i", "anullsrc=r=44100:cl=stereo",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
       "-c:a", "aac", "-ar", "44100", "-shortest", out)


def main():
    import config
    cfg = config.load(PROJ)
    global VOICE, TITLE, SUBTITLE, GLOSSARY, TOP, BOT, CTOP, CBOT, LOGO, CAPTIONS, MUSIC, MUSIC_GAIN, ORIGINAL_VOICE, TRANSITION, LANG, CARD_STYLE
    global FONT_ID, CARD_ALIGN, CARD_TITLE_COLOR, CARD_SUB_COLOR, CARD_SCALE
    FONT_ID = cfg.get("font")
    CARD_ALIGN = cfg.get("card_align")
    CARD_TITLE_COLOR = cfg.get("card_title_color")
    CARD_SUB_COLOR = cfg.get("card_sub_color")
    CARD_SCALE = float(cfg.get("card_scale") or 1.0)
    VOICE = cfg["voice"]; TITLE = cfg["title"]; SUBTITLE = cfg["subtitle"]
    GLOSSARY = cfg["glossary"]; LOGO = cfg.get("logo")
    TOP = config.hex_rgb(cfg["brand_top"]); BOT = config.hex_rgb(cfg["brand_bottom"])
    CTOP = config.hex_rgb(cfg.get("card_top") or cfg["brand_top"])
    CBOT = config.hex_rgb(cfg.get("card_bottom") or cfg["brand_bottom"])
    CAPTIONS = bool(cfg.get("captions", True))
    MUSIC = cfg.get("music"); MUSIC_GAIN = cfg.get("music_gain", config.DEFAULTS["music_gain"])
    CARD_STYLE = cfg.get("card_style") or "gradient"
    ORIGINAL_VOICE = bool(cfg.get("original_voice", False))
    # BYOK: which TTS backend voices this render. 'original' (the zero-key
    # default) means keep the recorded audio — same path as original_voice.
    TTS_PROVIDER = tts_provider()
    if TTS_PROVIDER == "original":
        ORIGINAL_VOICE = True
    # cache/signature key: switching provider must invalidate voiced audio,
    # but google keys stay bare so existing caches survive
    VOICE_KEY = VOICE if TTS_PROVIDER == "google" else f"{TTS_PROVIDER}:{VOICE}"
    TRANSITION = cfg.get("transition", "none") or "none"
    LANG = cfg.get("lang", "English") or "English"
    if "--voice" in sys.argv:
        VOICE = sys.argv[sys.argv.index("--voice") + 1]
    ZOOM_ON = bool(cfg["zoom"])
    OUTRO_TITLE, OUTRO_SUB = cfg["outro_title"], cfg["outro_subtitle"]
    INTRO_DUR = float(cfg.get("intro_dur", 2.5) or 2.5); OUTRO_DUR = float(cfg.get("outro_dur", 2.5) or 2.5)

    # v:0 = first video stream only (mp4s can carry a thumbnail stream too)
    _dim = ffprobe(RAW, "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x")
    W, H = (int(x) for x in _dim.split("x")[:2])
    # Cap the working resolution: the final framed window is ~1450px wide, so
    # rendering the heavy fx/zoom pass at a 5MP source (2880px) just burns time.
    # 1600px keeps the window crisp and headroom for zoom, ~3x fewer pixels.
    CAP = int(os.environ.get("REVOICE_RES_CAP", "1600"))
    if CAP and max(W, H) > CAP:
        sc = CAP / max(W, H)
        W = int(W * sc); H = int(H * sc)
    W -= W % 2; H -= H % 2

    # Fast path: only re-apply the fx pass (zoom/captions/music) to the existing base.mp4.
    if FX_ONLY:
        data = json.load(open(f"{PROJ}/script.json"))
        timeline = [s for s in data["segments"] if s.get("rstart") is not None]
        render_fx(f"{PROJ}/base.mp4", W, H, timeline, data.get("zooms") or [], bool(data.get("zoomsEdited")), data.get("elements") or [])
        json.dump(data, open(f"{PROJ}/script.json", "w"), indent=1, ensure_ascii=False)
        print(f"fx-only done: {PROJ}/revoiced.mp4  ({dur(PROJ + '/revoiced.mp4'):.1f}s)")
        return

    segs = json.load(open(f"{PROJ}/whisper.json"))["segments"] if not FROM_SCRIPT else []
    print(f"source {W}x{H}  (speed cap {SPEED_MIN}-{SPEED_MAX}x)")

    # ---- build the ordered render segments (rsegs) ----
    # FROM_SCRIPT: the edited script.json is the source of truth (order + types +
    # zoom events). Fresh: transcript -> clean -> per-line AI zoom (clips only).
    if FROM_SCRIPT:
        data = json.load(open(f"{PROJ}/script.json"))
        rsegs = data["segments"]
        ZOOMS_SAVED = data.get("zooms") or []
        ZOOMS_EDITED = bool(data.get("zoomsEdited"))
        ELEMENTS_SAVED = data.get("elements") or []
        SOUNDS_SAVED = data.get("sounds") or []
        print(f"using edited script.json ({len(rsegs)} segments, {len(ZOOMS_SAVED)} zoom events, {len(ELEMENTS_SAVED)} elements)")
        # Language change: the edited narration is still in the previous language.
        # Re-translate it into LANG (preserving the user's edits) before TTS, so
        # the voice reads the right words — not the old-language text.
        prev_lang = data.get("lang") or "English"
        if LANG != prev_lang:
            from cerebras_clean import clean_translate as cc_clean
            idx = [i for i, s in enumerate(rsegs)
                   if s.get("type", "clip") in ("clip", "added") and s.get("en")]
            if idx:
                print(f"translating {len(idx)} narration lines: {prev_lang} → {LANG}…")
                res = cc_clean([{"text": rsegs[i]["en"],
                                 "start": float(rsegs[i].get("start") or rsegs[i].get("rstart") or 0),
                                 "end": float(rsegs[i].get("end") or rsegs[i].get("rstart") or 0)}
                                for i in idx],
                               glossary=GLOSSARY, target_lang=LANG)
                cl = res.get("segments") or []
                for j, i in enumerate(idx):
                    nt = (cl[j].get("clean", "") if j < len(cl) else "").strip()
                    if nt:
                        rsegs[i]["en"] = nt
                # persist immediately so the editor shows the translated script
                # even if a later stage fails
                data["lang"] = LANG
                json.dump(data, open(f"{PROJ}/script.json", "w"), indent=1, ensure_ascii=False)
    else:
        from cerebras_clean import clean_translate as cc_clean
        print(f"clean+translate → {LANG} (Cerebras → Gemini-3.1-flash-lite fallback)…")
        res = cc_clean(segs, glossary=GLOSSARY, target_lang=LANG)
        print("  engine:", res.get("engine"))
        cl = res["segments"]
        en = [(cl[i].get("clean", "") if i < len(cl) else "") for i in range(len(segs))]
        if ZOOM_ON:
            from zoom_decide import decide as decide_zooms
            from events_zoom import click_targets
            pre = click_targets(f"{PROJ}/events.json", segs)
            n_click = sum(1 for p in pre if p)
            if n_click:
                print(f"zoom targets: {n_click}/{len(segs)} from recorded clicks, rest AI vision…")
            else:
                print("AI zoom decisions (grid vision, Cerebras → Gemini)…")
            zd = decide_zooms(RAW, segs, en, PROJ, precomputed=pre)
        else:
            zd = [{"zoom": False}] * len(segs)
        rsegs = []
        for i, s in enumerate(segs):
            if not en[i]:
                continue
            z = zd[i] if i < len(zd) else {}
            rsegs.append({"i": i, "type": "clip", "start": s["start"], "end": s["end"],
                          "original": s["text"], "en": en[i],
                          "zoom": bool(z.get("zoom")), "cx": z.get("cx"), "cy": z.get("cy"),
                          "scale": z.get("scale"), "speed": z.get("speed") or 3})
        ZOOMS_SAVED, ZOOMS_EDITED, ELEMENTS_SAVED, SOUNDS_SAVED = [], False, [], []

    if SCRIPT_ONLY:
        if not rsegs:
            raise SystemExit("No speech detected in the recording — nothing to script "
                             "(check that your microphone was on).")
        json.dump({"voice": VOICE, "title": TITLE, "lang": LANG, "segments": rsegs,
                   "zooms": [], "zoomsEdited": False, "elements": []},
                  open(f"{PROJ}/script.json", "w"), indent=1, ensure_ascii=False)
        print(f"script ready: {len(rsegs)} segments (no render)")
        return

    # ---- render each segment RAW (no zoom/caption); those go on the fx pass ----
    # TTS + segment clips are content-addressed (hash of everything that shapes
    # them), so an unchanged line is a cache hit and a one-word edit re-renders
    # only its own segment.
    os.makedirs(f"{PROJ}/seg", exist_ok=True)
    raw_sig = f"{os.path.getmtime(RAW):.0f}" if os.path.exists(RAW) else "0"

    def _needs_tts(sg):
        typ = sg.get("type", "clip")
        return bool(sg.get("en")) and (typ == "added" or (typ == "clip" and not ORIGINAL_VOICE))

    def _tts_path(sg):
        return f"{PROJ}/seg/tts_{_h(sg['en'], VOICE_KEY)}.mp3"

    def _gen_tts(k):
        """Returns None on success/cache-hit, error string on failure."""
        sg = rsegs[k]
        if not _needs_tts(sg):
            return None
        mp3 = _tts_path(sg)
        if os.path.exists(mp3) and os.path.getsize(mp3) > 0:
            return None
        last = "no output"
        for attempt in range(3):
            try:
                tts_synth(sg["en"], VOICE, mp3, provider=TTS_PROVIDER)
                if os.path.exists(mp3) and os.path.getsize(mp3) > 0:
                    return None
            except Exception as e:
                last = str(e)
            time.sleep(1.5 * (attempt + 1))
        return f"seg{k}: TTS failed after 3 attempts ({last[:150]})"

    with ThreadPoolExecutor(max_workers=8) as ex:
        tts_errs = [e for e in ex.map(_gen_tts, range(len(rsegs))) if e]
    if tts_errs:
        raise SystemExit("TTS generation failed — nothing was rendered:\n" + "\n".join(tts_errs))

    card_sig = (CARD_STYLE, CTOP, CBOT, LOGO or "", W, H,
                FONT_ID or "", CARD_ALIGN or "", CARD_TITLE_COLOR or "", CARD_SUB_COLOR or "", CARD_SCALE)
    used = set()

    def _cached(out):
        return os.path.exists(out) and dur(out) > 0

    parts, timeline, pdurs, fails = [], [], [], []
    intro = f"{PROJ}/seg/card_{_h('intro', TITLE, SUBTITLE, INTRO_DUR, *card_sig)}.mp4"
    if not _cached(intro):
        make_card_clip(intro, W, H, TITLE, SUBTITLE, seconds=INTRO_DUR)
    used.add(intro); parts.append(intro); pdurs.append(dur(intro))

    for k, sg in enumerate(rsegs):
        typ = sg.get("type", "clip")
        tdur = 0.0
        hit = False
        if typ == "scene":
            seg_len = float(sg.get("dur") or 3.0)
            out = f"{PROJ}/seg/card_{_h('scene', sg.get('title') or sg.get('en') or '', sg.get('subtitle') or '', seg_len, *card_sig)}.mp4"
            if not (hit := _cached(out)):
                make_card_clip(out, W, H, sg.get("title") or sg.get("en") or "", sg.get("subtitle") or "", seconds=seg_len)
        elif typ == "clip" and ORIGINAL_VOICE:  # keep the recorded audio + natural timing
            seg_len = max(0.4, sg["end"] - sg["start"])
            out = f"{PROJ}/seg/clip_{_h('orig', sg['start'], sg['end'], W, H, raw_sig)}.mp4"
            if not (hit := _cached(out)):
                sh("ffmpeg", "-y", "-ss", str(sg["start"]), "-t", f"{seg_len:.3f}", "-i", RAW,
                   "-filter_complex", f"[0:v]fps=30,scale={W}:{H},setsar=1,format=yuv420p[v];[0:a]aresample=44100[a]",
                   "-map", "[v]", "-map", "[a]", *venc("12M"), "-c:a", "aac", "-ar", "44100", "-t", f"{seg_len:.3f}", out)
        else:
            mp3 = _tts_path(sg)
            tdur = dur(mp3) if os.path.exists(mp3) else 0
            if tdur <= 0:
                print(f"  seg{k}: (dropped, empty TTS)"); continue
            used.add(mp3); sg["tts_file"] = f"seg/{os.path.basename(mp3)}"
            seg_len = tdur
            if typ == "added":  # narration over a frozen source frame
                anchor = float(sg.get("anchor", sg.get("start", 0)) or 0)
                out = f"{PROJ}/seg/clip_{_h('added', anchor, os.path.basename(mp3), W, H, raw_sig)}.mp4"
                if not (hit := _cached(out)):
                    frame = out.replace(".mp4", ".png")
                    sh("ffmpeg", "-y", "-ss", f"{anchor:.2f}", "-i", RAW, "-frames:v", "1", "-vf", f"scale={W}:{H}", frame)
                    sh("ffmpeg", "-y", "-loop", "1", "-t", f"{seg_len:.3f}", "-i", frame, "-i", mp3,
                       "-filter_complex", "[0:v]fps=30,setsar=1,format=yuv420p[v];[1:a]apad[a]",
                       "-map", "[v]", "-map", "[a]", *venc("12M"), "-c:a", "aac", "-ar", "44100", "-t", f"{seg_len:.3f}", out)
            else:  # clip: slice source, fit to narration, drop trailing dead air
                orig = max(0.4, sg["end"] - sg["start"])
                src_use = min(orig, SPEED_MAX * tdur)
                speed = max(SPEED_MIN, src_use / tdur)
                pad_v = max(0.0, seg_len - src_use / speed)
                out = f"{PROJ}/seg/clip_{_h('clip', sg['start'], sg['end'], os.path.basename(mp3), W, H, raw_sig)}.mp4"
                if not (hit := _cached(out)):
                    vf = (f"setpts=PTS/{speed:.5f},fps=30,scale={W}:{H},setsar=1,format=yuv420p,"
                          f"tpad=stop_mode=clone:stop_duration={pad_v:.3f}")
                    sh("ffmpeg", "-y", "-ss", str(sg["start"]), "-t", f"{src_use:.3f}", "-i", RAW, "-i", mp3,
                       "-filter_complex", f"[0:v]{vf}[v];[1:a]apad[a]", "-map", "[v]", "-map", "[a]",
                       *venc("12M"), "-c:a", "aac", "-ar", "44100", "-t", f"{seg_len:.3f}", out)
        if not (os.path.exists(out) and dur(out) > 0):
            fails.append(f"seg{k} ({typ}): \"{(sg.get('en') or sg.get('title') or '')[:60]}\"")
            print(f"  seg{k}: FAIL ({typ})"); continue
        used.add(out)
        sg["tts_dur"] = round(tdur, 2); sg["rdur"] = round(seg_len, 3)
        parts.append(out); timeline.append(sg); pdurs.append(seg_len)
        print(f"  seg{k} [{typ}] {seg_len:.1f}s{' (cache)' if hit else ''}  \"{(sg.get('en') or sg.get('title') or '')[:42]}\"")

    if fails:
        raise SystemExit("Some segments failed to render — aborting so no content is silently lost:\n"
                         + "\n".join(fails))

    outro = f"{PROJ}/seg/card_{_h('outro', OUTRO_TITLE, OUTRO_SUB, OUTRO_DUR, *card_sig)}.mp4"
    if not _cached(outro):
        make_card_clip(outro, W, H, OUTRO_TITLE, OUTRO_SUB, seconds=OUTRO_DUR)
    used.add(outro); parts.append(outro); pdurs.append(dur(outro))

    # GC stale cache entries + legacy positional seg files (element uploads
    # img_* and zoom frames are untouched). A .png sticks around only while its
    # .mp4 twin is in use (card/freeze-frame sources).
    for f in os.listdir(f"{PROJ}/seg"):
        if not (f.startswith(("tts_", "clip_", "card_", "seg_", "frz_")) or f.startswith(("_intro.", "_outro."))):
            continue
        p = f"{PROJ}/seg/{f}"
        twin = p[:-4] + ".mp4" if p.endswith(".png") else p
        if twin not in used:
            try: os.remove(p)
            except OSError: pass

    # transition overlap (0 = hard cut). Clamp so no clip is shorter than the fade.
    n = len(parts)
    FADE = 0.0 if TRANSITION == "none" or n < 2 else min(0.35, (min(pdurs) / 2.5))
    # rendered start of each part on the output timeline (accounts for xfade overlap)
    partstart = [0.0] * n; run = pdurs[0]
    for i in range(1, n):
        partstart[i] = run - FADE; run = run - FADE + pdurs[i]
    for j, sg in enumerate(timeline):   # timeline segs are parts[1..n-2]
        sg["rstart"] = round(partstart[j + 1], 3)

    base = f"{PROJ}/base.mp4"
    inputs = []
    for p in parts:
        inputs += ["-i", p]
    if FADE > 0:
        trans = {"dissolve": "dissolve", "slide": "slideleft", "fade": "fade"}.get(TRANSITION, "dissolve")
        fc = "".join(f"[{i}:v]fps=30,setsar=1,format=yuv420p[cv{i}];" for i in range(n))
        vout = "[cv0]"; run = pdurs[0]
        for i in range(1, n):
            o = f"[vx{i}]"; fc += f"{vout}[cv{i}]xfade=transition={trans}:duration={FADE:.3f}:offset={run - FADE:.3f}{o};"
            vout = o; run = run - FADE + pdurs[i]
        aout = "[0:a]"
        for i in range(1, n):
            o = f"[ax{i}]"; fc += f"{aout}[{i}:a]acrossfade=d={FADE:.3f}{o};"; aout = o
        sh("ffmpeg", "-y", *inputs, "-filter_complex", fc.rstrip(";"), "-map", vout, "-map", aout,
           *venc("14M"), "-r", "30", "-c:a", "aac", "-ar", "44100", base)
    else:
        fc = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
        sh("ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
           *venc("14M"), "-r", "30", "-c:a", "aac", "-ar", "44100", base)

    render_fx(base, W, H, timeline, ZOOMS_SAVED, ZOOMS_EDITED, ELEMENTS_SAVED)
    json.dump({"voice": VOICE, "title": TITLE, "lang": LANG, "segments": timeline,
               "zooms": (ZOOMS_SAVED if (ZOOMS_EDITED and ZOOMS_SAVED) else _derive_zooms(timeline)),
               "zoomsEdited": ZOOMS_EDITED, "elements": ELEMENTS_SAVED, "sounds": SOUNDS_SAVED,
               "voiced_sig": config.voiced_sig(timeline, VOICE_KEY)},
              open(f"{PROJ}/script.json", "w"), indent=1, ensure_ascii=False)
    print(f"\nDONE: {PROJ}/revoiced.mp4  ({dur(PROJ + '/revoiced.mp4'):.1f}s, {len(parts)} clips)")


def _derive_zooms(timeline):
    """Zoom events straight from the per-line AI suggestion (rendered times)."""
    return [{"start": sg["rstart"], "end": round(sg["rstart"] + sg["rdur"], 3),
             "cx": sg.get("cx") or 0.5, "cy": sg.get("cy") or 0.5,
             "scale": sg.get("scale") or 1.4, "speed": sg.get("speed") or 3}
            for sg in timeline if sg.get("zoom")]


SFX_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", "webstudio", "assets", "sfx"))


def _click_times(timeline):
    """Map recorded click timestamps (raw-recording seconds, from events.json)
    onto the rendered timeline, accounting for each segment's speed change."""
    p = f"{PROJ}/events.json"
    if not os.path.exists(p):
        return []
    try:
        evs = json.load(open(p)).get("events") or []
    except Exception:
        return []
    clicks = sorted(float(e["t"]) for e in evs
                    if (e.get("ty") or e.get("type")) == "click" and e.get("t") is not None)
    out = []
    for sg in timeline:
        if sg.get("type", "clip") != "clip" or sg.get("rstart") is None:
            continue
        st, en, td = sg.get("start"), sg.get("end"), sg.get("tts_dur") or 0
        if st is None or en is None:
            continue
        orig = max(0.4, en - st)
        speed = 1.0 if td <= 0 else max(SPEED_MIN, min(orig, SPEED_MAX * td) / td)
        for t in clicks:
            if st <= t <= en:
                rt = sg["rstart"] + (t - st) / speed
                if rt <= sg["rstart"] + (sg.get("rdur") or 0):
                    out.append(rt)
    return sorted(out)


def _mix_sfx(final, zoom_events, timeline, cfg):
    """Overlay short SFX (clicks from the recording, whooshes on zooms) onto the
    final mix. Additive amix — narration/music levels are untouched."""
    evs = []
    if cfg.get("sfx_zoom"):
        w = os.path.join(SFX_DIR, "whoosh.wav")
        if os.path.exists(w):
            evs += [(max(0.0, z["start"]), w, 0.45) for z in zoom_events]
    if cfg.get("sfx_clicks", True):
        c = os.path.join(SFX_DIR, "click.wav")
        if os.path.exists(c):
            evs += [(t, c, 0.9) for t in _click_times(timeline)]
    # user-placed sounds from the editor (script.json "sounds": sfx name, start, gain dB)
    try:
        user = json.load(open(f"{PROJ}/script.json")).get("sounds") or []
    except Exception:
        user = []
    for snd in user:
        f = os.path.join(SFX_DIR, f"{snd.get('sfx')}.wav")
        if os.path.exists(f) and snd.get("start") is not None:
            vol = 10 ** (max(-24.0, min(6.0, float(snd.get("gain") or 0))) / 20)
            evs.append((max(0.0, float(snd["start"])), f, round(vol, 3)))
    evs = sorted(evs)[:60]
    if not evs:
        return
    files = sorted({f for _, f, _ in evs})
    fidx = {f: i + 1 for i, f in enumerate(files)}
    counts = {f: sum(1 for _, g, _ in evs if g == f) for f in files}
    parts, labels, used = [], [], {f: 0 for f in files}
    for f in files:
        if counts[f] > 1:
            outs = "".join(f"[u{fidx[f]}_{k}]" for k in range(counts[f]))
            parts.append(f"[{fidx[f]}:a]asplit={counts[f]}{outs}")
    for j, (t, f, vol) in enumerate(evs):
        src = f"[{fidx[f]}:a]" if counts[f] == 1 else f"[u{fidx[f]}_{used[f]}]"
        used[f] += 1
        parts.append(f"{src}adelay={int(t * 1000)}:all=1,volume={vol}[s{j}]")
        labels.append(f"[s{j}]")
    graph = ";".join(parts) + f";[0:a]{''.join(labels)}amix=inputs={len(evs) + 1}:duration=first:normalize=0[a]"
    ins = ["-i", final]
    for f in files:
        ins += ["-i", f]
    mixed = f"{PROJ}/revoiced_s.mp4"
    r = sh("ffmpeg", "-y", *ins, "-filter_complex", graph, "-map", "0:v", "-c:v", "copy",
           "-map", "[a]", "-c:a", "aac", "-ar", "44100", mixed)
    if os.path.exists(mixed) and dur(mixed) > 0:
        os.replace(mixed, final); print(f"  sfx mixed: {len(evs)} events")
    else:
        print("  sfx mix skipped:", (r.stderr or "")[-150:])


def render_fx(base, W, H, timeline, zooms_saved, zooms_edited, elements=None):
    """Apply zoom events + captions + elements (fx pass) to base.mp4, then music.
    Also used standalone by --fx-only for fast re-renders (no re-voicing)."""
    import config, timeline_fx
    cfg = config.load(PROJ)
    caps = bool(cfg.get("captions", True))
    music = cfg.get("music"); gain = cfg.get("music_gain", config.DEFAULTS["music_gain"])
    zoom_events = zooms_saved if (zooms_edited and zooms_saved) else _derive_zooms(timeline)
    cap_events = timeline_fx.caption_events(timeline) if caps else []
    elements = elements or []
    print(f"  fx: {len(zoom_events)} zooms, {len(elements)} elements, captions={'on' if caps else 'off'}")
    final = f"{PROJ}/revoiced.mp4"
    import fonts as _fonts
    cap_font = _fonts.path(cfg.get("font")) or CAP_FONT
    cap_style = {"pos": cfg.get("cap_pos"), "scale": cfg.get("cap_scale"),
                 "color": cfg.get("cap_color"), "bg": cfg.get("cap_bg"),
                 "bg_opacity": cfg.get("cap_bg_opacity")}
    graph, outlab, imgs = timeline_fx.build_graph(zoom_events, cap_events, elements, W, H, 30, cap_font,
                                                  f"{PROJ}/seg", cap_style=cap_style)
    if graph:
        inputs = ["-i", base]
        for s in imgs:
            inputs += ["-i", s]
        # the fx encode is the export's long pole -> stream real progress (10-82%)
        ff_progress(["-y", *inputs, "-filter_complex", graph, "-map", f"[{outlab}]", "-map", "0:a",
                     *venc("14M"), "-r", "30", "-c:a", "aac", "-ar", "44100", final],
                    dur(base), base=0.10, span=0.72)
        if not (os.path.exists(final) and dur(final) > 0):
            import shutil; shutil.copy(base, final); print("  fx failed; copied base")
    else:
        import shutil; shutil.copy(base, final)
    print("@@P 0.82", flush=True)
    _mix_sfx(final, zoom_events, timeline, cfg)
    if music and os.path.exists(music):
        mixed = f"{PROJ}/revoiced_m.mp4"
        af = (f"[1:a]volume={gain}dB,aloop=loop=-1:size=2000000000,aresample=44100[bg];"
              f"[bg][0:a]sidechaincompress=threshold=0.03:ratio=4:attack=20:release=600[bgd];"
              f"[0:a][bgd]amix=inputs=2:duration=first:normalize=0[a]")
        r = sh("ffmpeg", "-y", "-i", final, "-i", music, "-filter_complex", af,
               "-map", "0:v", "-c:v", "copy", "-map", "[a]", "-c:a", "aac", "-ar", "44100", "-shortest", mixed)
        if os.path.exists(mixed) and dur(mixed) > 0:
            os.replace(mixed, final); print(f"  music bed mixed + ducked ({os.path.basename(music)} @ {gain}dB)")
        else:
            print("  music mix skipped:", (r.stderr or "")[-200:])


if __name__ == "__main__":
    main()
