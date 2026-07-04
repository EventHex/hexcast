"""Remaster — demo video studio (working name).
Wraps the pipeline: upload recording -> process (transcribe/clean/zoom/
voice/align/frame) -> edit script + branding -> re-render -> download.

Run:  python3 -m uvicorn app:app --port 8765   (from the repo root)
"""
from __future__ import annotations
import os, sys, json, subprocess, threading, uuid
from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.abspath(__file__))   # repo root
load_dotenv(os.path.join(ROOT, ".env"))   # so /api/settings reflects .env keys too
HERE = ROOT
# project data lives outside the repo when REMASTER_DATA_DIR is set
PROJECTS = os.environ.get("REMASTER_DATA_DIR") or os.path.join(ROOT, "projects")
os.makedirs(PROJECTS, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import config as cfgmod
import brands as brandsmod
from providers import settings as settingsmod

VERSION = "0.1.0"
_EXT_SEEN = {"at": 0.0}   # last time the recorder extension pinged us

app = FastAPI(title="Remaster")


@app.on_event("startup")
def _reap_orphans():
    """A previous server instance may have died mid-render, orphaning pipeline
    processes that keep writing into project files. Reap them on boot."""
    subprocess.run(["pkill", "-f", r"pipeline/(build_revoice|polish_export|transcribe)\.py"],
                   capture_output=True)
    brandsmod.seed_default(PROJECTS, os.path.join(HERE, "assets"))
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


def run_job(job: str, steps, cwd, proj: str, record_state=False, env=None):
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
                                     text=True, start_new_session=True,
                                     env={**os.environ, **(env or {})})
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


@app.get("/")
def index(request: Request):
    # old links / the recorder extension open /?project=<id> — keep that working
    q = str(request.query_params)
    return RedirectResponse("/editor/" + (f"?{q}" if q else ""))


def _proj_size(d):
    total = 0
    for root, _, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


HEAVY = ("base.mp4", "revoiced.mp4", "audio.mp3")   # regenerable from raw; raw itself is the biggest
HEAVY_DIRS = ("seg", "zoomframes")


def prune_project(d, keep_raw=False):
    """Drop regenerable intermediates (and raw unless keep_raw); keep
    config/script/exports. Returns bytes freed."""
    freed = 0
    import shutil, glob
    victims = [os.path.join(d, f) for f in HEAVY]
    if not keep_raw:
        victims += glob.glob(os.path.join(d, "raw.*"))
    for p in victims:
        if os.path.isfile(p):
            freed += os.path.getsize(p)
            os.remove(p)
    for sub in HEAVY_DIRS:
        p = os.path.join(d, sub)
        if os.path.isdir(p):
            freed += _proj_size(p)
            shutil.rmtree(p, ignore_errors=True)
    return freed


def auto_prune():
    """Retention policy from settings: prune exported projects untouched for N days."""
    days = int((settingsmod.load_settings(PROJECTS).get("retention") or {}).get("days") or 0)
    if days <= 0:
        return
    import time as _t
    cutoff = _t.time() - days * 86400
    for pid in os.listdir(PROJECTS):
        d = os.path.join(PROJECTS, pid)
        if not os.path.isdir(d) or pid == "brands":
            continue
        exported = any(f.startswith("framed-") for f in os.listdir(d))
        if exported and os.path.getmtime(d) < cutoff:
            freed = prune_project(d)
            if freed:
                print(f"retention: pruned {pid} ({freed // 1048576} MB)")


@app.on_event("startup")
def _startup_prune():
    threading.Thread(target=auto_prune, daemon=True).start()


@app.get("/api/projects")
def list_projects():
    out = []
    for pid in os.listdir(PROJECTS):
        d = os.path.join(PROJECTS, pid)
        if not os.path.isdir(d) or pid == "brands":
            continue
        cfgp = os.path.join(d, "config.json")
        name = pid
        if os.path.isfile(cfgp):
            try:
                name = json.load(open(cfgp, encoding="utf-8")).get("name") or pid
            except Exception:
                pass
        has_raw = any(os.path.exists(os.path.join(d, f"raw.{e}")) for e in ("webm", "mp4", "mov", "mkv"))
        exported = any(f.startswith("framed-") and f.endswith(".mp4") for f in os.listdir(d))
        status = "exported" if exported else \
                 "ready" if os.path.exists(os.path.join(d, "script.json")) else \
                 "recorded" if has_raw else "empty"
        out.append({"id": pid, "name": name, "status": status,
                    "mtime": os.path.getmtime(d), "size": _proj_size(d)})
    out.sort(key=lambda p: -p["mtime"])
    return {"projects": out}


