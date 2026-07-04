# Remaster — Product Gap Analysis & Target IA (2026-07-04)

> **BUILT 2026-07-04 — the P0/P1/P2 backlog below is implemented.** App shell
> (Library/Brands/Settings/Help + stats + ⌘K palette), Brands screen with live
> preview, tabbed Settings (Providers&Keys / Workspace / About), first-run
> wizard, editor breadcrumb + inline rename, Publish drawer (video / SRT+VTT /
> transcript / MP3 / thumbnail / GIF / **step-by-step SOP doc** / batch
> languages / reveal-in-Finder), timeline ruler + track labels, library
> search/sort/filter + variant grouping, undo/redo + keyboard shortcuts, job
> retry + log viewer, extension-detection CTA + coach marks. Deferred to launch:
> YouTube/Drive OAuth upload (C6), UI i18n (D9). Verified via Playwright
> screenshot passes (zero console/network errors).


The pipeline and editor are strong; the *product shell* around them is thin.
This doc lists every gap between "a working editor" and "a clean product",
organized by user journey, then defines the target screens/menus and a
prioritized backlog.

## Current surface (inventory)

- **Screens: 2.** Library (grid, upload, settings modal, one onboarding card)
  and Editor (player, timeline, tabs: Script/Zooms/Elements/Audio/Style/⚙).
- **No app shell** — Library and Editor have different, ad-hoc headers.
- **Settings**: providers, keys, retention. Nothing about the user/company.
- **Brands**: exist in the backend (apply / save-from-project) but have **no
  screen** — a brand can't be edited or even inspected after creation.
- **Publishing**: two download links. That's the entire output surface.
- No search, no stats, no sample content, no help, no undo/redo (script
  revert only), no version display, no extension status.

---

## Journey A — first-time user

| # | Gap | Detail |
|---|-----|--------|
| A1 | Welcome is a card, not a flow | Needs a 3-step wizard: (1) key-free vs BYOK choice with key entry, (2) quick brand setup — company name, colors, logo, (3) get footage: install extension (link + detection) / upload / try the sample. Skippable at every step. |
| A2 | No sample project | Empty library = dead first impression. Bundle a 30s sample recording so the first editor open, script edit and export happen in minute one. |
| A3 | No extension pairing status | Library should show "recorder extension: not detected — install" (extension can ping an endpoint; we already accept its uploads). |
| A4 | No workspace/company profile | Company name, website, default narration language, default aspects, default voice. Lives in settings.json; brands and new projects prefill from it. |
| A5 | No guided first edit | First editor open: 4-step coach marks (script line → zoom block → style/brand → export). Dismiss forever. |

## Journey B — returning user

| # | Gap | Detail |
|---|-----|--------|
| B1 | Library is a dumb grid | No search, no sort (date/name/size), no status filter, no tags. Fine at 5 projects, dead at 50. |
| B2 | No workspace stats | "N videos · M exported · X languages · Y GB used" strip — the "how many videos have I made" signal, plus prune shortcut when Y is big. |
| B3 | Raw project ids in editor | Header shows `web-1bb9ac04`; should show the project *name*, inline-renamable, with a ← Library breadcrumb. |
| B4 | No per-project output view | A card should reveal what exists: which aspects, which languages, when last exported, open-in-Finder. |
| B5 | No undo/redo | Only AI-rewrite has revert. Every edit should be undoable (state history is cheap — config+script are tiny JSON). |
| B6 | Batch languages invisible | Derived language projects appear unexplained in the Library; group them under the parent project card. |

## Journey C — publishing (an editor alone is not a product)

| # | Gap | Detail |
|---|-----|--------|
| C1 | Output = 2 file links | Needs a **Publish drawer**: per-aspect files, reveal in Finder, copy path. |
| C2 | No caption/transcript exports | We already have per-line timed text — SRT/VTT export is nearly free. Also plain transcript (.md/.txt). High value, trivial cost. |
| C3 | No audio-only export | Narration MP3 (voiceover reuse) — data already on disk (`seg/tts_*`). |
| C4 | No thumbnail export | Grab current preview frame as PNG (title card = ready thumbnail). |
| C5 | No doc/SOP generation | Trupeer's second headline: step-by-step guide from the same recording (script text + zoom-target screenshots — both already exist in `zoomframes/`). Biggest differentiating gap. |
| C6 | No direct upload | YouTube/Drive via user's own OAuth (BYOK spirit). Later; local-first stays default. |
| C7 | Batch-languages UI misplaced | It's a publishing concern; move from Audio tab into the Publish drawer. |

## Cross-cutting hygiene

