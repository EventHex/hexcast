"""Timeline effects pass: apply free-form ZOOM events and chunked CAPTIONS to the
already-concatenated base video in a single ffmpeg filter chain.

Zoom and captions live on the ABSOLUTE rendered timeline (seconds from the start
of the base video, intro included) — not baked per-segment — so zoom blocks can be
dragged/resized freely and captions are drawn AFTER the zoom (never cropped by it).

  vf = build_vf(zoom_events, caption_events, W, H, fps, font, workdir)
  ffmpeg -i base.mp4 -vf "<vf>" ... revoiced.mp4

zoom event:    {"start":s,"end":s,"cx":0..1,"cy":0..1,"scale":1.1..2.0,"speed":1..5}
caption event: {"start":s,"end":s,"text":"..."}
"""
from __future__ import annotations
import os, re


def chunk_text(text, maxw=9):
    """Split a line into caption-sized chunks: sentence-first, then <=maxw words."""
    text = " ".join((text or "").split())
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        words = sent.split()
        while len(words) > maxw:
            out.append(" ".join(words[:maxw])); words = words[maxw:]
        if words:
            out.append(" ".join(words))
    return out


def caption_events(segs):
    """segs: [{en, rstart, tts_dur, type}] -> timed caption chunks over each line."""
    evs = []
    for s in segs:
        if s.get("type") == "scene" or not s.get("en"):
            continue
        chunks = chunk_text(s["en"])
        if not chunks:
            continue
        tw = sum(len(c.split()) for c in chunks) or 1
        t, d = s["rstart"], max(0.3, s.get("tts_dur", 0) or 0)
        for c in chunks:
            w = len(c.split()); seg = d * w / tw
            evs.append({"start": round(t, 3), "end": round(t + seg, 3), "text": c}); t += seg
    return evs


def _speed_ramp(speed):
    return max(0.15, 1.05 - float(speed) * 0.18)  # 1->0.87s ... 5->0.15s


def _zoom_filter(events, W, H, fps):
    if not events:
        return None
    ev = sorted(events, key=lambda e: e["start"])

    def nest(field, default):
        expr = default
        for e in reversed(ev):
            a, b = float(e["start"]), float(e["end"])
            r = _speed_ramp(e.get("speed", 3))
            t = f"(on/{fps})"
            w = f"max(0,min(1,min(({t}-{a:.3f})/{r:.3f},({b:.3f}-{t})/{r:.3f})))"
            if field == "z":
                val = f"(1+({float(e.get('scale',1.5)):.3f}-1)*{w})"
            elif field == "cx":
                val = f"{min(1.0,max(0.0,float(e.get('cx',0.5)))):.3f}"
            else:
                val = f"{min(1.0,max(0.0,float(e.get('cy',0.5)))):.3f}"
            expr = f"if(between({t},{a:.3f},{b:.3f}),{val},{expr})"
        return expr

    Z = nest("z", "1"); CX = nest("cx", "0.5"); CY = nest("cy", "0.5")
    X = f"max(0,min(iw*{Z}-{W},iw*({CX})*{Z}-{W}/2))"
    Y = f"max(0,min(ih*{Z}-{H},ih*({CY})*{Z}-{H}/2))"
    return f"zoompan=z='{Z}':x='{X}':y='{Y}':d=1:s={W}x{H}:fps={fps}"


def _caption_filters(events, W, H, font, workdir):
    fs = max(22, int(H * 0.031))
    out = []
    for k, c in enumerate(events):
        txt = os.path.join(workdir, f"capx_{k}.txt")
        open(txt, "w", encoding="utf-8").write(c["text"])
        kv = [f"textfile='{txt}'", "fontcolor=white", f"fontsize={fs}", "box=1",
              "boxcolor=black@0.58", f"boxborderw={int(fs*0.42)}", "line_spacing=6",
              "x=(w-text_w)/2", f"y=h-text_h-{int(H*0.062)}",
              f"enable='between(t,{c['start']:.3f},{c['end']:.3f})'"]
        if font:
            kv.insert(0, f"fontfile='{font}'")
        out.append("drawtext=" + ":".join(kv))
    return out


