"""Data + crypto for the central control plane.

Self-contained (this service deploys on its own, separate from the desktop app):
stdlib pbkdf2 password hashing + HMAC session tokens, and a tiny DB layer that
runs on SQLite locally and Postgres (Cloud SQL) when DATABASE_URL is set.

The token is HMAC-signed with a server-only secret, so the desktop client can't
forge one — it verifies a token by calling /auth/verify here, online.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = os.environ.get("CENTRAL_DB", "central.db")
PBKDF2_ROUNDS = 200_000
SESSION_DAYS = 30

_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
_PH = "%s" if _PG else "?"          # placeholder differs across drivers
_SECRET = None


# ---- connection ----

def _conn():
    if _PG:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    con = sqlite3.connect(SQLITE_PATH)
    con.row_factory = sqlite3.Row
    return con


def _sql(q: str) -> str:
    return q.replace("?", _PH)


def init() -> None:
    global _SECRET
    con = _conn()
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        id TEXT PRIMARY KEY, email TEXT UNIQUE, name TEXT, pw_hash TEXT,
        google_sub TEXT, plan TEXT DEFAULT 'free', created DOUBLE PRECISION)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS usage_events(
        id TEXT PRIMARY KEY, uid TEXT, kind TEXT, at DOUBLE PRECISION, meta TEXT)""")
    con.commit()
    con.close()
    _SECRET = _load_secret()


def _load_secret() -> str:
    env = os.environ.get("SECRET_KEY") or os.environ.get("REMASTER_SECRET_KEY")
    if env:
        return env
    # dev fallback: persist a local secret so restarts don't invalidate sessions
    p = os.environ.get("CENTRAL_SECRET_FILE", ".central-secret")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    s = secrets.token_hex(32)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
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
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(s), PBKDF2_ROUNDS)
        return hmac.compare_digest(got, base64.b64decode(d))
    except Exception:
        return False


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
        want = hmac.new(_SECRET.encode(), f"{uid}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(want, sig) or int(exp) < time.time():
            return None
        return uid
    except Exception:
        return None


# ---- users ----

def _one(sql, args=()):
    con = _conn(); cur = con.cursor()
    cur.execute(_sql(sql), args)
    r = cur.fetchone(); con.close()
    return dict(r) if r else None


def _exec(sql, args=()):
    con = _conn(); cur = con.cursor()
    cur.execute(_sql(sql), args)
    con.commit(); con.close()


def user_public(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "plan": u["plan"]}


def get(uid: str):
    return _one("SELECT * FROM users WHERE id=?", (uid,))


def get_by_email(email: str):
    return _one("SELECT * FROM users WHERE email=?", (email.lower().strip(),))


def get_by_google(sub: str):
    return _one("SELECT * FROM users WHERE google_sub=?", (sub,))


def count() -> int:
    return (_one("SELECT COUNT(*) AS c FROM users") or {"c": 0})["c"]


def create_user(email, name=None, pw=None, google_sub=None) -> str:
    uid = secrets.token_hex(8)
    _exec("INSERT INTO users(id,email,name,pw_hash,google_sub,plan,created) VALUES(?,?,?,?,?,?,?)",
          (uid, email.lower().strip(), name or email.split("@")[0],
           hash_pw(pw) if pw else None, google_sub, "free", time.time()))
    return uid


def set_google_sub(uid: str, sub: str) -> None:
    _exec("UPDATE users SET google_sub=? WHERE id=?", (sub, uid))


# ---- usage ----

def record_event(uid: str, kind: str = "render", meta: str = "") -> None:
    _exec("INSERT INTO usage_events(id,uid,kind,at,meta) VALUES(?,?,?,?,?)",
          (secrets.token_hex(8), uid, kind, time.time(), meta or ""))


def usage_summary(uid: str) -> dict:
    month_ago = time.time() - 30 * 86400
    total = (_one("SELECT COUNT(*) AS c FROM usage_events WHERE uid=? AND kind='render'", (uid,)) or {"c": 0})["c"]
    recent = (_one("SELECT COUNT(*) AS c FROM usage_events WHERE uid=? AND kind='render' AND at>=?",
                   (uid, month_ago)) or {"c": 0})["c"]
    return {"renders_total": total, "renders_30d": recent}
