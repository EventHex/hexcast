"""HexCast — demo video studio (working name).
Wraps the pipeline: upload recording -> process (transcribe/clean/zoom/
voice/align/frame) -> edit script + branding -> re-render -> download.

Run:  python3 -m uvicorn app:app --port 8765   (from the repo root)
"""
from __future__ import annotations
import os, sys, json, subprocess, threading, uuid
from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.abspath(__file__))   # repo root
load_dotenv(os.path.join(ROOT, ".env"))   # so /api/settings reflects .env keys too
HERE = ROOT
# project data lives outside the repo when HEXCAST_DATA_DIR is set
PROJECTS = os.environ.get("HEXCAST_DATA_DIR") or os.path.join(ROOT, "projects")
os.makedirs(PROJECTS, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import config as cfgmod
import brands as brandsmod
from providers import settings as settingsmod
import contextvars
import auth

# Multi-user: every request runs as a signed-in user, and all project/brand/
# settings I/O is scoped to that user's own data dir (PROJECTS/users/<uid>/).
# The pure-ASGI middleware below sets _CUR before each request; _data() reads it.
USERS_ROOT = os.path.join(PROJECTS, "users")
os.makedirs(USERS_ROOT, exist_ok=True)
# Every request is authenticated -> strict per-user BYOK: the host .env never
# acts as a shared key fallback (set HEXCAST_MULTIUSER=0 only for a private
# single-user install that wants .env keys back).
os.environ.setdefault("HEXCAST_MULTIUSER", "1")
auth.init(PROJECTS)
_CUR: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("uid", default=None)


def _data() -> str:
    """Current user's data dir. Falls back to the shared root only outside a
    request (startup tasks pass explicit paths, so this stays safe)."""
    uid = _CUR.get()
    return auth.user_dir(PROJECTS, uid) if uid else PROJECTS


_KEY_LOCK = threading.Lock()


def _with_keys(fn):
    """Run fn() with the current user's provider keys in os.environ, under a lock
    so a concurrent request can't observe (or clobber into) another user's keys.
    Renders isolate via the subprocess env; this covers the in-process API calls
    (LLM rewrite, Soniox voice management) that read os.environ directly."""
    add = settingsmod.provider_env(_data())
    with _KEY_LOCK:
        old = {k: os.environ.get(k) for k in add}
        os.environ.update(add)
        try:
            return fn()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

VERSION = "0.2.0"
_EXT_SEEN = {"at": 0.0}   # last time the recorder extension pinged us

app = FastAPI(title="HexCast")


@app.on_event("startup")
def _reap_orphans():
    """A previous server instance may have died mid-render, orphaning pipeline
    processes that keep writing into project files. Reap them on boot."""
    subprocess.run(["pkill", "-f", r"pipeline/(build_revoice|polish_export|transcribe)\.py"],
                   capture_output=True)
# Allow the Chrome extension (chrome-extension://) to hand recordings straight in.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def _no_cache_html(request, call_next):
    """Never cache the HTML document. Vite hashes asset filenames per build, so
    a browser-cached index.html can point at a deleted CSS/JS after a rebuild
    (→ unstyled page). Hashed assets stay immutably cacheable; only the entry
    HTML is revalidated so it always references the current hashes."""
    resp = await call_next(request)
    ct = resp.headers.get("content-type", "")
    if ct.startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp
app.mount("/assets", StaticFiles(directory=os.path.join(HERE, "assets")), name="assets")
_EDITOR_DIST = os.path.join(HERE, "editor", "dist")
if os.path.isdir(_EDITOR_DIST):
    # React + Remotion Player editor (webstudio/editor). Build: cd editor && npm run build
    app.mount("/editor", StaticFiles(directory=_EDITOR_DIST, html=True), name="editor")

# Endpoints reachable without a session. Everything else under /api or /media
# requires a signed-in user (per-user data isolation).
_PUBLIC = ("/api/auth/", "/api/health", "/api/ping")


# --- optional central control plane (SaaS "model B"): accounts + usage live in
# a remote service and this app is a client (login/verify/usage over HTTP).
# Unset HEXCAST_AUTH_URL => self-contained local accounts (the default). ---
AUTH_URL = os.environ.get("HEXCAST_AUTH_URL", "").rstrip("/")
CENTRAL = bool(AUTH_URL)
_TOK: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("tok", default=None)
_USER: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar("user", default=None)
_verify_cache: dict = {}   # token -> (user, expiry_ts)


def _central(path, method="POST", json=None, token=None, timeout=20):
    import requests
    h = {"Authorization": "Bearer " + token} if token else {}
    call = requests.post if method == "POST" else requests.get
    return call(AUTH_URL + path, json=json, headers=h, timeout=timeout)


def _resolve_user(tok):
    """Session token -> user dict (or None). Central mode verifies against the
    control plane (cached ~60s); local mode uses the embedded SQLite."""
    if not tok:
        return None
    if not CENTRAL:
        uid = auth.verify_token(tok)
        return auth.get(uid) if uid else None
    import time as _t
    hit = _verify_cache.get(tok)
    if hit and hit[1] > _t.time():
        return hit[0]
    try:
        r = _central("/auth/verify", "GET", token=tok)
        if r.status_code != 200:
            _verify_cache.pop(tok, None)
            return None
        u = r.json()["user"]
        _verify_cache[tok] = (u, _t.time() + 60)
        return u
    except Exception:
        return hit[0] if hit else None   # tolerate a transient central outage


def _report_usage(kind: str, meta: str = "") -> None:
    """Best-effort central usage metering (BYOK => advisory). No-op locally."""
    if not CENTRAL:
        return
    try:
        _central("/usage/report", json={"kind": kind, "meta": meta}, token=_TOK.get(), timeout=8)
    except Exception:
        pass


def _cookie_token(scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            for part in v.decode("latin1").split(";"):
                name, _, val = part.strip().partition("=")
                if name == auth.COOKIE:
                    return val
    return None


class _AuthCtx:
    """Pure-ASGI middleware: resolves the session cookie into _CUR / _TOK / _USER
    (contextvars set here DO propagate to endpoints, unlike BaseHTTPMiddleware)
    and rejects unauthenticated API/media calls before they touch any data path."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        tok = _cookie_token(scope)
        user = _resolve_user(tok)
        uid = user["id"] if user else None
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        guarded = path.startswith("/api/") or path.startswith("/media/")
        public = method == "OPTIONS" or any(path.startswith(p) for p in _PUBLIC)
        if guarded and not public and not uid:
            body = b'{"detail":"auth required"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        c1, c2, c3 = _CUR.set(uid), _TOK.set(tok), _USER.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _CUR.reset(c1); _TOK.reset(c2); _USER.reset(c3)


app.add_middleware(_AuthCtx)


def _ensure_user_space(uid: str) -> str:
    """User's data dir exists + has the seeded default brand. Idempotent."""
    d = auth.user_dir(PROJECTS, uid)
    os.makedirs(d, exist_ok=True)
    brandsmod.seed_default(d, os.path.join(HERE, "assets"))
    return d


def _migrate_legacy(uid: str) -> None:
    """One-time: when the very first user signs up, hand them any pre-existing
    single-user data (loose project dirs + brands/ + settings.json) sitting at
    the data root, so upgrading to multi-user doesn't strip the founder's work."""
    import shutil
    dest = auth.user_dir(PROJECTS, uid)
    os.makedirs(dest, exist_ok=True)
    reserved = {"users", ".secret", "hexcast.db"}
    for name in os.listdir(PROJECTS):
        if name in reserved:
            continue
        src = os.path.join(PROJECTS, name)
        tgt = os.path.join(dest, name)
        if os.path.exists(tgt):
            continue
        is_project = os.path.isdir(src) and os.path.exists(os.path.join(src, "config.json"))
        if is_project or name in ("brands", "settings.json"):
            try:
                shutil.move(src, tgt)
            except Exception as e:
                print(f"migrate skip {name}: {e}")
    # Hand the host .env provider keys to the founder's own account — in
    # multi-user mode .env is no longer a shared fallback, so without this the
    # founder would lose the AI features their .env keys powered.
    envkeys = {name: os.environ[var].strip() for name, var in settingsmod.KEY_ENV.items()
               if os.environ.get(var, "").strip()}
    if envkeys:
        settingsmod.save_settings(dest, {"keys": envkeys})


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax",
                        max_age=auth.SESSION_DAYS * 86400, path="/")


def _no_local_users() -> bool:
    if not os.path.isdir(USERS_ROOT):
        return True
    return not any(os.path.isdir(os.path.join(USERS_ROOT, x)) for x in os.listdir(USERS_ROOT))


def _claim_space(user: dict, token: str, response: Response) -> None:
    """Provision this account's local data dir (+ claim any legacy data if this
    is the machine's first account) and set the session cookie."""
    import time as _t
    first = _no_local_users()
    _ensure_user_space(user["id"])
    if first:
        _migrate_legacy(user["id"])
    _set_cookie(response, token)
    _verify_cache[token] = (user, _t.time() + 60)


def _central_detail(r) -> str:
    try:
        return r.json().get("detail") or "sign-in failed"
    except Exception:
        return "sign-in failed"


@app.post("/api/auth/signup")
def signup(body: dict = Body(...), response: Response = None):
    if CENTRAL:
        r = _central("/auth/signup", json={"email": body.get("email"),
                     "password": body.get("password"), "name": body.get("name")})
        if r.status_code != 200:
            raise HTTPException(r.status_code, _central_detail(r))
        d = r.json()
        _claim_space(d["user"], d["token"], response)
        return d["user"]
    # local mode
    email = (body.get("email") or "").strip().lower()
    if "@" not in email or len(body.get("password") or "") < 6:
        raise HTTPException(400, "valid email and a 6+ char password required")
    if auth.get_by_email(email):
        raise HTTPException(409, "that email already has an account")
    first = auth.count() == 0
    uid = auth.create_user(email, body.get("name"), pw=body.get("password"))
    _ensure_user_space(uid)
    if first:
        _migrate_legacy(uid)
    _set_cookie(response, auth.make_token(uid))
    return auth.get(uid)


@app.post("/api/auth/login")
def login(body: dict = Body(...), response: Response = None):
    if CENTRAL:
        r = _central("/auth/login", json={"email": body.get("email"), "password": body.get("password")})
        if r.status_code != 200:
            raise HTTPException(r.status_code, _central_detail(r))
        d = r.json()
        _claim_space(d["user"], d["token"], response)
        return d["user"]
    row = auth.get_by_email(body.get("email") or "")
    if not row or not auth.verify_pw(body.get("password") or "", row["pw_hash"]):
        raise HTTPException(401, "wrong email or password")
    _ensure_user_space(row["id"])
    _set_cookie(response, auth.make_token(row["id"]))
    return auth.get(row["id"])


@app.post("/api/auth/logout")
def logout(response: Response):
    _verify_cache.pop(_TOK.get(), None)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me():
    return {"user": _USER.get(), "google": (False if CENTRAL else auth.google_configured())}


@app.get("/api/auth/google/login")
def google_login(request: Request):
    if CENTRAL:
        return RedirectResponse(AUTH_URL + "/auth/google/login")
    if not auth.google_configured():
        raise HTTPException(400, "Google sign-in is not configured on this server")
    redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/google/callback"
    return RedirectResponse(auth.google_auth_url(redirect_uri, state="hexcast"))


@app.get("/auth/google/done")
def google_done(token: str = "", response: Response = None):
    """Loopback target for central's Google flow: receive the session token,
    claim local space, and open the app."""
    user = _resolve_user(token)
    if not user:
        raise HTTPException(400, "Google sign-in failed")
    resp = RedirectResponse("/editor/")
    _claim_space(user, token, resp)
    return resp


@app.get("/api/auth/google/callback")
def google_callback(request: Request, code: str = "", response: Response = None):
    if not auth.google_configured() or not code:
        raise HTTPException(400, "Google sign-in unavailable")
    redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/google/callback"
    try:
        prof = auth.google_exchange(code, redirect_uri)
    except Exception as e:
        raise HTTPException(400, f"Google sign-in failed: {e}")
    row = auth.get_by_google(prof["sub"]) or (auth.get_by_email(prof["email"]) if prof.get("email") else None)
    if row:
        uid = row["id"]
        if not row["google_sub"]:
            auth.set_google_sub(uid, prof["sub"])
    else:
        first = auth.count() == 0
        uid = auth.create_user(prof["email"] or f'{prof["sub"]}@google', prof.get("name"), google_sub=prof["sub"])
        _ensure_user_space(uid)
        if first:
            _migrate_legacy(uid)
    resp = RedirectResponse("/editor/")
    _set_session(resp, uid)
    return resp
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
    d = os.path.join(_data(), pid)
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
    """Retention policy from each user's settings: prune exported projects
    untouched for N days. Startup task -> walks every user's data dir explicitly
    (no request context, so it can't rely on _data())."""
    import time as _t
    if not os.path.isdir(USERS_ROOT):
        return
    for uid in os.listdir(USERS_ROOT):
        udir = os.path.join(USERS_ROOT, uid)
        if not os.path.isdir(udir):
            continue
        days = int((settingsmod.load_settings(udir).get("retention") or {}).get("days") or 0)
        if days <= 0:
            continue
        cutoff = _t.time() - days * 86400
        for pid in os.listdir(udir):
            d = os.path.join(udir, pid)
            if not os.path.isdir(d) or pid == "brands":
                continue
            exported = any(f.startswith("framed-") for f in os.listdir(d))
            if exported and os.path.getmtime(d) < cutoff:
                freed = prune_project(d)
                if freed:
                    print(f"retention: pruned {uid}/{pid} ({freed // 1048576} MB)")


@app.on_event("startup")
def _startup_prune():
    threading.Thread(target=auto_prune, daemon=True).start()


@app.get("/api/projects")
def list_projects():
    out = []
    for pid in os.listdir(_data()):
        d = os.path.join(_data(), pid)
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
    nd = os.path.join(_data(), new)
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
    for pid in (ids or os.listdir(_data())):
        d = os.path.join(_data(), pid)
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
    os.makedirs(os.path.join(_data(), pid), exist_ok=True)
    cfgmod.save(os.path.join(_data(), pid), {})
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
    new = _with_keys(lambda: cerebras_clean.rewrite_lines([segs[i]["en"] for i in idx], glossary=cfg.get("glossary")))
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
    tts_provider = settingsmod.provider_env(_data()).get("HEXCAST_TTS_PROVIDER", "auto")
    return {
        "voiced": _sig(cfg.get("voice"), cfg.get("lang"), cfg.get("original_voice"), tts_provider,
                       [s.get("en") for s in segs], [s.get("type") for s in segs],
                       [(s.get("start"), s.get("end"), s.get("dur"), s.get("anchor")) for s in segs]),
        "cards": _sig(cfg.get("title"), cfg.get("subtitle"), cfg.get("outro_title"),
                      cfg.get("outro_subtitle"), cfg.get("intro_dur"), cfg.get("outro_dur"),
                      cfg.get("card_style"), cfg.get("card_top"), cfg.get("card_bottom"),
                      cfg.get("brand_top"), cfg.get("brand_bottom"), cfg.get("logo"), cfg.get("transition"),
                      cfg.get("font"), cfg.get("card_align"), cfg.get("card_title_color"),
                      cfg.get("card_sub_color"), cfg.get("card_scale"),
                      cfg.get("intro_template"), cfg.get("outro_template"), cfg.get("intro_eyebrow"),
                      cfg.get("outro_eyebrow"), cfg.get("intro_cta"), cfg.get("intro_url"),
                      cfg.get("outro_cta"), cfg.get("outro_url")),
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
@app.post("/api/projects/{pid}/render")
def render_project(pid: str):
    """Smart render: diff the current config/script against what was last
    rendered and run only the stages that changed (frame-only, fx+frame, or a
    full re-voice). Nothing changed -> no job, files already current. Produces
    the video files that Export downloads and Publish distributes."""
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
    _report_usage("render")
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
    # absolute project dir: works with HEXCAST_DATA_DIR outside the repo;
    # cwd stays ROOT only so the pipeline scripts' imports + .env resolve
    steps = [(desc, [sys.executable] + [a.replace("{REL}", d) for a in args]) for desc, args in steps_defs]
    threading.Thread(target=run_job, args=(job, steps, ROOT, d),
                     kwargs={"record_state": record_state,
                             "env": settingsmod.provider_env(_data())}, daemon=True).start()
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
        nd = os.path.join(_data(), f"{pid}-{slug}")
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


# ---- native screen recording (the point of the desktop app) --------------
# One capture at a time per machine (ffmpeg avfoundation). Records straight
# into a fresh project so the user goes record -> process with no upload step.
_REC: dict = {}   # {"pid": ...} for the in-flight recording


@app.get("/api/record/devices")
def record_devices():
    import recording
    try:
        dev = recording.list_devices()
    except Exception as e:
        raise HTTPException(500, f"cannot list capture devices: {e}")
    return {"recording": recording.is_recording(),
            "elapsed": recording.elapsed(), **dev}


@app.post("/api/record/start")
def record_start(body: dict = Body(...)):
    import recording
    if recording.is_recording():
        raise HTTPException(409, "already recording")
    target = (body.get("target") or "").strip()
    if not target:
        raise HTTPException(400, "target required")
    mic = body.get("mic") or None
    fps = int(body.get("fps") or 30)
    pid = "rec-" + uuid.uuid4().hex[:8]
    d = os.path.join(_data(), pid)
    os.makedirs(d, exist_ok=True)
    cfgmod.save(d, {"name": "Screen recording"})
    raw_path = os.path.join(d, "raw.mp4")
    try:
        recording.start(raw_path, target, mic, fps)
    except Exception as e:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, str(e))
    _REC["pid"] = pid
    return {"id": pid}


@app.post("/api/record/stop")
def record_stop():
    import recording
    res = recording.stop()
    pid = _REC.pop("pid", None)
    if not res or not pid:
        raise HTTPException(400, "not recording")
    if not res.get("ok"):
        import shutil
        shutil.rmtree(os.path.join(_data(), pid), ignore_errors=True)
        raise HTTPException(500, "recording produced no video — check Screen Recording permission")
    return {"id": pid, "raw": "raw.mp4"}


# ---- auto-update check ---------------------------------------------------
# The app polls a release manifest ({version,url,notes}) and, if a newer build
# exists, the editor shows a banner linking to the DMG. Silent self-replace
# needs a signed build (later) — for the unsigned build we notify + download.
_UPD: dict = {"at": 0.0, "data": None}


def _semver(v: str) -> tuple:
    import re as _re
    return tuple(int(x) for x in _re.findall(r"\d+", v or "")[:3])


def _update_manifest_url() -> str:
    u = os.environ.get("HEXCAST_UPDATE_URL", "").strip()
    if u:
        return u
    return (AUTH_URL + "/updates/latest") if CENTRAL else ""


@app.get("/api/update")
def check_update():
    import time as _t
    now = _t.time()
    if _UPD["data"] is None or now - _UPD["at"] > 3600:
        _UPD["at"] = now
        _UPD["data"] = {}
        url = _update_manifest_url()
        if url:
            try:
                import requests
                r = requests.get(url, timeout=6)
                if r.ok:
                    _UPD["data"] = r.json()
            except Exception:
                pass
    m = _UPD["data"] or {}
    latest = m.get("version")
    available = bool(latest) and _semver(latest) > _semver(VERSION)
    return {"current": VERSION, "latest": latest, "available": available,
            "url": m.get("url"), "notes": m.get("notes")}


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
    d = os.path.join(_data(), pid)
    shutil.copytree(_SAMPLE_DIR, d)
    c = cfgmod.load(d); c["name"] = "Sample demo"; cfgmod.save(d, c)
    return {"id": pid}


@app.get("/api/brands")
def brands_list():
    return {"brands": brandsmod.list_brands(_data())}


@app.get("/api/brands/{bid}")
def brands_get(bid: str):
    try:
        return brandsmod.get_brand(_data(), bid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such brand")


@app.post("/api/brands/{bid}/logo")
async def brand_logo(bid: str, file: UploadFile = File(...)):
    try:
        d = brandsmod._bdir(_data(), bid)
    except ValueError:
        raise HTTPException(400, "bad brand id")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "logo.png")
    with open(path, "wb") as f:
        f.write(await file.read())
    b = brandsmod.get_brand(_data(), bid)
    cfg = b.get("config") or {}
    cfg["logo"] = path
    brandsmod.save_brand(_data(), bid, b.get("name"), cfg)
    return {"ok": True, "logo": path}


@app.get("/api/brands/{bid}/logo")
def brand_logo_get(bid: str):
    try:
        p = os.path.join(brandsmod._bdir(_data(), bid), "logo.png")
    except ValueError:
        raise HTTPException(400, "bad brand id")
    if not os.path.isfile(p):
        raise HTTPException(404, "no logo")
    return FileResponse(p)


@app.post("/api/brands")
def brands_create(body: dict = Body(...)):
    bid = brandsmod.create_brand(_data(), body.get("name"), body.get("config"))
    return {"id": bid}


@app.put("/api/brands/{bid}")
def brands_update(bid: str, body: dict = Body(...)):
    try:
        cur = brandsmod.get_brand(_data(), bid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such brand")
    cfg = {**(cur.get("config") or {}), **(body.get("config") or {})}
    return brandsmod.save_brand(_data(), bid, body.get("name") or cur.get("name"), cfg)


@app.delete("/api/brands/{bid}")
def brands_delete(bid: str):
    try:
        brandsmod.delete_brand(_data(), bid)
    except ValueError:
        raise HTTPException(400, "bad brand id")
    return {"ok": True}


@app.post("/api/projects/{pid}/apply-brand/{bid}")
def apply_brand(pid: str, bid: str):
    d = proj_dir(pid)
    try:
        return brandsmod.apply_to_project(_data(), bid, d, cfgmod)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such brand")


@app.post("/api/brands/from-project/{pid}")
def brand_from_project(pid: str, body: dict = Body(...)):
    d = proj_dir(pid)
    bid = brandsmod.from_project(_data(), body.get("name") or "My brand", d, cfgmod)
    return {"id": bid}


@app.get("/api/voices")
def voices(provider: str = "elevenlabs"):
    """Live voice list for pickable TTS providers (id, name, preview_url)."""
    from providers import tts as ttsmod
    try:
        return _with_keys(lambda: {"voices": ttsmod.list_voices(provider)})
    except Exception as e:
        raise HTTPException(400, str(e))


def _normalize_clip(data: bytes) -> tuple[bytes, str]:
    """Transcode any uploaded/recorded audio to a clean mp3 (mono, 24 kHz,
    trimmed to 20s) so Soniox gets a consistent format — browser mic recordings
    are webm/opus, which we can't hand to the API raw. Falls back to the
    original bytes if ffmpeg isn't usable."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".in", delete=False) as fi:
        fi.write(data); src = fi.name
    dst = src + ".mp3"
    try:
        r = subprocess.run(["ffmpeg", "-y", "-i", src, "-t", "20", "-ac", "1",
                            "-ar", "24000", "-codec:a", "libmp3lame", "-q:a", "3", dst],
                           capture_output=True)
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            with open(dst, "rb") as f:
                return f.read(), "sample.mp3"
    except Exception:
        pass
    finally:
        for p in (src, dst):
            try:
                os.remove(p)
            except OSError:
                pass
    return data, "sample.mp3"


@app.post("/api/voices/clone")
async def clone_voice(name: str, file: UploadFile = File(...)):
    """Create a Soniox cloned voice from a short reference clip (few sec–20s,
    ≤10 MB). Accepts an uploaded file or an in-browser recording; the clip is
    normalized to mp3 first. Returns {id, name}; the id is used as the TTS voice."""
    from tools.audio import soniox_tts
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "sample too large")
    clip, fname = _normalize_clip(data)
    if len(clip) > 10 * 1024 * 1024:
        raise HTTPException(400, "sample too large after encoding (max 10 MB)")
    try:
        return _with_keys(lambda: soniox_tts.create_cloned_voice(name.strip() or "My voice", clip, fname))
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/voices/clone/{voice_id}")
def delete_clone(voice_id: str):
    from tools.audio import soniox_tts
    try:
        _with_keys(lambda: soniox_tts.delete_cloned_voice(voice_id))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/settings")
def get_settings():
    return settingsmod.masked_view(_data())


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    settingsmod.save_settings(_data(), body)
    return settingsmod.masked_view(_data())


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


@app.post("/api/projects/{pid}/zooms/auto")
def regenerate_zooms(pid: str):
    """Re-run AI zoom targeting on the current script and return fresh zoom
    blocks. Does NOT re-render the video — the editor drops the zooms onto the
    timeline live; the user renders when happy. Needs a vision key (Gemini /
    Cerebras); without one it reports 0 so the UI can prompt for a key."""
    env = settingsmod.provider_env(_data())
    has_vision = bool(env.get("GEMINI_API_KEY") or env.get("CEREBRAS_API_KEY"))
    d = proj_dir(pid)
    sp = os.path.join(d, "script.json")
    if not os.path.exists(sp):
        raise HTTPException(400, "no script yet — process the recording first")
    data = json.load(open(sp, encoding="utf-8"))
    segs_all = data.get("segments") or []
    # need text + a valid recording window to sample a frame; rendered times
    # (rstart/rdur) aren't written back for every project, so fall back to the
    # original start/end when they're missing.
    idx = [i for i, s in enumerate(segs_all)
           if s.get("type") in (None, "clip") and s.get("en") and s.get("start") is not None]
    if not idx:
        raise HTTPException(400, "no narration segments to zoom")
    raw = next((os.path.join(d, f"raw.{e}") for e in ("webm", "mp4", "mov", "mkv")
                if os.path.exists(os.path.join(d, f"raw.{e}"))), None)
    if not raw:
        raise HTTPException(400, "raw recording not found for this project")
    from zoom_decide import decide
    sub = [segs_all[i] for i in idx]
    en = [segs_all[i].get("en", "") for i in idx]
    try:
        decisions = _with_keys(lambda: decide(raw, sub, en, d))
    except Exception as e:
        raise HTTPException(400, f"zoom AI failed: {e}")
    zooms = []
    for k, i in enumerate(idx):
        dd = decisions[k] if k < len(decisions) else {}
        s = segs_all[i]
        if not dd.get("zoom"):
            continue
        st = s["rstart"] if s.get("rstart") is not None else s.get("start")
        dur = s["rdur"] if s.get("rdur") is not None else (s.get("end", st) - st)
        zooms.append({"start": round(st, 3), "end": round(st + (dur or 0), 3),
                      "cx": dd.get("cx") or 0.5, "cy": dd.get("cy") or 0.5,
                      "scale": dd.get("scale") or 1.4, "speed": dd.get("speed") or 3})
    data["zooms"] = zooms
    data["zoomsEdited"] = True   # explicit list — render must keep it verbatim
    json.dump(data, open(sp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return {"zooms": zooms, "count": len(zooms), "segments": len(idx), "vision": has_vision}


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
<footer>Generated by HexCast from a screen recording.</footer></body></html>"""
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
