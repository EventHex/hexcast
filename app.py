"""Remaster — demo video studio (working name).
Wraps the pipeline: upload recording -> process (transcribe/clean/zoom/
voice/align/frame) -> edit script + branding -> re-render -> download.

Run:  python3 -m uvicorn app:app --port 8765   (from the repo root)
"""
from __future__ import annotations
import os, sys, json, subprocess, threading, uuid
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

ROOT = os.path.dirname(os.path.abspath(__file__))   # repo root
HERE = ROOT
# project data lives outside the repo when REMASTER_DATA_DIR is set
PROJECTS = os.environ.get("REMASTER_DATA_DIR") or os.path.join(ROOT, "projects")
os.makedirs(PROJECTS, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import config as cfgmod

app = FastAPI(title="Remaster")


@app.on_event("startup")
def _reap_orphans():
    """A previous server instance may have died mid-render, orphaning pipeline
    processes that keep writing into project files. Reap them on boot."""
    subprocess.run(["pkill", "-f", r"pipeline/(build_revoice|polish_export|transcribe)\.py"],
                   capture_output=True)
# Allow the Chrome extension (chrome-extension://) to hand recordings straight in.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/assets", StaticFiles(directory=os.path.join(HERE, "assets")), name="assets")
_EDITOR_DIST = os.path.join(HERE, "editor", "dist")
if os.path.isdir(_EDITOR_DIST):
    # React + Remotion Player editor (webstudio/editor). Build: cd editor && npm run build
    app.mount("/editor", StaticFiles(directory=_EDITOR_DIST, html=True), name="editor")
JOBS: dict = {}
JOBS_MAX = 200                      # keep the newest finished jobs, evict the rest
RENDERS = threading.Semaphore(2)    # at most 2 concurrent renders machine-wide
_PROJ_LOCKS: dict = {}              # one lock per project: overlapping jobs serialize
_LOCKS_GUARD = threading.Lock()


def _proj_lock(d: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _PROJ_LOCKS.setdefault(d, threading.Lock())


def proj_dir(pid: str) -> str:
    if "/" in pid or ".." in pid:
        raise HTTPException(400, "bad id")
    d = os.path.join(PROJECTS, pid)
    if not os.path.isdir(d):
        raise HTTPException(404, "no such project")
    return d


def _prune_jobs():
    done = [j for j, v in JOBS.items() if v.get("status") in ("done", "error", "cancelled")]
    for j in done[:max(0, len(JOBS) - JOBS_MAX)]:
        JOBS.pop(j, None)


def run_job(job: str, steps, cwd, proj: str, record_state=False):
    lock = _proj_lock(proj)
    JOBS[job] = {"status": "queued", "step": "waiting for a free slot", "log": [],
                 "error": None, "cancel": False, "progress": 0.0}
    with lock, RENDERS:
        if JOBS[job]["cancel"]:
            JOBS[job].update(status="cancelled", step="")
            return
        JOBS[job].update(status="running", step="")
        try:
            for desc, cmd in steps:
                if JOBS[job]["cancel"]:
                    JOBS[job].update(status="cancelled", step="")
                    return
                JOBS[job]["step"] = desc
                # own process group: cancelling kills ffmpeg grandchildren too,
                # never leaving orphan encoders writing into project files
                p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, start_new_session=True)
                JOBS[job]["_proc"] = p
                errbuf: list = []
                t = threading.Thread(target=lambda: errbuf.append(p.stderr.read() or ""), daemon=True)
                t.start()
                outlines = []
                # stream stdout: '@@P <frac>' lines drive the live progress bar,
                # everything else is job log
                for line in p.stdout:
                    if line.startswith("@@P "):
                        try:
                            JOBS[job]["progress"] = max(0.0, min(1.0, float(line[4:].strip())))
                        except ValueError:
                            pass
                    else:
                        outlines.append(line)
                    if JOBS[job]["cancel"]:
                        break
                p.wait()
                t.join(timeout=1)
                JOBS[job].pop("_proc", None)
                err = "".join(errbuf)
                JOBS[job]["log"].append(f"$ {desc}\n{''.join(outlines)[-1200:]}")
                if JOBS[job]["cancel"]:
                    JOBS[job].update(status="cancelled", step="")
                    return
                if p.returncode != 0:
                    JOBS[job].update(status="error", error=(err or "".join(outlines) or "")[-1500:])
                    return
            # record what was exported so the next export can skip unchanged stages
            if record_state:
                _write_state(proj)
            JOBS[job].update(status="done", progress=1.0)
        except Exception as e:
            JOBS[job].update(status="error", error=str(e))


@app.get("/", response_class=HTMLResponse)
def index():
    return open(os.path.join(HERE, "index.html"), encoding="utf-8").read()


@app.post("/api/projects")
def create_project():
    pid = "web-" + uuid.uuid4().hex[:8]
    os.makedirs(os.path.join(PROJECTS, pid), exist_ok=True)
    cfgmod.save(os.path.join(PROJECTS, pid), {})
    return {"id": pid}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    """Remove a project and everything in it (the uploaded video + all renders)."""
    d = proj_dir(pid)
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@app.post("/api/projects/{pid}/upload")
async def upload(pid: str, file: UploadFile = File(...)):
    d = proj_dir(pid)
    ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "webm").lower()
    if ext not in ("webm", "mp4", "mov", "mkv"):
        ext = "webm"
    # clear other raw.* so transcribe picks the new one
    for e in ("webm", "mp4", "mov", "mkv"):
        old = os.path.join(d, f"raw.{e}")
        if os.path.exists(old):
            os.remove(old)
    raw_path = os.path.join(d, f"raw.{ext}")
    with open(raw_path, "wb") as f:
        f.write(await file.read())
    if ext == "webm":
        # MediaRecorder webm usually lacks a duration header (probes read 0,
        # seeking is slow). Stream-copy remux writes proper metadata.
        fixed = os.path.join(d, "_fixed.webm")
        p = subprocess.run(["ffmpeg", "-y", "-i", raw_path, "-c", "copy", fixed], capture_output=True)
        if p.returncode == 0 and os.path.getsize(fixed) > 0:
            os.replace(fixed, raw_path)
        else:
            try: os.remove(fixed)
            except OSError: pass
    return {"ok": True, "raw": f"raw.{ext}"}


