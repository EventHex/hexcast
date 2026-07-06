"""TTS dispatch: one synth() call, provider-routed (google | elevenlabs | piper).

build_revoice keeps its retry loop, thread pool, and content-addressed cache —
this module only hides which backend produces the mp3. Raises on failure so the
caller's retry/abort logic stays in charge.
"""
from __future__ import annotations
import os, subprocess

from tools.audio.google_tts import GoogleTTS
from tools.audio.elevenlabs_tts import ElevenLabsTTS
from tools.audio.piper_tts import PiperTTS
from tools.audio import soniox_tts

_GOOGLE_HINTS = ("Chirp", "Journey", "Neural2", "Wavenet", "Standard", "Studio")


def resolve_provider(explicit: str | None = None) -> str:
    """Effective TTS provider. 'auto' = google if a key is configured, else
    'original' (keep the recorded voice — the zero-key default)."""
    p = (explicit or os.environ.get("HEXCAST_TTS_PROVIDER") or "auto").lower()
    if p != "auto":
        return p
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") \
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return "google"
    return "original"


def _looks_google(voice: str) -> bool:
    return any(h in (voice or "") for h in _GOOGLE_HINTS)


def synth(text: str, voice: str, output_path: str, provider: str | None = None,
          language: str | None = None) -> None:
    """Generate speech to output_path (mp3). `language` is the config language
    name (e.g. 'Malayalam'); only Soniox needs it (built-in voices are language
    -agnostic). Raises RuntimeError on failure."""
    p = resolve_provider(provider)
    if p == "google":
        lang = "-".join(voice.split("-")[:2]) if _looks_google(voice) else "en-US"
        r = GoogleTTS().execute({"text": text, "voice": voice, "language_code": lang,
                                 "output_path": output_path})
    elif p == "elevenlabs":
        # a leftover Google voice name in config means "use the default voice"
        inputs = {"text": text, "output_path": output_path}
        if voice and not _looks_google(voice):
            inputs["voice_id"] = voice
        r = ElevenLabsTTS().execute(inputs)
    elif p == "soniox":
        # a leftover Google voice code means "use the default built-in voice"
        v = voice if (voice and not _looks_google(voice)) else soniox_tts.DEFAULT_VOICE
        r = soniox_tts.SonioxTTS().execute({"text": text, "voice": v,
                                            "language": soniox_tts.iso(language),
                                            "output_path": output_path})
    elif p == "piper":
        wav = output_path.rsplit(".", 1)[0] + ".wav"
        model = voice if voice and not _looks_google(voice) else "en_US-lessac-medium"
        r = PiperTTS().execute({"text": text, "model": model, "output_path": wav})
        if r.success:
            enc = subprocess.run(["ffmpeg", "-y", "-i", wav, "-codec:a", "libmp3lame",
                                  "-q:a", "2", output_path], capture_output=True)
            try:
                os.remove(wav)
            except OSError:
                pass
            if enc.returncode != 0:
                raise RuntimeError("piper wav->mp3 encode failed")
    else:
        raise RuntimeError(f"unknown TTS provider {p!r} (original-voice mode never calls synth)")
    if not r.success:
        raise RuntimeError(r.error or f"{p} TTS failed")


def list_voices(provider: str) -> list[dict]:
    """[{id, name, preview_url, group, cloned}] for pickable providers.
    ElevenLabs and Soniox are live (per-account voices); Google voices stay
    hardcoded in the editor with bundled preview MP3s. Soniox has no preview
    endpoint, so preview_url is null."""
    if provider == "soniox":
        out = []
        for group, names in soniox_tts.BUILTIN_VOICES:
            for n in names:
                out.append({"id": n, "name": n, "preview_url": None, "group": group, "cloned": False})
        try:
            for v in soniox_tts.list_cloned_voices():
                out.append({"id": v["id"], "name": v["name"], "preview_url": None,
                            "group": "My cloned voices", "cloned": True})
        except Exception:
            pass  # no key / API down: still return the built-ins
        return out
    if provider == "elevenlabs":
        import requests
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")
        r = requests.get("https://api.elevenlabs.io/v1/voices",
                         headers={"xi-api-key": key}, timeout=30)
        r.raise_for_status()
        out = []
        for v in r.json().get("voices", []):
            labels = v.get("labels") or {}
            desc = ", ".join(x for x in (labels.get("gender"), labels.get("accent")) if x)
            out.append({"id": v["voice_id"],
                        "name": v.get("name", v["voice_id"]) + (f" · {desc}" if desc else ""),
                        "preview_url": v.get("preview_url")})
        return out
    raise RuntimeError(f"no live voice list for provider {provider!r}")
