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

# Backend: SQLite (default, local) | Postgres (DATABASE_URL) | Firestore
# (HEXCAST_BACKEND=firestore — serverless, scales to zero, the Cloud Run pick).
_FS = os.environ.get("HEXCAST_BACKEND", "").lower() == "firestore"
_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
_PH = "%s" if _PG else "?"          # placeholder differs across drivers
_SECRET = None
_fsdb = None                        # lazy google.cloud.firestore client


def _fs():
    global _fsdb
    if _fsdb is None:
        from google.cloud import firestore
        proj = os.environ.get("FIRESTORE_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        _fsdb = firestore.Client(project=proj) if proj else firestore.Client()
    return _fsdb


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
    if _FS:
        _fs()                       # fail fast if creds/project are wrong (schemaless: no DDL)
        _SECRET = _load_secret()
        return
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
    env = os.environ.get("SECRET_KEY") or os.environ.get("HEXCAST_SECRET_KEY")
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


def _fs_users():
    return _fs().collection("users")


def _fs_first(field: str, value):
    for d in _fs_users().where(field, "==", value).limit(1).stream():
        return d.to_dict()
    return None


def get(uid: str):
    if _FS:
        d = _fs_users().document(uid).get()
        return d.to_dict() if d.exists else None
    return _one("SELECT * FROM users WHERE id=?", (uid,))


def get_by_email(email: str):
    email = email.lower().strip()
    if _FS:
        return _fs_first("email", email)
    return _one("SELECT * FROM users WHERE email=?", (email,))


def get_by_google(sub: str):
    if _FS:
        return _fs_first("google_sub", sub)
    return _one("SELECT * FROM users WHERE google_sub=?", (sub,))


def count() -> int:
    if _FS:
        return sum(1 for _ in _fs_users().stream())
    return (_one("SELECT COUNT(*) AS c FROM users") or {"c": 0})["c"]


def create_user(email, name=None, pw=None, google_sub=None) -> str:
    uid = secrets.token_hex(8)
    row = {"id": uid, "email": email.lower().strip(), "name": name or email.split("@")[0],
           "pw_hash": hash_pw(pw) if pw else None, "google_sub": google_sub,
           "plan": "free", "created": time.time()}
    if _FS:
        _fs_users().document(uid).set(row)
    else:
        _exec("INSERT INTO users(id,email,name,pw_hash,google_sub,plan,created) VALUES(?,?,?,?,?,?,?)",
              (row["id"], row["email"], row["name"], row["pw_hash"],
               row["google_sub"], row["plan"], row["created"]))
    return uid


def set_google_sub(uid: str, sub: str) -> None:
    if _FS:
        _fs_users().document(uid).update({"google_sub": sub})
        return
    _exec("UPDATE users SET google_sub=? WHERE id=?", (sub, uid))


# ---- usage ----

def record_event(uid: str, kind: str = "render", meta: str = "") -> None:
    eid = secrets.token_hex(8)
    if _FS:
        _fs().collection("usage_events").document(eid).set(
            {"id": eid, "uid": uid, "kind": kind, "at": time.time(), "meta": meta or ""})
        return
    _exec("INSERT INTO usage_events(id,uid,kind,at,meta) VALUES(?,?,?,?,?)",
          (eid, uid, kind, time.time(), meta or ""))


def usage_summary(uid: str) -> dict:
    month_ago = time.time() - 30 * 86400
    if _FS:
        # single-field query (auto-indexed); filter kind/window in Python so no
        # composite index is needed. Usage rows per user are small.
        total = recent = 0
        for d in _fs().collection("usage_events").where("uid", "==", uid).stream():
            e = d.to_dict()
            if e.get("kind") != "render":
                continue
            total += 1
            if (e.get("at") or 0) >= month_ago:
                recent += 1
        return {"renders_total": total, "renders_30d": recent}
    total = (_one("SELECT COUNT(*) AS c FROM usage_events WHERE uid=? AND kind='render'", (uid,)) or {"c": 0})["c"]
    recent = (_one("SELECT COUNT(*) AS c FROM usage_events WHERE uid=? AND kind='render' AND at>=?",
                   (uid, month_ago)) or {"c": 0})["c"]
    return {"renders_total": total, "renders_30d": recent}