@app.post("/api/projects/{pid}/logo")
async def upload_logo(pid: str, file: UploadFile = File(...)):
    d = proj_dir(pid)
    path = os.path.join(d, "logo.png")
    with open(path, "wb") as f:
        f.write(await file.read())
    c = cfgmod.load(d); c["logo"] = path; cfgmod.save(d, c)
    return {"ok": True}


MUSIC_DIR = os.path.join(HERE, "assets", "music")
BUILTIN_MUSIC = os.path.join(MUSIC_DIR, "ambient.mp3")


@app.get("/api/music")
def music_list():
    """Built-in bed library (assets/music/*.mp3) — the UI builds its picker from this."""
    return {"tracks": sorted(f[:-4] for f in os.listdir(MUSIC_DIR) if f.endswith(".mp3"))}


@app.get("/api/sfx")
def sfx_list():
    """Built-in sound effects (assets/sfx/*.wav) placeable on the editor timeline."""
    d = os.path.join(HERE, "assets", "sfx")
    return {"sfx": sorted(f[:-4] for f in os.listdir(d) if f.endswith(".wav"))}


@app.post("/api/projects/{pid}/music")
async def upload_music(pid: str, file: UploadFile = File(...)):
    d = proj_dir(pid)
    ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "mp3").lower()
    if ext not in ("mp3", "m4a", "wav", "aac", "ogg"):
        ext = "mp3"
    path = os.path.join(d, f"music.{ext}")
    with open(path, "wb") as f:
        f.write(await file.read())
    c = cfgmod.load(d); c["music"] = path; cfgmod.save(d, c)
    return {"ok": True, "music": "upload"}


