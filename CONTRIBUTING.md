# Contributing to HexCast

Thanks for helping! HexCast is a local-first, BYOK demo-video studio.

## Dev setup

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn app:app --port 8765 --reload
# editor dev server (proxies API to :8765):
cd editor && npm install && npm run dev
```

Before committing editor changes, rebuild the served bundle — `dist/` is
committed so the server works without Node:

```bash
cd editor && npm run build
```

## Layout

- `app.py` — FastAPI server, background job runner, all HTTP endpoints.
- `pipeline/` — the render pipeline (one script per stage, run as
  subprocesses so jobs are cancellable).
- `providers/` — BYOK: settings store + TTS provider dispatch.
- `brands.py` — workspace-level brand kits.
- `editor/` — React + Remotion Player editor (Library, panels, live preview).
- `assets/` — bundled music/sfx/fonts/voice-previews and the example brand.

## Ground rules

- **Preview–render parity**: any visual control must land in BOTH the editor
  preview (`editor/src/composition/`) and the server render
  (`pipeline/build_revoice.py`, `timeline_fx.py`, `polish_export.py`), and be
  added to the export staleness signatures in `app.py` (`_sigs`).
- **Never touch the content-addressed cache semantics** (`seg/tts_<hash>`,
  `clip_<hash>`, `voiced_sig`) without understanding what invalidates what.
- **No silent provider swaps**: degrading to a free local path is fine;
  auto-hopping between paid providers is not.
- Keys live in `<data>/settings.json` (0600) or `.env` — never in code and
  never returned raw by the API.

## Tests / verification

There is no test suite yet — verify by driving the app:

```bash
python3 -m py_compile app.py brands.py pipeline/*.py providers/*.py
cd editor && npm run build
# then: upload a short recording, export, and (keyless check) pin
# stt=local / llm=none / vision=none / tts=original in ⚙ Settings.
```
