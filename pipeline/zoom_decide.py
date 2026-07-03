"""AI-vision zoom decider (Cerebras gemma-4-31b, multimodal + fast).

For each narration segment we sample a frame, overlay a LABELED 6x3 grid
(columns A-F left->right, rows 1-3 top->bottom), and ask the model which cell
holds the UI element the narration is about. Naming a cell forces real spatial
reasoning — free-form cx,cy just collapsed to center. Falls back to Gemini vision.

decide(raw, segs, en, proj) -> list aligned to segs of
  {"zoom":bool, "cx":0..1, "cy":0..1, "scale":1.2-1.7, "cell":str, "reason":str}
"""
from __future__ import annotations
import os, json, base64, subprocess, re
from dotenv import load_dotenv
load_dotenv()
import requests
from PIL import Image, ImageDraw, ImageFont

CK = os.environ.get("CEREBRAS_API_KEY")
GEM = os.environ.get("GEMINI_API_KEY")
CEREBRAS = "https://api.cerebras.ai/v1/chat/completions"

COLS, ROWS = 6, 3  # A-F, 1-3

PROMPT = (
    "This is a frame from a software product-demo screen recording. A labeled grid is drawn on top: "
    f"{COLS} columns lettered A-F (left to right) and {ROWS} rows numbered 1-3 (top to bottom), so cells are A1..F3.\n"
    'The narration at this exact moment says: "{text}".\n'
    "Task: find the ONE specific UI element or area the narration is talking about (a button, field, menu, "
    "panel, list item, section, cursor target) and report the grid cell it sits in.\n"
    "Rules:\n"
    "- Do NOT default to the center. If the narration is about something on the LEFT, answer a column A or B cell; "
    "on the right, E or F; top row 1, bottom row 3. Match the cell to where the element actually is.\n"
    "- scale: 1.6-1.7 for a small target (one button/field), 1.3-1.4 for a larger panel/section.\n"
    "- If this moment is a general overview with no single element to focus on, set zoom=false.\n"
    'Return ONLY strict JSON: {{"zoom":true,"cell":"B2","scale":1.5,"reason":"the install button on the left"}}'
)


def _font(size):
    for p in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf"]:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _sample_frame(raw, t, out):
    subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", raw, "-frames:v", "1",
                    "-vf", "scale=1024:-1", "-q:v", "5", out], capture_output=True)


