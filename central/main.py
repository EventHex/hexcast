"""Remaster central control plane — accounts + usage.

Deliberately tiny: it never sees video or keys. The desktop app does all
processing locally with the user's own keys, and only talks to this service to
sign in / verify a session / report "I rendered a video" for metering.

Runs on SQLite locally; point DATABASE_URL at Cloud SQL (Postgres) for GCP.
Deploy target: Cloud Run (scales to zero).
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Body, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

import store

app = FastAPI(title="Remaster Central")
store.init()

# The desktop app calls this server-to-server, but allow browser origins too
# (configurable) in case a hosted web client is added later.
_origins = [o.strip() for o in os.environ.get("ALLOW_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins or ["*"],
                   allow_methods=["*"], allow_headers=["*"])


def _bearer(authorization: str | None) -> dict:
    """Resolve 'Authorization: Bearer <token>' to a user, or 401."""
    tok = (authorization or "").removeprefix("Bearer ").strip()
    uid = store.verify_token(tok)
    u = store.get(uid) if uid else None
    if not u:
        raise HTTPException(401, "invalid or expired session")
    return u


@app.get("/health")
def health():
    return {"ok": True, "service": "remaster-central",
            "db": "firestore" if store._FS else ("postgres" if store._PG else "sqlite"),
            "google": bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID"))}


@app.get("/updates/latest")
def latest_update():
    """Release manifest the desktop app polls for auto-update. Set per release
    with env vars on the service (no redeploy needed):
        gcloud run services update remaster-central \\
          --set-env-vars UPDATE_VERSION=0.2.0,UPDATE_URL=https://…/Remaster.dmg
    Falls back to central/update_manifest.json for local runs."""
    v = os.environ.get("UPDATE_VERSION")
    if v:
        return {"version": v, "url": os.environ.get("UPDATE_URL"),
                "notes": os.environ.get("UPDATE_NOTES")}
    import json
    p = os.path.join(os.path.dirname(__file__), "update_manifest.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"version": None}


@app.post("/auth/signup")
def signup(body: dict = Body(...)):
    email = (body.get("email") or "").strip().lower()
    pw = body.get("password") or ""
    if "@" not in email or len(pw) < 6:
        raise HTTPException(400, "valid email and a 6+ character password required")
    if store.get_by_email(email):
        raise HTTPException(409, "that email already has an account")
    uid = store.create_user(email, body.get("name"), pw=pw)
    return {"token": store.make_token(uid), "user": store.user_public(store.get(uid))}


@app.post("/auth/login")
def login(body: dict = Body(...)):
    u = store.get_by_email(body.get("email") or "")
    if not u or not store.verify_pw(body.get("password") or "", u["pw_hash"]):
        raise HTTPException(401, "wrong email or password")
    return {"token": store.make_token(u["id"]), "user": store.user_public(u)}


@app.get("/auth/verify")
def verify(authorization: str | None = Header(default=None)):
    """The desktop app calls this to validate its stored session token."""
    return {"user": store.user_public(_bearer(authorization))}


@app.get("/auth/me")
def me(authorization: str | None = Header(default=None)):
    u = _bearer(authorization)
    return {"user": store.user_public(u), "usage": store.usage_summary(u["id"])}


@app.post("/usage/report")
def usage_report(body: dict = Body(default={}), authorization: str | None = Header(default=None)):
    """The desktop app calls this after a successful render (or other billable
    event) so usage can be metered centrally. BYOK means this is advisory."""
    u = _bearer(authorization)
    kind = (body.get("kind") or "render").strip()[:32]
    store.record_event(u["id"], kind, str(body.get("meta") or "")[:200])
    return {"ok": True, "usage": store.usage_summary(u["id"])}


# ---- Google sign-in (optional; activates with GOOGLE_OAUTH_* set) ----

def _google_configured() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


@app.get("/auth/google/login")
def google_login(request: Request):
    if not _google_configured():
        raise HTTPException(400, "Google sign-in is not configured")
    from urllib.parse import urlencode
    redirect_uri = str(request.base_url).rstrip("/") + "/auth/google/callback"
    q = urlencode({"client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"], "redirect_uri": redirect_uri,
                   "response_type": "code", "scope": "openid email profile", "prompt": "select_account"})
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + q)


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = ""):
    if not _google_configured() or not code:
        raise HTTPException(400, "Google sign-in unavailable")
    import requests
    redirect_uri = str(request.base_url).rstrip("/") + "/auth/google/callback"
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"}, timeout=30)
    tok.raise_for_status()
    info = requests.get("https://openidconnect.googleapis.com/v1/userinfo",
                        headers={"Authorization": "Bearer " + tok.json()["access_token"]}, timeout=30).json()
    sub, email, name = info["sub"], info.get("email"), info.get("name")
    u = store.get_by_google(sub) or (store.get_by_email(email) if email else None)
    if u:
        uid = u["id"]
        if not u["google_sub"]:
            store.set_google_sub(uid, sub)
    else:
        uid = store.create_user(email or f"{sub}@google", name, google_sub=sub)
    # Hand the token back to the desktop app's loopback listener.
    loopback = os.environ.get("DESKTOP_LOOPBACK", "http://127.0.0.1:8765/auth/google/done")
    return RedirectResponse(f"{loopback}?token={store.make_token(uid)}")
