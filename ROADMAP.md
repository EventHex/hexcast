# HexCast — A→Z Roadmap

*(working name; product = open-source demo-video studio: raw screen recording → revoiced, brand-framed product video)*

> **Status 2026-07-03: phases 0–9 implemented and verified in this repo**
> (extraction, BYOK settings + providers, zero-key mode, Brand Kit, Library +
> classic-UI removal, Style v2, trim, multi-language batch). Phase 10 repo
> collateral (README/CONTRIBUTING/CI/templates) is in place. Remaining items
> are external: publish the GitHub repo, final name/trademark check, recorder
> extension rename, demo GIF, announcement.

## Context

Decisions locked (2026-07-03):
- **Target user**: SaaS founders / product owners producing many demo videos without editing skills. Benchmark: Trupeer.ai; OSS playbook benchmark: Cap (cap.so).
- **Open source, BYOK-first**: users bring their own AI keys; credible zero-key local mode. Desktop app explicitly dropped.
- **License**: AGPL-3.0 + open-core (cloud/team features paid later).
- **Brand**: "HexCast" (working name — trademark/domain check before announce). EventHex demoted to *first client*: its logo/colors ship only as the default example brand.
- App chrome rebrand already done (editor header, titles, README, FastAPI title).

Architecture facts that drive the plan (verified):
- Pipeline: transcribe (Groq→local faster-whisper) → clean/translate (Cerebras→Gemini) → zoom (events.json clicks first, AI vision fallback) → TTS (Google Chirp3-HD only, content-addressed cache) → align → frame.
- Coupling to OpenMontage repo: 4 external files (`tools/audio/google_tts.py`, `tools/base_tool.py`, `tools/google_credentials.py`, `lib/env_loader.py`), cwd-dependent subprocess spawns, `projects/` at repo root. **Nothing else in OpenMontage imports webstudio/lib-revoice — extraction breaks nothing.**
- `zoom_decide.py:19` crashes at import without `GEMINI_API_KEY` (hard blocker for zero-key).
- Server render hardcodes fonts/caption styles; editor preview (React/Remotion) duplicates them — style features must land in both.

---

## Phase 0 — De-risk in place (0.5d)
- Fix `lib/revoice/zoom_decide.py:19` — lazy `os.environ.get("GEMINI_API_KEY")`, build URL inside `_gemini_vision`.
- `transcribe.py`: honor `HEXCAST_STT_PROVIDER=local` / missing `GROQ_API_KEY` → skip Groq loop cleanly.

**Verify**: unset all keys, run transcribe on a sample project — local whisper path completes; zoom step degrades to events-only without crash.

## Phase 1 — Repo extraction (1.5–2d)
New standalone repo `hexcast/`:

```
app.py  index.html  editor/  assets/  pipeline/   # ex lib/revoice
providers/          # NEW (settings.py, tts.py)
lib/env_loader.py   tools/{base_tool,google_credentials,audio/*}.py   # vendored verbatim
projects/           # gitignored; HEXCAST_DATA_DIR overrides
.env.example  requirements.txt  LICENSE(AGPL-3.0)  README.md
```

Keep `lib/`+`tools/` names so imports resolve unchanged. Changes:
- `pipeline/build_revoice.py:15` — cwd sys.path → anchor `Path(__file__).parents[1]`.
- `pipeline/cerebras_clean.py:24-27` — 3-level walk → 2-level.
- `pipeline/config.py:49-53` — default logo → `assets/`; tolerant loading for stale absolute `logo`/`music` paths in old configs (remap by basename or drop to default).
- `app.py` — `PROJECTS = $HEXCAST_DATA_DIR or ./projects`; `_spawn` passes **absolute** project dirs; script paths + pkill regex `lib/revoice/` → `pipeline/`.
- `requirements.txt` (fastapi, uvicorn, requests, google-auth; `faster-whisper` as optional extra — currently missing from every requirements file). `.env.example` trimmed to the 6 real keys.
- `!editor/dist/` gitignore negation (committed build must survive).

**Verify**: fresh clone in a new directory, `uvicorn app:app`, full record→export cycle with existing keys; old project copied into new data dir still renders.

## Phase 2 — BYOK settings (1–1.5d)
- `providers/settings.py`: `<DATA_DIR>/settings.json` (chmod 600) — per-capability provider choice + keys; `provider_env()` returns canonical env dict; `masked_view()` never leaks raw keys.
- `app.py`: `GET/PUT /api/settings`; inject `env={**os.environ, **provider_env()}` into every subprocess spawn (pipeline code keeps reading env unchanged — minimal blast radius).
- Editor: new `SettingsPanel.jsx` + "settings" tab — provider dropdowns, masked key inputs, OpenAI base-url/model fields.
- Policy: **explicit provider choice per capability; degrade only to free local paths, never auto-hop between paid providers.**

**Verify**: enter key in browser → stored masked → render uses it with env vars stripped from shell.