@app.post("/api/projects/{pid}/music/{choice}")
def set_music(pid: str, choice: str):
    """choice: a built-in bed name (assets/music/<name>.mp3), 'builtin' (=ambient), or 'off'."""
    d = proj_dir(pid)
    c = cfgmod.load(d)
    track = os.path.join(MUSIC_DIR, f"{choice}.mp3")
    if choice == "off":
        c["music"] = None
    elif choice == "builtin":
        c["music"] = BUILTIN_MUSIC
    elif "/" not in choice and ".." not in choice and os.path.isfile(track):
        c["music"] = track
    else:
        raise HTTPException(400, "unknown track")
    cfgmod.save(d, c)
    return {"ok": True, "music": choice}


def _snapshot(d):
    p = os.path.join(d, "script.json")
    if os.path.exists(p):
        import shutil; shutil.copy(p, os.path.join(d, "script.prev.json"))


@app.post("/api/projects/{pid}/script/revert")
def script_revert(pid: str):
    """Undo the last AI rewrite / edit by swapping in the previous snapshot."""
    d = proj_dir(pid)
    cur = os.path.join(d, "script.json"); prev = os.path.join(d, "script.prev.json")
    if not os.path.exists(prev):
        raise HTTPException(404, "no previous version")
    import shutil
    tmp = cur + ".tmp"; shutil.copy(cur, tmp); shutil.move(prev, cur); shutil.move(tmp, prev)
    return json.load(open(cur, encoding="utf-8"))


@app.post("/api/projects/{pid}/background")
async def upload_background(pid: str, file: UploadFile = File(...)):
    d = proj_dir(pid)
    ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg").lower()
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"
    path = os.path.join(d, f"background.{ext}")
    with open(path, "wb") as f:
        f.write(await file.read())
    c = cfgmod.load(d); c["background"] = path; cfgmod.save(d, c)
    return {"ok": True}


@app.post("/api/projects/{pid}/background/off")
def background_off(pid: str):
    d = proj_dir(pid); c = cfgmod.load(d); c["background"] = None; cfgmod.save(d, c)
    return {"ok": True}


