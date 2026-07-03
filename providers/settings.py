"""BYOK settings: per-capability provider choice + API keys.

Stored at <data_dir>/settings.json (chmod 600). Keys entered in the editor's
Settings tab take precedence over .env / shell environment. The server never
returns raw keys to the browser — only {set, hint} via masked_view().

Provider selections ship to the pipeline subprocesses as REMASTER_*_PROVIDER
env vars; keys ship under their canonical env names. Pipeline code keeps
reading os.environ unchanged.
"""
from __future__ import annotations
import json, os

KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "google": "GOOGLE_API_KEY",
}

# "auto" = pick by key availability, degrading to the free local path
# (stt: groq->local, llm: cerebras->gemini->openai->none, vision: same->events-only,
#  tts: google->original voice). Explicit values pin one provider.
DEFAULTS = {
    "stt": {"provider": "auto"},                    # auto | groq | local
    "llm": {"provider": "auto",                     # auto | cerebras | gemini | openai | none
            "openai_base_url": "https://api.openai.com/v1",
            "openai_model": "gpt-4o-mini"},
    "vision": {"provider": "auto"},                 # auto | cerebras | gemini | none
    "tts": {"provider": "auto"},                    # auto | google | elevenlabs | piper | original
    "keys": {k: "" for k in KEY_ENV},
}


def _path(data_dir):
    return os.path.join(data_dir, "settings.json")


def load_settings(data_dir) -> dict:
    s = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        on_disk = json.load(open(_path(data_dir), encoding="utf-8"))
        for sect, vals in on_disk.items():
            if sect in s and isinstance(vals, dict):
                s[sect].update({k: v for k, v in vals.items() if k in s[sect]})
    except Exception:
        pass
    return s


def save_settings(data_dir, patch: dict) -> dict:
    """Merge a partial update. For keys: empty string = leave unchanged,
    None = clear, non-empty = set."""
    s = load_settings(data_dir)
    for sect in ("stt", "llm", "vision", "tts"):
        if isinstance(patch.get(sect), dict):
            s[sect].update({k: v for k, v in patch[sect].items() if k in s[sect]})
    for name, val in (patch.get("keys") or {}).items():
        if name not in KEY_ENV:
            continue
        if val is None:
            s["keys"][name] = ""
        elif isinstance(val, str) and val.strip():
            s["keys"][name] = val.strip()
    p = _path(data_dir)
    json.dump(s, open(p, "w", encoding="utf-8"), indent=1)
    os.chmod(p, 0o600)
    return s


def resolve_key(data_dir, name) -> str | None:
    s = load_settings(data_dir)
    return s["keys"].get(name) or os.environ.get(KEY_ENV[name]) or None


def provider_env(data_dir) -> dict:
    """Env additions for pipeline subprocesses: resolved keys + selections."""
    s = load_settings(data_dir)
    env = {}
    for name, var in KEY_ENV.items():
        v = s["keys"].get(name) or os.environ.get(var)
        if v:
            env[var] = v
    for sect in ("stt", "llm", "vision", "tts"):
        prov = s[sect].get("provider") or "auto"
        if prov != "auto":
            env[f"REMASTER_{sect.upper()}_PROVIDER"] = prov
    if s["llm"].get("openai_base_url"):
        env["OPENAI_BASE_URL"] = s["llm"]["openai_base_url"]
    if s["llm"].get("openai_model"):
        env["REMASTER_LLM_MODEL"] = s["llm"]["openai_model"]
    return env


def masked_view(data_dir) -> dict:
    """Settings snapshot safe to send to the browser: keys become {set, hint, source}."""
    s = load_settings(data_dir)
    out = {sect: dict(s[sect]) for sect in ("stt", "llm", "vision", "tts")}
    keys = {}
    for name, var in KEY_ENV.items():
        own = s["keys"].get(name) or ""
        env = os.environ.get(var) or ""
        val = own or env
        keys[name] = {"set": bool(val),
                      "hint": ("…" + val[-4:]) if val else "",
                      "source": "settings" if own else ("env" if env else "")}
    out["keys"] = keys
    return out
