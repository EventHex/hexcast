"""Transcribe step: extract audio from the raw recording -> <proj>/whisper.json
(accurate word/segment timestamps).

Primary engine: Groq whisper-large-v3 (cloud, ~1-2s vs ~30-60s local, same accuracy).
Fallback: local faster-whisper "small" if GROQ_API_KEY is unset or the call fails.

Language is pinned (default "en") because autodetect misfires on Indian-accented
English (detected Tamil/Malayalam and returned garbage in testing). Override with
GROQ_WHISPER_LANG=<iso> for genuinely non-English demos.

Usage: python3 lib/revoice/transcribe.py <project_dir>
  expects <proj>/raw.webm  (or raw.mp4 / raw.mov)
"""
from __future__ import annotations
import sys, os, json, subprocess
from dotenv import load_dotenv
load_dotenv()

GROQ_MODEL = "whisper-large-v3"
GROQ_LANG = os.environ.get("GROQ_WHISPER_LANG", "en")


def find_raw(proj):
    for ext in ("webm", "mp4", "mov", "mkv"):
        p = f"{proj}/raw.{ext}"
        if os.path.exists(p):
            return p
    raise SystemExit(f"no raw.* recording in {proj}")


def groq_transcribe(audio, key):
    """Groq Whisper cloud transcription -> list of {start,end,text}. Raises on failure."""
    import requests
    data = {"model": GROQ_MODEL, "response_format": "verbose_json",
            "timestamp_granularities[]": "segment"}
    if GROQ_LANG:
        data["language"] = GROQ_LANG
    with open(audio, "rb") as f:
        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {key}"},
                          files={"file": (os.path.basename(audio), f, "audio/mpeg")},
                          data=data, timeout=120)
    r.raise_for_status()
    j = r.json()
    segs = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
            for s in j.get("segments", []) if s.get("text", "").strip()]
    return segs, j.get("language", GROQ_LANG or "en"), j.get("duration", 0.0)


def local_transcribe(audio):
    """Local faster-whisper fallback."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit(
            "Local transcription needs faster-whisper: pip install faster-whisper\n"
            "(or add a Groq API key in Settings for fast cloud transcription)")
    m = WhisperModel("small", device="cpu", compute_type="int8")
    segs, info = m.transcribe(audio, word_timestamps=True, vad_filter=True)
    out = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs]
    return out, info.language, info.duration


def main(proj):
    raw = find_raw(proj)
    audio = f"{proj}/audio.mp3"
    subprocess.run(["ffmpeg", "-y", "-i", raw, "-vn", "-ac", "1", "-ar", "16000", audio],
                   capture_output=True)

    # Fail fast on a silent recording (mic was off) — otherwise Whisper hallucinates
    # filler ("you", "thank you") on the silence and the result is garbage.
    import re
    vd = subprocess.run(["ffmpeg", "-i", audio, "-af", "volumedetect", "-f", "null", "-"],
                        capture_output=True, text=True).stderr
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", vd)
    if m and float(m.group(1)) <= -50:
        raise SystemExit("No narration detected — the recording's audio is silent "
                         "(was your microphone on?). Re-record with your voice.")

    key = os.environ.get("GROQ_API_KEY")
    if os.environ.get("REMASTER_STT_PROVIDER", "").lower() == "local":
        key = None   # explicit local mode: skip the cloud attempt entirely
    out = lang = None
    dur = 0.0
    if key:
        for attempt in range(3):   # retry transient Groq errors (429/5xx/timeout) before falling back
            try:
                out, lang, dur = groq_transcribe(audio, key)
                if not out:
                    raise RuntimeError("Groq returned no segments")
                print(f"transcribed via Groq {GROQ_MODEL} (lang={GROQ_LANG or 'auto'}, try {attempt + 1})")
                break
            except Exception as e:
                print(f"Groq attempt {attempt + 1}/3 failed: {e}")
                out = None
    if out is None:
        print("using local faster-whisper (small)")
        out, lang, dur = local_transcribe(audio)

    json.dump({"language": lang, "duration": dur, "segments": out},
              open(f"{proj}/whisper.json", "w"), indent=1, ensure_ascii=False)
    print(f"transcribed: {len(out)} segments, {dur:.1f}s, lang={lang}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "projects/demo-revoice-test")