| # | Gap | Detail |
|---|-----|--------|
| D1 | No consistent app shell | Two headers, different buttons. One shell: `Library · Brands · Settings · Help` + stats chip; Editor = focused mode with breadcrumb back. |
| D2 | **Brands have no screen** | Top-level Brands page: list → editor (name, colors, logo, font, card style, voice+language, music, outro CTA) with a live card preview. Today a typo'd brand color is uneditable. |
| D3 | Settings shallow + monolithic | Restructure into tabs: **Providers & Keys** / **Workspace** (profile + defaults + storage/retention + data dir) / **About** (version, license, links, changelog). |
| D4 | Job failures are dead ends | Error text only. Add: retry button, view-full-log, and "report issue" prefilled with the log tail. |
| D5 | Batch progress opaque | One job, many steps. Show per-language progress cards. |
| D6 | No version/update signal | Show version in About + Library footer; link to releases. |
| D7 | No in-app help | Help menu: shortcuts list, README/docs link, report issue. |
| D8 | No keyboard shortcuts beyond player | At minimum: space play/pause, ⌘S save-now, ⌘E export, [ ] zoom in/out at playhead. |
| D9 | UI language | English-only UI (fine for launch; note for later). |
| D10 | No telemetry — keep it that way | State explicitly in README: zero tracking. It's a selling point, not a gap. |

---

## Target information architecture

```
App shell (persistent top bar)
├── Library (home)          search · sort · filter · stats strip · project cards
│                            └─ card: thumb, name, status, outputs ▾, languages,
│                               rename/duplicate/delete/reveal
├── Brands                  brand list → brand editor (live card preview)
├── Settings                tabs: Providers & Keys | Workspace | About
├── Help ?                  shortcuts · docs · report issue
└── Editor (focused mode — entered from a project)
    ├── header: ← Library · [project name, inline rename] · Auto-render · Publish ▾
    ├── tabs: Script | Zooms | Elements | Audio | Style
    │        (⚙ tab retired — settings live in the shell)
    └── Publish drawer: aspect files · SRT/VTT · transcript · MP3 · thumbnail ·
                        batch languages · reveal in Finder
First-run wizard (over Library): keys → brand quick-setup → footage (extension/upload/sample)
```

## Prioritized backlog

**P0 — product coherence (next build cycle)**
1. App shell + Brands screen with brand editor (D1, D2)
2. Settings restructure: Providers & Keys / Workspace profile+defaults / About (D3, A4, D6)
3. First-run wizard v2 (A1) + sample project (A2)
4. Editor header: breadcrumb + project name + inline rename (B3)
5. Publish drawer v1: files, SRT/VTT, transcript, thumbnail, reveal in Finder; move batch languages here (C1–C4, C7)

**P1 — returning-user quality**
6. Library search/sort/filter + stats strip (B1, B2)
7. Language variants grouped under parent card (B6)
8. Job failure retry + log viewer (D4); per-language batch progress (D5)
9. Undo/redo (B5); core keyboard shortcuts (D8)
10. Extension detection + install CTA (A3); editor coach marks (A5)

**P2 — differentiation & reach**
11. Doc/SOP generation from recording (C5) — flagship follow-up
12. Audio-only + GIF exports; YouTube/Drive BYOK upload (C6)
13. UI i18n (D9); command palette

Non-goals unchanged: desktop app, collaboration/multi-tenant, avatars, cloud.

---

## UX audit round 2 — screenshot-verified (2026-07-04)

Playwright walkthrough of every screen/state (21 screenshots, console/network
clean: zero JS errors, zero ≥400 responses). Editor core photographs like a
real product — WYSIWYG player, script cards with footage trim, style panel and
export modal all read well. The shell and wayfinding are where it breaks.

**Fixed immediately (verified headless):**
- Esc did not cancel an armed draw tool despite the hint promising it.
- Right-panel tab nav overflowed the viewport 5px, clipping the ⚙ tab.
- 800ms autosave debounce could silently drop the last edit on navigation —
  now flushed with keepalive PUTs on pagehide.

**Confirmed/raised by screenshots (feeds the backlog):**
- **Timeline has no ruler or track labels** (new, high): three unlabeled
  tracks; caption chips overflow with no scroll affordance; a zoom block is an
  anonymous pill. Needs time ruler + track labels (Captions / Zooms / Sounds).
- **Library variant sprawl** (B6, upgrade to P0-adjacent): real workspace
  showed `web-139def41`, `web-139def41 (Tamil)`, `web-139def41 (Malayalam)` as
  unrelated sibling cards — language variants must group under the parent.
- **Raw ids everywhere** (B3 confirmed): editor header and card titles show
  `web-…` ids; names must lead.
- **Settings modal ordering** (D3 detail): API keys — the #1 first-run action —
  are below Storage; OpenAI base-URL/model fields show even when irrelevant;
  provider dropdowns assume vendor knowledge (add "recommended" captions).
- **Downloads affordance** (C1 confirmed): a lone `↓ 16:9` chip bottom-left is
  the entire output surface.
- **Onboarding is visually flat** (A1 detail): no logo/brand color, generic
  copy box — first impression undersells the product.
- **Export modal jitter** (new, small): cache-hit exports flash 0%→done in
  milliseconds; when a job finishes <400ms, skip straight to the done state.

