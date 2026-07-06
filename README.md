# HexCast

**Turn a raw screen recording into a polished, revoiced, brand-framed product
demo video — in your browser, on your machine.**

You record yourself talking through your product once. HexCast transcribes it,
cleans the narration with AI, re-voices it with a studio-quality TTS voice,
zooms in on what you're talking about (driven by your actual clicks), frames it
with your brand, and exports 16:9 / 9:16 / 1:1 — all locally, with your own API
keys.

> Working name. EventHex is the first client: its logo/colors ship as the
> default example brand, not product branding.

## Quickstart

```bash
git clone <repo> hexcast && cd hexcast
python3 -m pip install -r requirements.txt
python3 -m uvicorn app:app --port 8765
# open http://127.0.0.1:8765
```

Requires `ffmpeg` on PATH. Runs with **zero API keys** (local transcription via
`pip install faster-whisper`, your original voice, click-driven zooms). Add
keys in the Settings tab — or `.env` (see `.env.example`) — to unlock cloud
transcription, AI script cleanup, AI zoom targeting, and TTS revoicing.

## Pipeline

transcribe (Groq whisper-large-v3 → local faster-whisper) →
clean + translate (Cerebras / Gemini / any OpenAI-compatible endpoint) →
zoom targets (recorded clicks from `events.json` first, AI-vision grid as
fallback) → TTS revoice (Google Chirp3-HD / ElevenLabs / Piper, 3 retries) →
segment-align (drop dead air, cap speed) → brand frame → export.

TTS audio and segment clips are content-addressed (`seg/tts_<hash>.mp3`,
`seg/clip_<hash>.mp4`): editing one line re-voices and re-slices only that
line; everything else is a cache hit. `script.json` carries a `voiced_sig` —
the fast export path auto-upgrades to a full re-voice when the script text
changed, so captions can never desync from the audio.

## Use

1. Record a demo (tab + your mic) with the recorder extension → `.webm`,
   or upload any screen recording on the **Library** home page.
2. Apply a **brand** (colors, logo, cards, voice, music — set once, reuse on
   every video) or a one-click **style preset**; tweak fonts, captions and
   frame in the Style tab.
3. Edit any script line (only that line re-voices); cut lines and trim the
   footage each line uses; drag zooms on the timeline; draw boxes / blurs /
   redactions / text on the preview.
4. **Export** → 16:9 / 9:16 / 1:1 — or batch-export the same video in other
   languages (translated narration + native voice, one click).

Keys, providers and storage retention live in the **⚙ Settings** tab.

## Layout

- `app.py` — FastAPI server; wraps the pipeline as background jobs
  (per-project lock + 2-render semaphore, cancellable).
- `pipeline/` — transcribe, clean/translate, zoom decide, revoice, align,
  frame/export.
- `editor/` — React + Remotion Player editor (true WYSIWYG preview: zoom /
  captions / elements / music applied live, no render needed). Rebuild after
  changes: `cd editor && npm install && npm run build` (built `dist/` is
  committed so the server works without Node).
- `assets/` — music beds, SFX, voice previews, default example brand.
- `projects/` (gitignored; move with `HEXCAST_DATA_DIR`) — per-project data:
  `config.json`, `script.json`, renders.

## Notes

- Chirp3-HD voices need a `language_code` matching the voice region
  (e.g. `en-IN` voice → `language_code=en-IN`); derived automatically.
- Renders re-encode concats to clean CFR timestamps — downstream overlay
  filters terminate early on non-monotonic PTS otherwise.
- macOS/Linux. Process control (cancel/cleanup) is POSIX-only for now.

## License

AGPL-3.0 — see [LICENSE](LICENSE).