def _grid_overlay(src, dst):
    """Draw a semi-transparent labeled grid so the model can name a cell."""
    im = Image.open(src).convert("RGB")
    W, H = im.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    f = _font(max(14, H // 22))
    for c in range(1, COLS):
        x = int(W * c / COLS); d.line([(x, 0), (x, H)], fill=(255, 90, 90, 150), width=2)
    for r in range(1, ROWS):
        y = int(H * r / ROWS); d.line([(0, y), (W, y)], fill=(255, 90, 90, 150), width=2)
    for c in range(COLS):
        for r in range(ROWS):
            lbl = f"{chr(ord('A') + c)}{r + 1}"
            x = int(W * (c + 0.5) / COLS); y = int(H * (r + 0.5) / ROWS)
            bb = d.textbbox((0, 0), lbl, font=f)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            d.rectangle([x - tw / 2 - 4, y - th / 2 - 3, x + tw / 2 + 4, y + th / 2 + 3], fill=(0, 0, 0, 120))
            d.text((x - tw / 2, y - th / 2 - 2), lbl, font=f, fill=(255, 255, 255, 230))
    Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB").save(dst, quality=85)


def _strip(txt):
    txt = txt.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-z]*\n?", "", txt); txt = re.sub(r"\n?```$", "", txt)
    m = re.search(r"\{.*\}", txt, re.S)
    return m.group(0) if m else txt


def _cell_xy(cell):
    m = re.match(r"\s*([A-Fa-f])\s*([1-3])", str(cell) or "")
    if not m:
        return None
    c = ord(m.group(1).upper()) - ord("A")
    r = int(m.group(2)) - 1
    return ((c + 0.5) / COLS, (r + 0.5) / ROWS)


def _cerebras_vision(b, text):
    if not CK:
        raise RuntimeError("CEREBRAS_API_KEY not set")
    body = {"model": "gemma-4-31b", "max_tokens": 150, "temperature": 0.1,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT.format(text=text)},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b}}]}]}
    r = requests.post(CEREBRAS, headers={"Authorization": "Bearer " + CK}, json=body, timeout=40)
    r.raise_for_status()
    return json.loads(_strip(r.json()["choices"][0]["message"]["content"]))


def _gemini_vision(b, text):
    if not GEM:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-3.1-flash-lite:generateContent?key={GEM}")
    body = {"contents": [{"parts": [
        {"inline_data": {"mime_type": "image/jpeg", "data": b}},
        {"text": PROMPT.format(text=text)}]}],
        "generationConfig": {"temperature": 0.1, "response_mime_type": "application/json"}}
    r = requests.post(url, json=body, timeout=60)
    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])


def _resolve(d):
    """Turn a model answer into normalized cx,cy,scale. Prefer the named cell."""
    xy = _cell_xy(d.get("cell"))
    if xy is None and d.get("cx") is not None:
        try:
            xy = (float(d["cx"]), float(d["cy"]))
        except Exception:
            xy = None
    if xy is None:
        xy = (0.5, 0.5)
    cx, cy = xy
    d["cx"] = min(1.0, max(0.0, cx))
    d["cy"] = min(1.0, max(0.0, cy))
    d["scale"] = min(1.7, max(1.2, float(d.get("scale", 1.5))))
    return d


def decide(raw, segs, en, proj, precomputed=None):
    """precomputed: optional list aligned to segs; a non-None entry (e.g. a
    click-derived target from events_zoom) is used as-is and skips the AI call."""
    os.makedirs(f"{proj}/zoomframes", exist_ok=True)
    out, engine = [], None
    for i, s in enumerate(segs):
        if precomputed and i < len(precomputed) and precomputed[i] is not None:
            out.append(precomputed[i])
            print(f"  seg{i}: zoom @({precomputed[i]['cx']:.2f},{precomputed[i]['cy']:.2f}) x{precomputed[i]['scale']}  [click]")
            continue
        text = (en[i] if i < len(en) else "") or ""
        if not text:
            out.append({"zoom": False, "reason": "empty"}); continue
        mid = (s["start"] + s["end"]) / 2.0
        raw_fp = f"{proj}/zoomframes/f_{i}.jpg"
        grid_fp = f"{proj}/zoomframes/f_{i}_grid.jpg"
        _sample_frame(raw, mid, raw_fp)
        try:
            _grid_overlay(raw_fp, grid_fp)
        except Exception:
            grid_fp = raw_fp
        b = base64.b64encode(open(grid_fp, "rb").read()).decode()
        try:
            d = _cerebras_vision(b, text); engine = engine or "cerebras"
        except Exception:
            try:
                d = _gemini_vision(b, text); engine = "gemini"
            except Exception as e:
                d = {"zoom": False, "reason": "err:" + str(e)[:50]}
        if d.get("zoom"):
            d["zoom"] = True; _resolve(d)
        else:
            d["zoom"] = False
        out.append(d)
        z = f"zoom {d.get('cell','?')} @({d.get('cx'):.2f},{d.get('cy'):.2f}) x{d.get('scale')}" if d["zoom"] else "no-zoom"
        print(f"  seg{i}: {z}  \"{text[:38]}\"")
    print(f"  (zoom engine: {engine})")
    json.dump(out, open(f"{proj}/zooms.json", "w"), indent=1)
    return out


if __name__ == "__main__":
    import sys
    proj = sys.argv[1] if len(sys.argv) > 1 else "projects/demo-revoice-test"
    segs = json.load(open(f"{proj}/whisper.json"))["segments"]
    sc = json.load(open(f"{proj}/script.json"))["segments"]
    enmap = {s["i"]: s["en"] for s in sc}
    en = [enmap.get(i, "") for i in range(len(segs))]
    decide(f"{proj}/raw.webm", segs, en, proj)
