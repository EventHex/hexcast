"""Accounts + sessions for the multi-user (SaaS) mode.

Stdlib-only crypto (no bcrypt/passlib/jwt dep): pbkdf2 password hashing and an
HMAC-signed session token. Users live in SQLite at <data_root>/hexcast.db.
Google sign-in is optional — it activates only when GOOGLE_OAUTH_CLIENT_ID /
GOOGLE_OAUTH_CLIENT_SECRET are set.

Each user gets their own data dir (<data_root>/users/<uid>/) holding their
projects, brands and settings.json — so BYOK keys and projects never cross
accounts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time

_DB = None
_ROOT = None
_SECRET = None
PBKDF2_ROUNDS = 200_000
SESSION_DAYS = 30
COOKIE = "rm_session"


def init(data_root: str) -> None:
    """Create the users table + load/generate the signing secret. Idempotent."""
    global _DB, _ROOT, _SECRET
    _ROOT = data_root
    os.makedirs(data_root, exist_ok=True)
    _DB = os.path.join(data_root, "hexcast.db")
    # carry over accounts from the pre-rebrand filename (Remaster -> HexCast)
    _legacy = os.path.join(data_root, "remaster.db")
    if os.path.exists(_legacy) and not os.path.exists(_DB):
        os.rename(_legacy, _DB)
    con = _con()
    con.execute(
        """CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY, email TEXT UNIQUE, name TEXT,
            pw_hash TEXT, google_sub TEXT, plan TEXT DEFAULT 'free', created REAL)"""
    )
    con.commit()
    con.close()
    _SECRET = _load_secret(data_root)


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(_DB)
    con.row_factory = sqlite3.Row
    return con


def _load_secret(data_root: str) -> str:
    """Persistent HMAC secret for session tokens. Env override wins so a fleet
    of instances can share one; else generated once and stored chmod 600."""
    env = os.environ.get("HEXCAST_SECRET_KEY")
    if env:
        return env
    p = os.path.join(data_root, ".secret")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    s = secrets.token_hex(32)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)
    os.chmod(p, 0o600)
    return s


# ---- password hashing (pbkdf2-hmac-sha256) ----

def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_pw(pw: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, s, d = stored.split("$")
        salt = base64.b64decode(s)
        want = base64.b64decode(d)
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
        return hmac.compare_digest(got, want)
    except Exception:
        return False


# ---- users ----

def _row_to_user(r) -> dict:
    return {"id": r["id"], "email": r["email"], "name": r["name"],
            "plan": r["plan"], "created": r["created"]}


def get(uid: str) -> dict | None:
    con = _con()
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    con.close()
    return _row_to_user(r) if r else None


def get_by_email(email: str) -> sqlite3.Row | None:
    con = _con()
    r = con.execute("SELECT * FROM users WHERE email=?", (email.lower().strip(),)).fetchone()
    con.close()
    return r


def get_by_google(sub: str) -> sqlite3.Row | None:
    con = _con()
    r = con.execute("SELECT * FROM users WHERE google_sub=?", (sub,)).fetchone()
    con.close()
    return r


def count() -> int:
    con = _con()
    n = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    con.close()
    return n


def create_user(email: str, name: str | None = None, pw: str | None = None,
                google_sub: str | None = None) -> str:
    uid = secrets.token_hex(8)
    con = _con()
    con.execute(
        "INSERT INTO users(id,email,name,pw_hash,google_sub,plan,created) VALUES(?,?,?,?,?,?,?)",
        (uid, email.lower().strip(), name or email.split("@")[0],
         hash_pw(pw) if pw else None, google_sub, "free", time.time()),
    )
    con.commit()
    con.close()
    return uid


def set_google_sub(uid: str, sub: str) -> None:
    con = _con()
    con.execute("UPDATE users SET google_sub=? WHERE id=?", (sub, uid))
    con.commit()
    con.close()


# ---- session token: "<uid>.<exp>.<sig>" ----

def make_token(uid: str, days: int = SESSION_DAYS) -> str:
    exp = int(time.time()) + days * 86400
    body = f"{uid}.{exp}"
    sig = hmac.new(_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verify_token(tok: str | None) -> str | None:
    if not tok:
        return None
    try:
        uid, exp, sig = tok.rsplit(".", 2)
        body = f"{uid}.{exp}"
        want = hmac.new(_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(want, sig):
            return None
        if int(exp) < time.time():
            return None
        return uid
    except Exception:
        return None


def user_dir(data_root: str, uid: str) -> str:
    return os.path.join(data_root, "users", uid)


# ---- Google OAuth (optional; active only when configured) ----

def google_configured() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def google_auth_url(redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode
    q = urlencode({
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    return "https://accounts.google.com/o/oauth2/v2/auth?" + q


def google_exchange(code: str, redirect_uri: str) -> dict:
    """Exchange an auth code for the user's Google profile. Returns
    {sub, email, name}. Raises on failure."""
    import requests
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=30)
    tok.raise_for_status()
    access = tok.json()["access_token"]
    info = requests.get("https://openidconnect.googleapis.com/v1/userinfo",
                        headers={"Authorization": f"Bearer {access}"}, timeout=30)
    info.raise_for_status()
    j = info.json()
    return {"sub": j["sub"], "email": j.get("email"), "name": j.get("name")}