@app.post("/api/projects/{pid}/element-image")
async def element_image(pid: str, file: UploadFile = File(...)):
    d = proj_dir(pid)
    import uuid as _u
    path = os.path.join(d, "seg", f"img_{_u.uuid4().hex[:8]}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(await file.read())
    return {"ok": True, "src": path}


@app.post("/api/projects/{pid}/script/rewrite")
def rewrite_script(pid: str):
    """Rewrite every spoken line with AI (clarity/flow), preserving order + count."""
    d = proj_dir(pid)
    p = os.path.join(d, "script.json")
    if not os.path.exists(p):
        raise HTTPException(404, "no script")
    _snapshot(d)
    data = json.load(open(p, encoding="utf-8"))
    segs = data.get("segments", [])
    idx = [i for i, s in enumerate(segs) if s.get("type", "clip") in ("clip", "added") and s.get("en")]
    if not idx:
        return data
    sys.path.insert(0, os.path.join(ROOT, "pipeline"))
    import cerebras_clean
    cfg = cfgmod.load(d)
    new = cerebras_clean.rewrite_lines([segs[i]["en"] for i in idx], glossary=cfg.get("glossary"))
    for j, i in enumerate(idx):
        segs[i]["en"] = new[j]
    json.dump(data, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return data


@app.post("/api/projects/{pid}/events")
async def upload_events(pid: str, body: dict = Body(...)):
    """Interaction event track from the recorder extension (clicks drive zoom)."""
    d = proj_dir(pid)
    json.dump(body, open(os.path.join(d, "events.json"), "w", encoding="utf-8"))
    n = len(body.get("events") or [])
    return {"ok": True, "events": n}


import hashlib


def _sig(*xs):
    return hashlib.sha1(repr(xs).encode()).hexdigest()[:12]


def _sigs(cfg, script):
    """Fingerprint each render stage's inputs so an export runs only the cheapest
    stage that a given edit actually invalidates:
      voiced -> TTS + segment clips + base concat (heaviest)
      cards  -> intro/outro cards baked into base -> base concat
      fx     -> zoom/caption/element/music/sfx pass on base
      frame  -> theme plate / background / shadow / logo framing
    """
    segs = script.get("segments") or []
    return {
        "voiced": _sig(cfg.get("voice"), cfg.get("lang"), cfg.get("original_voice"),
                       [s.get("en") for s in segs], [s.get("type") for s in segs],
                       [(s.get("start"), s.get("end"), s.get("dur"), s.get("anchor")) for s in segs]),
        "cards": _sig(cfg.get("title"), cfg.get("subtitle"), cfg.get("outro_title"),
                      cfg.get("outro_subtitle"), cfg.get("intro_dur"), cfg.get("outro_dur"),
                      cfg.get("card_style"), cfg.get("card_top"), cfg.get("card_bottom"),
                      cfg.get("brand_top"), cfg.get("brand_bottom"), cfg.get("logo"), cfg.get("transition")),
        "fx": _sig(script.get("zooms"), script.get("zoomsEdited"), script.get("elements"),
                   script.get("sounds"), cfg.get("captions"), cfg.get("music"), cfg.get("music_gain"),
                   cfg.get("sfx_clicks"), cfg.get("sfx_zoom")),
        "frame": _sig(cfg.get("frame_theme"), cfg.get("bg_style"), cfg.get("background"),
                      cfg.get("shadow"), cfg.get("radius"), cfg.get("padding"), cfg.get("logo_corner"),
                      cfg.get("vertical_stack"), cfg.get("browser_url"), cfg.get("aspects"), cfg.get("logo")),
    }


@app.post("/api/projects/{pid}/apply-fx")
@app.post("/api/projects/{pid}/export")
def export_project(pid: str):
    """Smart export: diff the current config/script against what was last
    exported and run only the stages that changed (frame-only, fx+frame, or a
    full re-voice). Nothing changed -> no job, files already current."""
    d = proj_dir(pid)
    cfg = cfgmod.load(d)
    sp = os.path.join(d, "script.json")
    script = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {"segments": []}
    cur = _sigs(cfg, script)
    prev = {}
    statef = os.path.join(d, "render_state.json")
    if os.path.exists(statef):
        try:
            prev = json.load(open(statef, encoding="utf-8"))
        except Exception:
            prev = {}

    has_base = os.path.exists(os.path.join(d, "base.mp4"))
    has_revoiced = os.path.exists(os.path.join(d, "revoiced.mp4"))
    aspects = cfg.get("aspects") or ["16x9"]
    has_frames = all(os.path.exists(os.path.join(d, f"framed-{a}.mp4")) for a in aspects)

    need_full = not has_base or cur["voiced"] != prev.get("voiced") or cur["cards"] != prev.get("cards")
    need_fx = need_full or not has_revoiced or cur["fx"] != prev.get("fx")
    need_frame = need_fx or not has_frames or cur["frame"] != prev.get("frame")

    if not need_frame:
        return {"done": True, "nothing": True}

    steps = []
    if need_full:
        steps.append(("Re-voicing & rendering", ["pipeline/build_revoice.py", "{REL}", "--from-script"]))
    elif need_fx:
        steps.append(("Applying zoom, captions & sound", ["pipeline/build_revoice.py", "{REL}", "--fx-only"]))
    steps.append(("Framing & exporting", ["pipeline/polish_export.py", "{REL}"]))
    return _spawn(d, steps, record_state=True)


def _write_state(d):
    """Snapshot the current stage signatures AFTER a render, reading config +
    script from disk — the render itself may have rewritten the script (e.g.
    translation), so pre-render sigs would be stale and re-trigger a full pass."""
    try:
        cfg = cfgmod.load(d)
        sp = os.path.join(d, "script.json")
        script = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {"segments": []}
        json.dump(_sigs(cfg, script), open(os.path.join(d, "render_state.json"), "w"))
    except Exception:
        pass


def _spawn(d, steps_defs, record_state=False):
    _prune_jobs()
    job = uuid.uuid4().hex[:8]
    # absolute project dir: works with REMASTER_DATA_DIR outside the repo;
    # cwd stays ROOT only so the pipeline scripts' imports + .env resolve
    steps = [(desc, [sys.executable] + [a.replace("{REL}", d) for a in args]) for desc, args in steps_defs]
    threading.Thread(target=run_job, args=(job, steps, ROOT, d),
                     kwargs={"record_state": record_state}, daemon=True).start()
    return {"job": job}


@app.post("/api/projects/{pid}/prepare")
def prepare(pid: str):
    # Phase 1: transcribe + clean + zoom -> editable script.json. No render.
    return _spawn(proj_dir(pid), [
        ("Transcribing", ["pipeline/transcribe.py", "{REL}"]),
        ("Reading the script", ["pipeline/build_revoice.py", "{REL}", "--script-only"]),
    ])


def _probe(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration", "-of", "json", path],
            capture_output=True, text=True).stdout
        m = json.loads(out)
        st = (m.get("streams") or [{}])[0]
        return {"dur": float(m.get("format", {}).get("duration") or 0),
                "w": int(st.get("width") or 1920), "h": int(st.get("height") or 1080)}
    except Exception:
        return {"dur": 0, "w": 1920, "h": 1080}


@app.get("/api/projects/{pid}/source")
def source(pid: str):
    d = proj_dir(pid)
    raw = next((f"raw.{e}" for e in ("webm", "mp4", "mov", "mkv") if os.path.exists(os.path.join(d, f"raw.{e}"))), None)
    # preview source for the React editor: the voiced concat (fx applies live on
    # top of it in the Player) or, before the first render, the raw upload
    pv = next((f for f in ("base.mp4", raw) if f and os.path.exists(os.path.join(d, f))), None)
    preview = None
    if pv:
        preview = {"file": pv, **_probe(os.path.join(d, pv))}
        preview["voiced"] = pv == "base.mp4"
    return {
        "raw": raw,
        "preview": preview,
        "has_script": os.path.exists(os.path.join(d, "script.json")),
        "generated": os.path.exists(os.path.join(d, "framed-16x9.mp4")) or os.path.exists(os.path.join(d, "framed-9x16.mp4")),
    }


@app.post("/api/projects/{pid}/process")
def process(pid: str):
    return _spawn(proj_dir(pid), [
        ("Transcribing", ["pipeline/transcribe.py", "{REL}"]),
        ("Clean + zoom + voice + align", ["pipeline/build_revoice.py", "{REL}"]),
        ("Frame + export", ["pipeline/polish_export.py", "{REL}"]),
    ])


@app.post("/api/projects/{pid}/render")
def render(pid: str):
    return _spawn(proj_dir(pid), [
        ("Re-render from edited script", ["pipeline/build_revoice.py", "{REL}", "--from-script"]),
        ("Frame + export", ["pipeline/polish_export.py", "{REL}"]),
    ])


@app.get("/api/jobs/{job}")
def job_status(job: str):
    j = JOBS.get(job)
    if j is None:
        raise HTTPException(404, "no such job")
    return {k: v for k, v in j.items() if not k.startswith("_")}


@app.post("/api/jobs/{job}/cancel")
def job_cancel(job: str):
    j = JOBS.get(job)
    if j is None:
        raise HTTPException(404, "no such job")
    j["cancel"] = True
    p = j.get("_proc")
    if p is not None:
        try:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
    return {"ok": True}


@app.get("/api/projects/{pid}/script")
def get_script(pid: str):
    p = os.path.join(proj_dir(pid), "script.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"segments": []}


@app.put("/api/projects/{pid}/script")
def put_script(pid: str, body: dict = Body(...)):
    json.dump(body, open(os.path.join(proj_dir(pid), "script.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return {"ok": True}


@app.get("/api/projects/{pid}/config")
def get_config(pid: str):
    return cfgmod.load(proj_dir(pid))


@app.put("/api/projects/{pid}/config")
def put_config(pid: str, body: dict = Body(...)):
    return cfgmod.save(proj_dir(pid), body)


@app.get("/media/{pid}/{name:path}")
def media(pid: str, name: str):
    """Serve a project file. Containment-based check (realpath must stay inside
    the project dir) so nested paths like seg/tts_x.mp3 work but ../ cannot."""
    d = os.path.realpath(proj_dir(pid))
    p = os.path.realpath(os.path.join(d, name))
    if not (p == d or p.startswith(d + os.sep)):
        raise HTTPException(400, "bad name")
    if not os.path.isfile(p):
        raise HTTPException(404, "not found")
    return FileResponse(p)