## Phase 3 — New providers (2–3d)
- `providers/tts.py`: `synth(text, voice, lang, output_path, provider)` dispatching Google / ElevenLabs / Piper (BaseTool classes already exist, vendored). Swap only the inner call in `build_revoice.py:342-371`; **do not touch** ThreadPoolExecutor, 3-retry, `_needs_tts`, content-addressed cache, `voiced_sig`.
- `GET /api/voices?provider=elevenlabs` (live voice list + `preview_url`; optional cache under `<DATA_DIR>/voice_cache/`). AudioPanel provider switch; preview button uses `preview_url` for ElevenLabs.
- LLM: `_call_openai_compat` in `cerebras_clean.py` (`OPENAI_BASE_URL` covers OpenAI/Ollama/OpenRouter/LM Studio); gate on `HEXCAST_LLM_PROVIDER` incl. `none` = identity passthrough. Same gating for vision (`none` → events-only zooms).

**Verify**: one project rendered per provider path (Google, ElevenLabs, Piper, Ollama-clean, none/none).

## Phase 4 — Zero-key polish (0.5d)
- No keys → defaults `stt=local, llm=none, vision=none, tts=original` (original-voice path already implemented); "running key-free" banner; README quickstart works keyless (with faster-whisper extra installed).

**Verify**: fresh machine simulation, zero keys: clone → install → record → export succeeds.

**→ Repo goes public here (soft launch — no announcement yet). Phases 0–4 ≈ 1 week.**

## Phase 5 — Brand Kit (3–4d)
- `<DATA_DIR>/brands/<id>/` + `brand.json`: colors, logo, wallpaper, card style + colors, frame defaults, font (Phase 7), voice+language, music bed + gain, outro CTA text. Multiple brands (agency case).
- API CRUD `/api/brands`; new project picks a brand → config prefilled; "Match brand" resets per-project overrides. EventHex seeded as the example brand.

**Verify**: two brands, two projects each — new project inherits correct brand with zero manual styling.

## Phase 6 — Library home + kill classic UI (3–4d)
- Editor gains a home route: project gallery (thumbnail, name, status raw/ready/exported, storage size), rename/duplicate/delete/search, upload + "waiting for extension" entry.
- Serve editor at `/`; **delete `index.html` (classic UI)** once upload/create/delete parity confirmed; extension redirect updated.
- Retention: configurable auto-prune of raw/base/seg artifacts after N days (keep `config.json`, `script.json`, exports); per-project storage shown. (Local `projects/` is already 1.8G — this is the fix.)

**Verify**: full lifecycle without classic UI; prune dry-run lists expected files only.

## Phase 7 — Style system v2 (4–6d)
Parity rule: every control lands in **both** editor preview (`DemoComposition.jsx`/`CardPreview.jsx`) and server render (`build_revoice.py card()`, `timeline_fx.py`, `polish_export.py`).
- Typography: bundle 3–5 OFL TTFs in `assets/fonts/` (also fixes Linux font risk), font picker + upload-to-brand; title/subtitle/caption sizes; alignment.
- Captions: position, size, colors, background style (pill/bar/none) — replace hardcoded values in `timeline_fx._caption_filters` + preview.
- Cards: presets become editable templates (bg type solid/linear/radial/mesh/image, text colors, logo placement, alignment) + per-card overrides; save-as-preset into brand.
- Elements: expose existing server-side `color`/`size` params in UI; add arrow + spotlight/dim types; entrance animation (fade/pop).
- One-click style presets gallery (coherent frame+bg+card+caption+font sets).

**Verify**: golden-frame comparison — exported frame matches editor preview for each control.

## Phase 8 — Timeline trim (2–3d)
- Manual cut/drop ranges on the editor timeline (auto dead-air drop already exists in align); ripple script segments/zooms/elements accordingly.

## Phase 9 — Multi-language batch export (2–3d)
- Same script → N languages (translate path exists) → per-language voice → batch render queue → `framed-<lang>-<aspect>.mp4`. Killer feature vs Trupeer for global SaaS.

## Phase 10 — Announce launch (1–2d)
- Final name check (trademark/domain) — swap the working name if needed (single-string surfaces).
- README with GIF demo (dogfood the product for its own demo video), docs quickstart, CONTRIBUTING, issue templates, CI (editor build + lint).
- Extension: rename to match brand, own README link, Chrome Web Store listing (optional now).
- Show HN / Product Hunt / r/SaaS.

---

## Non-goals (explicit)
Desktop app (dropped), full NLE timeline, collaboration/multi-tenant, avatar generation (later as optional BYOK provider), voice cloning (later, ElevenLabs BYOK makes it near-free to add).

## Standing risks
- POSIX-only process control (pkill/killpg) → document macOS/Linux at launch.
- `settings.json` plaintext keys → chmod 600, keep server on 127.0.0.1, flag `allow_origins=["*"]`.
- Editor preview vs server render drift → Phase 7 parity rule + golden-frame checks.
- faster-whisper pulls torch → optional extra, clear error if missing on keyless run.