@app.put("/api/projects/{pid}/name")
def rename_project(pid: str, body: dict = Body(...)):
    d = proj_dir(pid)
    c = cfgmod.load(d); c["name"] = str(body.get("name") or "").strip() or pid
    cfgmod.save(d, c)
    return {"ok": True, "name": c["name"]}


@app.post("/api/projects/{pid}/duplicate")
def duplicate_project(pid: str):
    """New project inheriting this one's settings/brand (no media)."""
    src = proj_dir(pid)
    new = "web-" + uuid.uuid4().hex[:8]
    nd = os.path.join(PROJECTS, new)
    os.makedirs(nd, exist_ok=True)
    c = cfgmod.load(src)
    c["name"] = (c.get("name") or pid) + " copy"
    # carry per-project style files so the copy is truly self-contained
    for k in ("logo", "background", "music"):
        p = c.get(k)
        if p and str(p).startswith(os.path.abspath(src) + os.sep) and os.path.isfile(p):
            import shutil
            dst = os.path.join(nd, os.path.basename(p))
            shutil.copy(p, dst)
            c[k] = dst
    cfgmod.save(nd, c)
    return {"id": new}


@app.get("/api/projects/{pid}/thumb")
def project_thumb(pid: str):
    d = proj_dir(pid)
    thumb = os.path.join(d, "thumb.jpg")
    if not os.path.isfile(thumb):
        src = next((os.path.join(d, f) for f in
                    ("base.mp4", "raw.webm", "raw.mp4", "raw.mov", "raw.mkv")
                    if os.path.exists(os.path.join(d, f))), None)
        if not src:
            raise HTTPException(404, "no media")
        subprocess.run(["ffmpeg", "-y", "-ss", "1", "-i", src, "-frames:v", "1",
                        "-vf", "scale=480:-1", "-q:v", "6", thumb], capture_output=True)
        if not os.path.isfile(thumb):
            raise HTTPException(404, "no thumb")
    return FileResponse(thumb)


