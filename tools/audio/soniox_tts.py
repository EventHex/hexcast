"""Soniox text-to-speech provider tool + voice-cloning helpers.

TTS:   POST https://tts-rt.soniox.com/tts  (Bearer key) -> raw mp3 bytes.
Voices: https://api.soniox.com/v1/voices  create/list/delete cloned voices.
        A cloned voice's UUID is passed in the same `voice` field as a built-in
        name, so the render path never branches on clone-vs-builtin.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from tools.base_tool import BaseTool, ToolResult

TTS_URL = "https://tts-rt.soniox.com/tts"
API_BASE = "https://api.soniox.com/v1"
MODEL = "tts-rt-v1"

# tts-rt-v1 built-in voices, grouped for the picker. Region tags help the
# founder pick a voice that matches their audience (Indian voices first for
# this workspace's en-IN default).
BUILTIN_VOICES = [
    ("Indian", ["Arjun", "Rohan", "Priya", "Meera"]),
    ("Female", ["Maya", "Nina", "Emma", "Claire", "Grace", "Mina", "Lucia",
                "Sofia", "Isla", "Victoria", "Ruby", "Elise"]),
    ("Male", ["Daniel", "Noah", "Jack", "Adrian", "Owen", "Kenji", "Rafael",
              "Mateo", "Oliver", "Arthur", "Cooper", "Mason"]),
]
DEFAULT_VOICE = "Priya"

# Config language name -> Soniox ISO code. Covers the editor's LANGS list;
# unknown names fall back to English.
LANG_ISO = {
    "English": "en", "Hindi": "hi", "Tamil": "ta", "Telugu": "te",
    "Malayalam": "ml", "Kannada": "kn", "Bengali": "bn", "Gujarati": "gu",
    "Marathi": "mr", "Arabic": "ar", "Spanish": "es", "French": "fr",
    "German": "de", "Portuguese": "pt", "Japanese": "ja", "Korean": "ko",
    "Indonesian": "id",
}


def iso(lang: str | None) -> str:
    return LANG_ISO.get((lang or "English").strip(), "en")


def _key() -> str:
    k = os.environ.get("SONIOX_API_KEY")
    if not k:
        raise RuntimeError("SONIOX_API_KEY not set")
    return k


class SonioxTTS(BaseTool):
    name = "soniox_tts"
    version = "0.1.0"
    capability = "tts"
    provider = "soniox"

    fallback_tools = ["piper_tts"]
    idempotency_key_fields = ["text", "voice", "language", "speed"]
    side_effects = ["writes audio file to output_path", "calls Soniox TTS API"]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "voice": {"type": "string", "description": "Built-in voice name or cloned voice UUID"},
            "language": {"type": "string", "description": "ISO code, e.g. en, hi, ml"},
            "speed": {"type": "number", "default": 1.0, "minimum": 0.7, "maximum": 1.3},
            "output_path": {"type": "string"},
        },
    }

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return round(len(inputs.get("text", "")) * 0.0002, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        import requests

        try:
            api_key = _key()
        except RuntimeError as e:
            return ToolResult(success=False, error=str(e))

        text = (inputs.get("text") or "").strip()
        if not text:
            return ToolResult(success=False, error="empty text")
        # Soniox caps a single request at 5000 chars; segments are far shorter,
        # but guard so a stray long line degrades gracefully instead of 400ing.
        text = text[:5000]
        voice = inputs.get("voice") or DEFAULT_VOICE
        language = inputs.get("language") or "en"
        speed = float(inputs.get("speed") or 1.0)

        start = time.time()
        try:
            r = requests.post(
                TTS_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": MODEL, "language": language, "voice": voice,
                      "audio_format": "mp3", "text": text, "speed": speed},
                timeout=120,
            )
            r.raise_for_status()
        except Exception as exc:
            body = getattr(getattr(exc, "response", None), "text", "")
            return ToolResult(success=False, error=f"Soniox TTS failed: {exc} {body[:200]}".strip())

        out = Path(inputs.get("output_path", "tts_output.mp3"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        return ToolResult(
            success=True,
            data={"provider": self.provider, "model": MODEL, "voice": voice,
                  "language": language, "output": str(out)},
            artifacts=[str(out)],
            model=MODEL,
        )


# ---- voice cloning (management API; separate from synthesis) ----

def list_cloned_voices() -> list[dict]:
    """[{id, name}] of the project's cloned voices (paged, up to 1000)."""
    import requests
    r = requests.get(f"{API_BASE}/voices", headers={"Authorization": f"Bearer {_key()}"},
                     params={"limit": 1000}, timeout=30)
    r.raise_for_status()
    return [{"id": v["id"], "name": v.get("name") or v["id"]} for v in r.json().get("voices", [])]


def create_cloned_voice(name: str, file_bytes: bytes, filename: str) -> dict:
    """Upload a reference clip (few seconds–20s, ≤10 MB) -> {id, name}."""
    import requests
    r = requests.post(
        f"{API_BASE}/voices",
        headers={"Authorization": f"Bearer {_key()}"},
        data={"name": name},
        files={"file": (filename or "sample.mp3", file_bytes)},
        timeout=120,
    )
    r.raise_for_status()
    v = r.json()
    return {"id": v["id"], "name": v.get("name") or name}


def delete_cloned_voice(voice_id: str) -> None:
    import requests
    r = requests.delete(f"{API_BASE}/voices/{voice_id}",
                        headers={"Authorization": f"Bearer {_key()}"}, timeout=30)
    if r.status_code not in (200, 204):
        r.raise_for_status()
