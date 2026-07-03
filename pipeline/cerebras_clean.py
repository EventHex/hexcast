"""Transcript cleaning via Cerebras (fast) with automatic Gemini fallback.

Usage:
    from pipeline.cerebras_clean import clean_translate
    result = clean_translate(segments, glossary=["EventHex"], target_lang="English")
    # {"engine": "cerebras"|"gemini",
    #  "segments": [{"start": .., "end": .., "original": "..", "clean": ".."}, ...]}

Cerebras is tried first.  On ANY failure (HTTP 402 payment-required, non-200,
timeout, JSON parse error) the call falls back to Gemini gemini-2.5-flash
transparently.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from lib.env_loader import load_env, require_env

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_CEREBRAS_ENDPOINT = "https://api.cerebras.ai/v1/chat/completions"
_CEREBRAS_MODEL = "gemma-4-31b"

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.1-flash-lite:generateContent"
)

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = (
    "You are a professional narration editor. "
    "Rewrite each transcript segment into clean, concise, professional {target_lang} product-demo narration.\n\n"
    "Rules:\n"
    "- Translate to {target_lang} if the text is in another language.\n"
    "- Remove ALL filler words: um, uh, like (filler), you know, so (sentence-starter), "
    "actually, basically, right, I mean, kind of, sort of.\n"
    "- Remove false starts and repetitions.\n"
    "- Fix grammar; keep the SAME meaning and roughly the SAME length (within ~20%).\n"
    "- If a segment is empty or pure filler, return an empty string for 'clean'.\n"
    "{glossary_line}"
    "\n"
    "Input segments (numbered for reference):\n"
    "{numbered_segments}\n\n"
    "Return ONLY valid JSON — NO markdown, NO code fences, NO explanation — exactly:\n"
    '{{"segments":[{{"n":1,"clean":"..."}},{{"n":2,"clean":"..."}}]}}\n'
    "One entry per input segment, same order, using the segment number 'n' shown above."
)


def _build_prompt(segments: list, glossary: list, target_lang: str) -> str:
    numbered = "\n".join(
        f"{i + 1}. [{s['start']:.2f}-{s['end']:.2f}] {s.get('text', '')}"
        for i, s in enumerate(segments)
    )
    glossary_line = (
        f"- Preserve exact brand spellings: {', '.join(glossary)}.\n"
        if glossary
        else ""
    )
    return _PROMPT_TEMPLATE.format(
        target_lang=target_lang,
        glossary_line=glossary_line,
        numbered_segments=numbered,
    )


# ---------------------------------------------------------------------------
# Code-fence stripper
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Low-level callers
# ---------------------------------------------------------------------------

def _call_cerebras(messages: list, max_tokens: int = 1024) -> dict:
    """Call Cerebras chat completions.  Returns parsed response dict.

    Raises:
        requests.HTTPError: on non-2xx (including 402 payment-required).
        requests.Timeout: on timeout.
        Exception: any other transport error.
    """
    load_env()
    api_key = require_env("CEREBRAS_API_KEY")

    payload = {
        "model": _CEREBRAS_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }

    resp = requests.post(
        _CEREBRAS_ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()  # raises HTTPError for 4xx/5xx
    return resp.json()


def _call_openai(messages: list, max_tokens: int = 1024) -> dict:
    """Call any OpenAI-compatible /chat/completions endpoint (OpenAI, Ollama,
    OpenRouter, LM Studio…). Base URL/model come from OPENAI_BASE_URL /
    REMASTER_LLM_MODEL; a key is optional (local endpoints don't need one)."""
    load_env()
    import os
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("REMASTER_LLM_MODEL") or "gpt-4o-mini"
    headers = {"Content-Type": "application/json"}
    if os.environ.get("OPENAI_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["OPENAI_API_KEY"]
    resp = requests.post(f"{base}/chat/completions", headers=headers,
                         json={"model": model, "messages": messages,
                               "max_tokens": max_tokens, "temperature": 0.2},
                         timeout=90)
    resp.raise_for_status()
    return resp.json()


def _provider_chain() -> list:
    """Engines to try, in order. Explicit REMASTER_LLM_PROVIDER pins one;
    'none' turns AI cleanup off; 'auto' = every configured provider."""
    load_env()
    import os
    p = (os.environ.get("REMASTER_LLM_PROVIDER") or "auto").lower()
    if p == "none":
        return []
    if p in ("cerebras", "gemini", "openai"):
        return [p]
    chain = []
    if os.environ.get("CEREBRAS_API_KEY"):
        chain.append("cerebras")
    if os.environ.get("GEMINI_API_KEY"):
        chain.append("gemini")
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL"):
        chain.append("openai")
    return chain


def _engine_json(engine: str, prompt: str, messages: list, max_tokens: int) -> dict:
    """Run one engine, return its parsed JSON output. Raises on any failure."""
    if engine == "cerebras":
        body = _call_cerebras(messages, max_tokens)
        raw = body["choices"][0]["message"]["content"]
    elif engine == "openai":
        body = _call_openai(messages, max_tokens)
        raw = body["choices"][0]["message"]["content"]
    else:
        body = _call_gemini(prompt, max_tokens)
        raw = body["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(_strip_fences(raw))


def _call_gemini(prompt: str, max_tokens: int = 1024) -> dict:
    """Call Gemini generateContent.  Returns parsed response dict."""
    load_env()
    api_key = require_env("GEMINI_API_KEY")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.2,
            "maxOutputTokens": max_tokens,
        },
    }

    resp = requests.post(
        _GEMINI_ENDPOINT,
        params={"key": api_key},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_translate(
    segments: list,
    glossary: Optional[list] = None,
    target_lang: str = "English",
) -> dict:
    """Clean and translate transcript segments; Cerebras-first, Gemini fallback.

    Args:
        segments:    List of {"start": float, "end": float, "text": str}.
        glossary:    Optional list of brand/product names to preserve verbatim.
        target_lang: Target language for the output narration (default "English").

    Returns:
        {
            "engine": "cerebras" | "gemini",
            "segments": [
                {"start": float, "end": float, "original": str, "clean": str},
                ...
            ],
        }

    Raises:
        ValueError: Empty segments list or unparseable API response (after fallback).
        EnvironmentError: Required API key missing.
    """
    if not segments:
        raise ValueError("segments list is empty")

    chain = _provider_chain()
    if not chain:
        # AI cleanup off (or no keys): identity passthrough — keep the words as spoken
        return {"engine": "none",
                "segments": [{"start": s["start"], "end": s["end"],
                              "original": s.get("text", ""),
                              "clean": (s.get("text") or "").strip()} for s in segments]}

    glossary = glossary or []
    prompt = _build_prompt(segments, glossary, target_lang)
    messages = [{"role": "user", "content": prompt}]

    last: Exception | None = None
    for engine in chain:
        try:
            out = _engine_json(engine, prompt, messages, 1024)
            if isinstance(out, dict) and isinstance(out.get("segments"), list):
                return {"engine": engine, "segments": _merge(segments, out["segments"])}
            last = ValueError(f"{engine} response missing 'segments' list")
        except Exception as exc:  # noqa: BLE001  (fall through the chain)
            last = exc
    raise ValueError(f"clean_translate failed on all providers ({', '.join(chain)}): {last}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _merge(original_segments: list, cleaned: list) -> list:
    """Merge cleaned text back with original timing + text."""
    # Build a lookup by position (1-indexed 'n', or positional fallback)
    clean_by_n: dict[int, str] = {}
    for item in cleaned:
        n = item.get("n")
        if n is not None:
            clean_by_n[int(n)] = item.get("clean", "")

    result = []
    for i, seg in enumerate(original_segments):
        n = i + 1
        clean_text = clean_by_n.get(n, "")
        result.append(
            {
                "start": seg["start"],
                "end": seg["end"],
                "original": seg.get("text", ""),
                "clean": clean_text,
            }
        )
    return result


def rewrite_lines(lines: list, glossary: Optional[list] = None, style: str = "concise, confident product-demo narration") -> list:
    """Rewrite each narration line for clarity/flow, preserving meaning + order.
    Returns a list the SAME length as `lines`. Cerebras first, Gemini fallback."""
    gl = ("\nKeep these terms exact: " + ", ".join(glossary)) if glossary else ""
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(lines))
    prompt = (
        f"Rewrite each numbered demo-narration line to be {style}. "
        "Keep the SAME meaning and the SAME number of lines, one rewrite per input line. "
        "Fix grammar, cut filler, make it natural spoken English. Do not merge or split lines." + gl +
        '\nReturn ONLY strict JSON: {"lines":["rewrite 0","rewrite 1", ...]}\n\nLines:\n' + numbered
    )
    out = None
    for engine in _provider_chain():
        try:
            out = _engine_json(engine, prompt, [{"role": "user", "content": prompt}], 1500)
            break
        except Exception:
            continue
    if out is None:
        return list(lines)
    res = out.get("lines") if isinstance(out, dict) else out
    if not isinstance(res, list) or len(res) != len(lines):
        return list(lines)
    return [str(x).strip() or lines[i] for i, x in enumerate(res)]


# ---------------------------------------------------------------------------
# CLI / quick validation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    load_env()

    SAMPLE = [
        {"start": 0, "end": 2.8, "text": "Um so this is like the the website builder you know"},
        {"start": 2.8, "end": 5.5, "text": "and uh basically it generates a whole site for you right"},
    ]
    GLOSSARY = ["EventHex", "website builder"]

    # --- Raw Cerebras probe (before the module logic, to capture exact error shape) ---
    print("=" * 60)
    print("RAW CEREBRAS PROBE")
    print("=" * 60)
    try:
        api_key = require_env("CEREBRAS_API_KEY")
        probe_resp = requests.post(
            _CEREBRAS_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": _CEREBRAS_MODEL,
                "messages": [{"role": "user", "content": "Say hello."}],
                "max_tokens": 16,
            },
            timeout=15,
        )
        print(f"HTTP status : {probe_resp.status_code}")
        print(f"Response    : {probe_resp.text[:300]}")
    except requests.Timeout:
        print("Cerebras probe TIMED OUT")
    except Exception as exc:  # noqa: BLE001
        print(f"Cerebras probe EXCEPTION: {exc}")

    # --- Full clean_translate call ---
    print()
    print("=" * 60)
    print("RUNNING clean_translate (Cerebras → Gemini fallback)")
    print("=" * 60)
    result = clean_translate(SAMPLE, glossary=GLOSSARY, target_lang="English")

    print(f"\nEngine used : {result['engine']}")
    print("\nCleaned segments:")
    pprint.pprint(result["segments"])