@app.post("/api/prune")
def prune(body: dict = Body(default={})):
    """Manual prune: {'ids': [...]} or {'days': N}; dry=true lists what would go."""
    dry = bool(body.get("dry"))
    ids = body.get("ids")
    days = int(body.get("days") or 0)
    import time as _t
    cutoff = _t.time() - days * 86400 if days else None
    hits = []
    for pid in (ids or os.listdir(PROJECTS)):
        d = os.path.join(PROJECTS, pid)
        if not os.path.isdir(d) or pid == "brands":
            continue
        if cutoff and os.path.getmtime(d) >= cutoff:
            continue
        if dry:
            hits.append({"id": pid, "size": _proj_size(d)})
        else:
            hits.append({"id": pid, "freed": prune_project(d)})
    return {"pruned": hits, "dry": dry}


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
    os.environ.update(settingsmod.provider_env(PROJECTS))  # in-process call: same key routing as subprocesses
    cfg = cfgmod.load(d)
    new = cerebras_clean.rewrite_lines([segs[i]["en"] for i in idx], glossary=cfg.get("glossary"))
    for j, i in enumerate(idx):
        segs[i]["en"] = new[j]
    json.dump(data, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return data


@app.post("/api/projects/{pid}/events")
async def upload_events(pid: str, body: dict = Body(...)):
    """Interaction event track from the recorder extension (clicks drive zoom)."""
    import time as _t
    _EXT_SEEN["at"] = _t.time()
    d = proj_dir(pid)
    json.dump(body, open(os.path.join(d, "events.json"), "w", encoding="utf-8"))
    n = len(body.get("events") or [])
    return {"ok": True, "events": n}


@app.get("/api/ping")
def ext_ping():
    """Lightweight endpoint the recorder extension can hit so the app knows
    it's installed (drives the 'extension detected' hint)."""
    import time as _t
    _EXT_SEEN["at"] = _t.time()
    return {"ok": True, "version": VERSION}


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
    tts_provider = settingsmod.provider_env(PROJECTS).get("REMASTER_TTS_PROVIDER", "auto")
    return {
        "voiced": _sig(cfg.get("voice"), cfg.get("lang"), cfg.get("original_voice"), tts_provider,
                       [s.get("en") for s in segs], [s.get("type") for s in segs],
                       [(s.get("start"), s.get("end"), s.get("dur"), s.get("anchor")) for s in segs]),
        "cards": _sig(cfg.get("title"), cfg.get("subtitle"), cfg.get("outro_title"),
                      cfg.get("outro_subtitle"), cfg.get("intro_dur"), cfg.get("outro_dur"),
                      cfg.get("card_style"), cfg.get("card_top"), cfg.get("card_bottom"),
                      cfg.get("brand_top"), cfg.get("brand_bottom"), cfg.get("logo"), cfg.get("transition"),
                      cfg.get("font"), cfg.get("card_align"), cfg.get("card_title_color"),
                      cfg.get("card_sub_color"), cfg.get("card_scale")),
        "fx": _sig(script.get("zooms"), script.get("zoomsEdited"), script.get("elements"),
                   script.get("sounds"), cfg.get("captions"), cfg.get("music"), cfg.get("music_gain"),
                   cfg.get("sfx_clicks"), cfg.get("sfx_zoom"),
                   cfg.get("font"), cfg.get("cap_pos"), cfg.get("cap_scale"), cfg.get("cap_color"),
                   cfg.get("cap_bg"), cfg.get("cap_bg_opacity")),
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
                     kwargs={"record_state": record_state,
                             "env": settingsmod.provider_env(PROJECTS)}, daemon=True).start()
    return {"job": job}


# default Chirp3-HD voice per target language — mirrors the editor's LANGS list
LANG_VOICES = {
    "English": "en-IN-Chirp3-HD-Aoede", "Hindi": "hi-IN-Chirp3-HD-Aoede",
    "Tamil": "ta-IN-Chirp3-HD-Aoede", "Telugu": "te-IN-Chirp3-HD-Aoede",
    "Malayalam": "ml-IN-Chirp3-HD-Aoede", "Kannada": "kn-IN-Chirp3-HD-Aoede",
    "Bengali": "bn-IN-Chirp3-HD-Aoede", "Gujarati": "gu-IN-Chirp3-HD-Aoede",
    "Marathi": "mr-IN-Chirp3-HD-Aoede", "Arabic": "ar-XA-Chirp3-HD-Aoede",
    "Spanish": "es-US-Chirp3-HD-Aoede", "French": "fr-FR-Chirp3-HD-Aoede",
    "German": "de-DE-Chirp3-HD-Aoede", "Portuguese": "pt-BR-Chirp3-HD-Aoede",
    "Japanese": "ja-JP-Chirp3-HD-Aoede", "Korean": "ko-KR-Chirp3-HD-Aoede",
    "Indonesian": "id-ID-Chirp3-HD-Aoede",
}


@app.post("/api/projects/{pid}/export-langs")
def export_langs(pid: str, body: dict = Body(...)):
    """Batch: same script, N languages. Each language becomes its own derived
    project (translated script + language voice), rendered sequentially in one
    job — the derived projects show up in the Library with their exports."""
    d = proj_dir(pid)
    if not os.path.exists(os.path.join(d, "script.json")):
        raise HTTPException(400, "prepare the script first")
    langs = [l for l in (body.get("langs") or []) if l in LANG_VOICES]
    if not langs:
        raise HTTPException(400, "no valid languages")
    import re, shutil
    src_cfg = cfgmod.load(d)
    steps = []
    for lang in langs:
        slug = re.sub(r"[^a-z]", "", lang.lower())[:12]
        nd = os.path.join(PROJECTS, f"{pid}-{slug}")
        os.makedirs(nd, exist_ok=True)
        c = dict(src_cfg)
        c["lang"] = lang
        c["voice"] = LANG_VOICES[lang]
        c["name"] = f"{src_cfg.get('name') or pid} ({lang})"
        # per-project style files: point the clone at its own copies
        for k in ("logo", "background", "music"):
            p = c.get(k)
            if p and str(p).startswith(os.path.abspath(d) + os.sep) and os.path.isfile(p):
                shutil.copy(p, os.path.join(nd, os.path.basename(p)))
                c[k] = os.path.join(nd, os.path.basename(p))
        cfgmod.save(nd, c)
        shutil.copy(os.path.join(d, "script.json"), os.path.join(nd, "script.json"))
        for e in ("webm", "mp4", "mov", "mkv"):
            s, t = os.path.join(d, f"raw.{e}"), os.path.join(nd, f"raw.{e}")
            if os.path.exists(s) and not os.path.exists(t):
                try:
                    os.link(s, t)          # hardlink: no extra disk for the raw
                except OSError:
                    shutil.copy(s, t)
        if os.path.exists(os.path.join(d, "events.json")):
            shutil.copy(os.path.join(d, "events.json"), nd)
        steps.append((f"{lang}: translate + revoice", ["pipeline/build_revoice.py", nd, "--from-script"]))
        steps.append((f"{lang}: framing & export", ["pipeline/polish_export.py", nd]))
    return _spawn(d, steps)


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


_SAMPLE_DIR = os.path.join(HERE, "assets", "sample")   # optional bundled starter project


@app.get("/api/health")
def health():
    import time as _t
    return {"version": VERSION,
            "extension_seen": (_t.time() - _EXT_SEEN["at"]) < 120,
            "has_sample": os.path.isdir(_SAMPLE_DIR)}


@app.post("/api/sample")
def make_sample():
    """Clone the bundled sample into a new project so a first-time user has
    something to open immediately. No-op (404) if no sample ships."""
    if not os.path.isdir(_SAMPLE_DIR):
        raise HTTPException(404, "no bundled sample")
    import shutil
    pid = "sample-" + uuid.uuid4().hex[:6]
    d = os.path.join(PROJECTS, pid)
    shutil.copytree(_SAMPLE_DIR, d)
    c = cfgmod.load(d); c["name"] = "Sample demo"; cfgmod.save(d, c)
    return {"id": pid}


@app.get("/api/brands")
def brands_list():
    return {"brands": brandsmod.list_brands(PROJECTS)}


@app.get("/api/brands/{bid}")
def brands_get(bid: str):
    try:
        return brandsmod.get_brand(PROJECTS, bid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such brand")


@app.post("/api/brands/{bid}/logo")
async def brand_logo(bid: str, file: UploadFile = File(...)):
    try:
        d = brandsmod._bdir(PROJECTS, bid)
    except ValueError:
        raise HTTPException(400, "bad brand id")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "logo.png")
    with open(path, "wb") as f:
        f.write(await file.read())
    b = brandsmod.get_brand(PROJECTS, bid)
    cfg = b.get("config") or {}
    cfg["logo"] = path
    brandsmod.save_brand(PROJECTS, bid, b.get("name"), cfg)
    return {"ok": True, "logo": path}


@app.get("/api/brands/{bid}/logo")
def brand_logo_get(bid: str):
    try:
        p = os.path.join(brandsmod._bdir(PROJECTS, bid), "logo.png")
    except ValueError:
        raise HTTPException(400, "bad brand id")
    if not os.path.isfile(p):
        raise HTTPException(404, "no logo")
    return FileResponse(p)


@app.post("/api/brands")
def brands_create(body: dict = Body(...)):
    bid = brandsmod.create_brand(PROJECTS, body.get("name"), body.get("config"))
    return {"id": bid}


@app.put("/api/brands/{bid}")
def brands_update(bid: str, body: dict = Body(...)):
    try:
        cur = brandsmod.get_brand(PROJECTS, bid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such brand")
    cfg = {**(cur.get("config") or {}), **(body.get("config") or {})}
    return brandsmod.save_brand(PROJECTS, bid, body.get("name") or cur.get("name"), cfg)


@app.delete("/api/brands/{bid}")
def brands_delete(bid: str):
    try:
        brandsmod.delete_brand(PROJECTS, bid)
    except ValueError:
        raise HTTPException(400, "bad brand id")
    return {"ok": True}


@app.post("/api/projects/{pid}/apply-brand/{bid}")
def apply_brand(pid: str, bid: str):
    d = proj_dir(pid)
    try:
        return brandsmod.apply_to_project(PROJECTS, bid, d, cfgmod)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such brand")


@app.post("/api/brands/from-project/{pid}")
def brand_from_project(pid: str, body: dict = Body(...)):
    d = proj_dir(pid)
    bid = brandsmod.from_project(PROJECTS, body.get("name") or "My brand", d, cfgmod)
    return {"id": bid}


@app.get("/api/voices")
def voices(provider: str = "elevenlabs"):
    """Live voice list for pickable TTS providers (id, name, preview_url)."""
    os.environ.update(settingsmod.provider_env(PROJECTS))
    from providers import tts as ttsmod
    try:
        return {"voices": ttsmod.list_voices(provider)}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/settings")
def get_settings():
    return settingsmod.masked_view(PROJECTS)


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    settingsmod.save_settings(PROJECTS, body)
    return settingsmod.masked_view(PROJECTS)


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


def _caption_cues(d):
    """Timed narration cues from script.json (rstart + duration), for SRT/VTT."""
    sp = os.path.join(d, "script.json")
    if not os.path.exists(sp):
        return []
    segs = json.load(open(sp, encoding="utf-8")).get("segments") or []
    cues = []
    for s in segs:
        if s.get("type") == "scene" or s.get("rstart") is None or not s.get("en"):
            continue
        start = float(s["rstart"])
        dur = float(s.get("rdur") or s.get("tts_dur") or 0) or 2.0
        cues.append((start, start + dur, s["en"].strip()))
    return cues


def _ts(t, sep):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", sep)


@app.get("/api/projects/{pid}/captions.{ext}")
def captions(pid: str, ext: str):
    d = proj_dir(pid)
    cues = _caption_cues(d)
    if ext == "vtt":
        body = "WEBVTT\n\n" + "\n\n".join(
            f"{_ts(a, '.')} --> {_ts(b, '.')}\n{t}" for a, b, t in cues)
        media = "text/vtt"
    else:  # srt
        body = "\n\n".join(
            f"{i+1}\n{_ts(a, ',')} --> {_ts(b, ',')}\n{t}" for i, (a, b, t) in enumerate(cues))
        media = "application/x-subrip"
    from fastapi.responses import Response
    return Response(body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{pid}.{ext}"'})


@app.get("/api/projects/{pid}/transcript.txt")
def transcript(pid: str):
    d = proj_dir(pid)
    sp = os.path.join(d, "script.json")
    segs = json.load(open(sp, encoding="utf-8")).get("segments") or [] if os.path.exists(sp) else []
    lines = []
    for s in segs:
        if s.get("type") == "scene":
            lines.append(f"\n## {s.get('title', '')}\n")
        elif s.get("en"):
            lines.append(s["en"].strip())
    from fastapi.responses import Response
    return Response("\n".join(lines).strip() + "\n", media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{pid}.txt"'})


@app.get("/api/projects/{pid}/audio.mp3")
def audio_only(pid: str):
    """Narration-only MP3, extracted from the rendered video."""
    d = proj_dir(pid)
    src = next((os.path.join(d, f) for f in ("framed-16x9.mp4", "revoiced.mp4")
                if os.path.exists(os.path.join(d, f))), None)
    if not src:
        raise HTTPException(404, "render first")
    out = os.path.join(d, "narration.mp3")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(src):
        subprocess.run(["ffmpeg", "-y", "-i", src, "-vn", "-c:a", "libmp3lame", "-q:a", "2", out],
                       capture_output=True)
    if not os.path.exists(out):
        raise HTTPException(500, "audio extract failed")
    return FileResponse(out, filename=f"{pid}.mp3")


@app.get("/api/projects/{pid}/poster.png")
def poster(pid: str):
    """A shareable thumbnail — a frame from the exported video."""
    d = proj_dir(pid)
    src = next((os.path.join(d, f) for f in ("framed-16x9.mp4", "framed-9x16.mp4", "framed-1x1.mp4")
                if os.path.exists(os.path.join(d, f))), None)
    if not src:
        raise HTTPException(404, "export first")
    out = os.path.join(d, "poster.png")
    subprocess.run(["ffmpeg", "-y", "-ss", "1.2", "-i", src, "-frames:v", "1", out], capture_output=True)
    if not os.path.exists(out):
        raise HTTPException(500, "poster failed")
    return FileResponse(out, filename=f"{pid}-poster.png")


@app.get("/api/projects/{pid}/preview.gif")
def preview_gif(pid: str):
    """A short looping GIF from the start of the export — for social/README."""
    d = proj_dir(pid)
    src = next((os.path.join(d, f"framed-{a}.mp4") for a in ("16x9", "9x16", "1x1")
                if os.path.exists(os.path.join(d, f"framed-{a}.mp4"))), None)
    if not src:
        raise HTTPException(404, "export first")
    out = os.path.join(d, "preview.gif")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(src):
        subprocess.run(["ffmpeg", "-y", "-t", "6", "-i", src,
                        "-vf", "fps=12,scale=640:-1:flags=lanczos", "-loop", "0", out], capture_output=True)
    if not os.path.exists(out):
        raise HTTPException(500, "gif failed")
    return FileResponse(out, filename=f"{pid}.gif")


@app.post("/api/projects/{pid}/reveal")
def reveal(pid: str):
    """Open the project's export in the OS file browser (macOS/Linux)."""
    d = proj_dir(pid)
    target = next((os.path.join(d, f"framed-{a}.mp4") for a in ("16x9", "9x16", "1x1")
                   if os.path.exists(os.path.join(d, f"framed-{a}.mp4"))), d)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", target])
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", os.path.dirname(target)])
    else:
        raise HTTPException(400, "unsupported platform")
    return {"ok": True}


def _doc_steps(d):
    """Ordered (title, text, frame_time_on_base) steps from the script."""
    sp = os.path.join(d, "script.json")
    if not os.path.exists(sp):
        return []
    segs = json.load(open(sp, encoding="utf-8")).get("segments") or []
    steps = []
    for s in segs:
        if s.get("type") == "scene":
            steps.append(("section", s.get("title", ""), None))
        elif s.get("en") and s.get("rstart") is not None:
            mid = float(s["rstart"]) + float(s.get("rdur") or s.get("tts_dur") or 1) / 2
            steps.append(("step", s["en"].strip(), mid))
    return steps


@app.get("/api/projects/{pid}/doc.html")
def doc_html(pid: str):
    """Self-contained step-by-step guide (SOP) from the recording: each
    narration line becomes a numbered step with a screenshot at that moment."""
    import base64, html as _html
    from fastapi.responses import Response
    d = proj_dir(pid)
    cfg = cfgmod.load(d)
    base = next((os.path.join(d, f) for f in ("base.mp4", "revoiced.mp4") if os.path.exists(os.path.join(d, f))), None)
    steps = _doc_steps(d)
    if not steps:
        raise HTTPException(400, "prepare the script first")
    docdir = os.path.join(d, "doc"); os.makedirs(docdir, exist_ok=True)

    def frame_b64(t, i):
        fp = os.path.join(docdir, f"step_{i}.jpg")
        if base and (not os.path.exists(fp) or os.path.getmtime(fp) < os.path.getmtime(base)):
            subprocess.run(["ffmpeg", "-y", "-ss", f"{max(0, t):.2f}", "-i", base, "-frames:v", "1",
                            "-vf", "scale=1000:-1", "-q:v", "4", fp], capture_output=True)
        if os.path.exists(fp):
            return "data:image/jpeg;base64," + base64.b64encode(open(fp, "rb").read()).decode()
        return None

    title = _html.escape(cfg.get("title") or cfg.get("name") or "How-to guide")
    body, n = [], 0
    for kind, text, t in steps:
        if kind == "section":
            body.append(f'<h2>{_html.escape(text)}</h2>')
            continue
        n += 1
        img = frame_b64(t, n) if t is not None else None
        shot = f'<img src="{img}" alt="Step {n}">' if img else ""
        body.append(f'<div class="step"><div class="num">{n}</div>'
                     f'<div class="body"><p>{_html.escape(text)}</p>{shot}</div></div>')
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:16px/1.6 -apple-system,Segoe UI,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;color:#1a2233}}
h1{{font-size:28px}} h2{{margin-top:34px;color:#005DBC}}
.step{{display:flex;gap:16px;margin:22px 0}} .num{{flex:none;width:30px;height:30px;border-radius:50%;
background:#005DBC;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700}}
.body p{{margin:4px 0 10px}} .body img{{width:100%;border:1px solid #dde3ec;border-radius:8px}}
footer{{margin-top:40px;color:#8a93a6;font-size:13px}}</style></head>
<body><h1>{title}</h1>{''.join(body)}
<footer>Generated by Remaster from a screen recording.</footer></body></html>"""
    return Response(doc, media_type="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{pid}-guide.html"'})


@app.get("/api/projects/{pid}/outputs")
def outputs(pid: str):
    """What's been produced — drives the Publish drawer."""
    d = proj_dir(pid)
    cfg = cfgmod.load(d)
    aspects = [a for a in (cfg.get("aspects") or ["16x9"]) if os.path.exists(os.path.join(d, f"framed-{a}.mp4"))]
    return {"aspects": aspects,
            "has_render": bool(aspects),
            "has_script": os.path.exists(os.path.join(d, "script.json")),
            "platform": sys.platform}


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