def build_vf(zoom_events, caption_events, W, H, fps, font, workdir):
    """Return the -vf string (zoom then captions), or None if there is nothing to do."""
    chain = []
    z = _zoom_filter(zoom_events or [], W, H, fps)
    if z:
        chain.append(z)
    chain += _caption_filters(caption_events or [], W, H, font, workdir)
    return ",".join(chain) if chain else None


def _px(e, W, H):
    x = int(min(1, max(0, e.get("x", 0))) * W); y = int(min(1, max(0, e.get("y", 0))) * H)
    w = max(2, int(min(1, e.get("w", 0.2)) * W)); h = max(2, int(min(1, e.get("h", 0.15)) * H))
    w -= w % 2; h -= h % 2
    x = min(x, W - w); y = min(y, H - h)
    return x, y, w, h


def _elt_linear(e, W, H, font, workdir, k):
    """box / redact / text -> a single 1-in-1-out filter."""
    x, y, w, h = _px(e, W, H)
    en = f"enable='between(t,{float(e['start']):.3f},{float(e['end']):.3f})'"
    col = (e.get("color") or "#FF6B57").lstrip("#")
    if e["type"] == "text":
        txt = os.path.join(workdir, f"elt_{k}.txt"); open(txt, "w", encoding="utf-8").write(e.get("text", ""))
        fs = int(e.get("size", 0.05) * H) if e.get("size") else max(24, int(H * 0.045))
        kv = [f"textfile='{txt}'", f"fontcolor=0x{col}", f"fontsize={fs}", f"x={x}", f"y={y}", en]
        if font:
            kv.insert(0, f"fontfile='{font}'")
        return "drawtext=" + ":".join(kv)
    if e["type"] == "redact":
        return f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black@1:t=fill:{en}"
    # box (outline)
    return f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x{col}@1:t=8:{en}"


def build_graph(zoom_events, caption_events, elements, W, H, fps, font, workdir):
    """Build a -filter_complex graph for zoom + captions + elements.
    Returns (graph_str, out_label, image_srcs). image_srcs are extra -i inputs
    the caller must add AFTER the base video (input index 1, 2, ...)."""
    elements = elements or []
    linear = []
    z = _zoom_filter(zoom_events or [], W, H, fps)
    if z:
        linear.append(z)
    linear += _caption_filters(caption_events or [], W, H, font, workdir)
    for k, e in enumerate(elements):
        if e.get("type") in ("box", "redact", "text"):
            linear.append(_elt_linear(e, W, H, font, workdir, k))

    graph, cur = [], "0:v"

    def emit(filters):
        nonlocal cur
        if not filters:
            return
        nl = f"L{len(graph)}"
        graph.append(f"[{cur}]" + ",".join(filters) + f"[{nl}]"); cur = nl
    emit(linear)

    for k, e in enumerate(elements):
        if e.get("type") != "blur":
            continue
        x, y, w, h = _px(e, W, H)
        en = f"enable='between(t,{float(e['start']):.3f},{float(e['end']):.3f})'"
        a, b, o = f"A{k}", f"B{k}", f"O{k}"
        graph.append(f"[{cur}]split[{a}][{b}]")
        graph.append(f"[{b}]crop={w}:{h}:{x}:{y},boxblur=20[{b}c]")
        graph.append(f"[{a}][{b}c]overlay={x}:{y}:{en}[{o}]"); cur = o

    image_srcs, iidx = [], 1
    for e in elements:
        if e.get("type") != "image" or not (e.get("src") and os.path.exists(e["src"])):
            continue
        x, y, w, h = _px(e, W, H)
        en = f"enable='between(t,{float(e['start']):.3f},{float(e['end']):.3f})'"
        image_srcs.append(e["src"]); si, o = f"S{iidx}", f"I{iidx}"
        graph.append(f"[{iidx}:v]scale={w}:{h}[{si}]")
        graph.append(f"[{cur}][{si}]overlay={x}:{y}:{en}[{o}]"); cur = o; iidx += 1

    return (";".join(graph) if graph else None), cur, image_srcs
