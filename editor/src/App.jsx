import React, { useCallback, useEffect, useRef, useState } from "react";
import { Player } from "@remotion/player";
import { DemoComposition, timing } from "./composition/DemoComposition.jsx";
import { ScriptPanel } from "./components/ScriptPanel.jsx";
import { ZoomPanel } from "./components/ZoomPanel.jsx";
import { StylePanel } from "./components/StylePanel.jsx";
import { AudioPanel } from "./components/AudioPanel.jsx";
import { ElementsPanel } from "./components/ElementsPanel.jsx";
import { PublishDrawer } from "./components/PublishDrawer.jsx";
import { ExportDrawer } from "./components/ExportDrawer.jsx";
import { Shell } from "./components/Shell.jsx";
import { ThemeToggle } from "./components/ThemeToggle.jsx";
import { Timeline } from "./components/Timeline.jsx";
import { api, jput, post, pollJob } from "./api.js";

const FPS = 30;

export default function App({ user, onLogout } = {}) {
  const [pid, setPid] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [script, setScript] = useState({ segments: [], zooms: [], elements: [] });
  const [src, setSrc] = useState(null); // {file, dur, w, h, generated}
  const [tab, setTab] = useState("script");
  const [selZ, setSelZ] = useState(-1);
  const [status, setStatus] = useState("Loading…");
  const [job, setJob] = useState(null);
  const [playheadS, setPlayheadS] = useState(0);
  const [videoKey, setVideoKey] = useState(0);
  const [generated, setGenerated] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [drawMode, setDrawMode] = useState(null);
  const [drawRect, setDrawRect] = useState(null);
  const playerRef = useRef(null);
  const stageRef = useRef(null);
  const busy = useRef(false);
  const editSeq = useRef(0);   // bumped on every edit; auto-render checks it to detect mid-render edits
  const hist = useRef({ past: [], future: [], lastAt: 0, applying: false });
  const snap = useRef({});     // current committed {cfg, script}, read by history
  // narration voice/provider shown in the header badge (kept in sync with the
  // Audio panel via onProviderChange / onVoicesLoaded)
  const [ttsProvider, setTtsProvider] = useState("auto");
  const [googleSet, setGoogleSet] = useState(false);
  const [voiceList, setVoiceList] = useState([]);
  useEffect(() => {
    api("/api/settings").then((v) => {
      setTtsProvider(v.tts?.provider || "auto"); setGoogleSet(!!v.keys?.google?.set);
    }).catch(() => {});
  }, []);
  const effProvider = ttsProvider === "auto" ? (googleSet ? "google" : "original") : ttsProvider;
  useEffect(() => {
    if (effProvider === "elevenlabs" || effProvider === "soniox")
      api(`/api/voices?provider=${effProvider}`).then((r) => setVoiceList(r.voices || [])).catch(() => setVoiceList([]));
  }, [effProvider]);

  // snapshot the pre-edit state, coalescing rapid edits into one undo step
  const pushHistory = () => {
    const h = hist.current;
    if (h.applying || !snap.current.cfg) return;
    const now = performance.now();
    if (h.past.length && now - h.lastAt < 450) { h.lastAt = now; return; }
    h.past.push(snap.current);
    if (h.past.length > 60) h.past.shift();
    h.future = [];
    h.lastAt = now;
  };

  // edits after the last export make the downloadable files stale
  const setScriptD = useCallback((v) => { pushHistory(); editSeq.current++; setDirty(true); setScript(v); }, []);
  const setCfgD = useCallback((v) => { pushHistory(); editSeq.current++; setDirty(true); setCfg(v); }, []);

  // autosave: every edit persists to the server after a short pause, so
  // nothing is lost on reload and server-side actions never see stale state.
  // Paused while a render job runs so it can't clobber the render's own write.
  const saveTick = useRef(0);
  useEffect(() => {
    if (!pid || !cfg || !dirty || job) return;
    const tick = ++saveTick.current;
    const id = setTimeout(async () => {
      try {
        await jput(`/api/projects/${pid}/config`, cfg);
        await jput(`/api/projects/${pid}/script`, script);
        if (tick === saveTick.current && !busy.current && !job) setStatus("Saved ✓");
      } catch {}
    }, 800);
    return () => clearTimeout(id);
  }, [cfg, script, dirty, pid, job]);

  const load = useCallback(async (id) => {
    const [c, s, meta] = await Promise.all([
      api(`/api/projects/${id}/config`),
      api(`/api/projects/${id}/script`),
      api(`/api/projects/${id}/source`),
    ]);
    setCfg(c);
    setScript({ zooms: [], elements: [], sounds: [], ...s });
    setSrc(meta.preview || null);
    setGenerated(!!meta.generated);
    setStatus(meta.preview ? "Ready" : "No media yet — upload from the Library, or record with the extension");
    return meta;
  }, []);

  const [noProject, setNoProject] = useState(false);
  const prepared = useRef(false);
  useEffect(() => {
    const id = new URLSearchParams(location.search).get("project");
    if (!id) { setNoProject(true); return; }
    setPid(id);
    (async () => {
      const meta = await load(id);
      // fresh recording straight from the extension: no script yet — run the
      // transcribe+script phase automatically so the editor is ready to edit
      if (meta.preview && !meta.has_script && !prepared.current) {
        prepared.current = true;
        setStatus("Reading your demo — transcribing…");
        const r = await post(`/api/projects/${id}/prepare`);
        setJob(r.job);
        const end = await pollJob(r.job, (s) =>
          setStatus(s.status === "queued" ? "Queued…" : s.step || s.status));
        setJob(null);
        if (end === "done") {
          await load(id);
          setStatus("Script ready — edit, then Render");
        } else {
          const s = await api(`/api/jobs/${r.job}`).catch(() => null);
          setStatus("Transcription failed — " +
            ((s?.error || "").split("\n").filter(Boolean).pop() || "check the mic/recording"));
        }
      }
    })().catch(() => setStatus("Failed to load project"));
  }, [load]);

  useEffect(() => {
    const p = playerRef.current;
    if (!p) return;
    const onFrame = (e) => setPlayheadS(e.detail.frame / FPS);
    p.addEventListener("frameupdate", onFrame);
    return () => p.removeEventListener("frameupdate", onFrame);
  }, [src]);

  // Esc cancels an armed draw tool (the Elements hint promises this)
  useEffect(() => {
    if (!drawMode) return;
    const onKey = (e) => {
      if (e.key === "Escape") { setDrawMode(null); setDrawRect(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawMode]);

  // navigating away inside the 800ms autosave debounce would silently drop the
  // last edit — flush it with keepalive requests on pagehide
  const latest = useRef({});
  latest.current = { pid, cfg, script, dirty };
  useEffect(() => {
    const flush = () => {
      const { pid, cfg, script, dirty } = latest.current;
      if (!pid || !cfg || !dirty) return;
      const opts = (b) => ({ method: "PUT", keepalive: true,
                             headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
      fetch(`/api/projects/${pid}/config`, opts(cfg)).catch(() => {});
      fetch(`/api/projects/${pid}/script`, opts(script)).catch(() => {});
    };
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, []);

  const seekTo = useCallback((t) => {
    playerRef.current?.seekTo(Math.round(t * FPS));
  }, []);
  const playhead = useCallback(() => playheadS, [playheadS]);

  const saveAll = async () => {
    await jput(`/api/projects/${pid}/config`, cfg);
    await jput(`/api/projects/${pid}/script`, script);
  };

  const [modal, setModal] = useState(null); // {phase, step, error, minimized}
  const [prog, setProg] = useState(0);
  const [downloadKey, setDownloadKey] = useState(0);   // cache-bust download links after a render
  const [autoRender, setAutoRender] = useState(() => localStorage.getItem("remaster_autorender") === "1");
  const [editingName, setEditingName] = useState(false);
  const [showPublish, setShowPublish] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [coach, setCoach] = useState(() => !localStorage.getItem("remaster_coached"));
  const dismissCoach = () => { localStorage.setItem("remaster_coached", "1"); setCoach(false); };

  const commitName = async (name) => {
    setEditingName(false);
    const n = (name || "").trim();
    if (!n || n === (cfg?.name || pid)) return;
    setCfg((c) => ({ ...c, name: n }));
    try { await jput(`/api/projects/${pid}/name`, { name: n }); } catch {}
  };

  const run = async (endpoint, label, { auto = false } = {}) => {
    if (busy.current) return;
    busy.current = true;
    const seq0 = editSeq.current;
    setStatus(label + "…");
    setProg(0);
    setModal({ phase: "running", step: label + "…", minimized: auto });
    try {
      await saveAll();
      const r = await post(`/api/projects/${pid}/${endpoint}`);
      if (r.nothing) {
        setStatus(auto ? "Up to date ✓" : "Already up to date");
        setModal(auto ? null : { phase: "done" });
        busy.current = false;
        return;
      }
      setJob(r.job);
      lastJob.current = r.job;
      const end = await pollJob(r.job, (s) => {
        const step = s.status === "queued" ? "Queued — waiting for a free render slot…" : s.step || s.status;
        setStatus((auto ? "Auto-render: " : "") + step);
        setProg(s.progress || 0);
        setModal((m) => (m ? { ...m, step } : m));
      });
      setJob(null);
      if (end === "done") {
        setDownloadKey((k) => k + 1);   // point the download links at the fresh files
        // only clear "dirty" if no edits landed while rendering — else keep it
        // so auto-render fires again for the newer changes
        if (editSeq.current === seq0) setDirty(false);
        setProg(1);
        if (auto) {
          // background pass: keep the live preview + playhead untouched, just
          // refresh the downloadable files quietly
          setGenerated(true);
          setStatus("Auto-render complete ✓");
          setModal(null);
        } else {
          await load(pid);
          setVideoKey((k) => k + 1);
          setStatus("Render complete");
          setModal({ phase: "done" });
        }
      } else {
        const s = await api(`/api/jobs/${r.job}`).catch(() => null);
        const err = (s?.error || "").split("\n").filter(Boolean).pop() || end;
        setStatus(end === "cancelled" ? "Cancelled" : "Failed — " + err);
        setModal(end === "cancelled" ? (auto ? null : { phase: "cancelled" })
                                     : { phase: "error", error: err });
      }
    } catch (e) {
      setStatus("Request failed");
      setModal(auto ? null : { phase: "error", error: "Request failed — is the server running?" });
    }
    busy.current = false;
  };

  // auto-render: after edits settle (and only when idle), run the incremental
  // export in the background. Reuses the server's stage-diffing, so an SFX-only
  // change is a fast fx pass — no re-voice. Opt-in; renders are CPU-heavy.
  const toggleAuto = () => {
    const v = !autoRender;
    setAutoRender(v);
    localStorage.setItem("remaster_autorender", v ? "1" : "0");
  };
  useEffect(() => {
    if (!autoRender || !dirty || job || !pid) return;
    const id = setTimeout(() => { if (!busy.current) run("render", "Auto-render", { auto: true }); }, 4000);
    return () => clearTimeout(id);
  }, [autoRender, dirty, job, cfg, script, pid]);

  // keep the history's view of "current state" fresh each render
  snap.current = { cfg, script };
  const applyHistory = (from, to) => {
    const h = hist.current;
    if (!from.length) return;
    to.push(snap.current);
    const prev = from.pop();
    h.applying = true;
    setCfg(prev.cfg); setScript(prev.script); setDirty(true); editSeq.current++;
    setTimeout(() => { h.applying = false; }, 0);
  };
  const undo = () => applyHistory(hist.current.past, hist.current.future);
  const redo = () => applyHistory(hist.current.future, hist.current.past);

  // keyboard shortcuts (Help lists these)
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target, editable = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      const cmd = e.metaKey || e.ctrlKey;
      if (cmd && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
      if (cmd && e.key.toLowerCase() === "s") { e.preventDefault(); saveAll().then(() => setStatus("Saved ✓")); return; }
      if (cmd && e.key.toLowerCase() === "e") { e.preventDefault(); if (!job) run("render", "Rendering video"); return; }
      if (e.key === " " && !editable) {
        e.preventDefault();
        const p = playerRef.current; if (p) (p.isPlaying?.() ? p.pause() : p.play());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [job, pid, cfg, script]);

  const cancel = () => job && post(`/api/jobs/${job}/cancel`).catch(() => {});
  const lastJob = useRef(null);
  const viewLog = async () => {
    if (modal?.log) { setModal((m) => ({ ...m, log: null })); return; }
    if (!lastJob.current) return;
    const s = await api(`/api/jobs/${lastJob.current}`).catch(() => null);
    setModal((m) => ({ ...m, log: (s?.log || []).join("\n\n").slice(-4000) || "no log" }));
  };

  if (noProject) {
    return <Shell user={user} onLogout={onLogout} />;
  }
  if (!cfg || !src) {
    return <div className="boot">{status}</div>;
  }

  const musicSrc = cfg.music
    ? String(cfg.music).includes("assets/music")
      ? `/assets/music/${String(cfg.music).split("/").pop()}`
      : `/media/${pid}/${String(cfg.music).split("/").pop()}`
    : null;
  // live timeline: replace the baked intro/outro with live cards, so duration
  // reacts to intro/outro seconds instantly
  const T = timing(script, cfg, src.dur);
  const durF = Math.max(1, Math.ceil(T.total * FPS));
  const seekBaked = (t) => seekTo(t + T.shift);
  const playheadBaked = () => Math.max(0, playheadS - T.shift);

  const onDrawDown = (e) => {
    if (!drawMode || !stageRef.current) return;
    e.preventDefault();
    const box = stageRef.current.getBoundingClientRect();
    const nx = (v) => Math.max(0, Math.min(1, (v - box.left) / box.width));
    const ny = (v) => Math.max(0, Math.min(1, (v - box.top) / box.height));
    const x0 = nx(e.clientX), y0 = ny(e.clientY);
    const move = (ev) => setDrawRect({
      x: Math.min(x0, nx(ev.clientX)), y: Math.min(y0, ny(ev.clientY)),
      w: Math.abs(nx(ev.clientX) - x0), h: Math.abs(ny(ev.clientY) - y0),
    });
    const up = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      const r = {
        x: Math.min(x0, nx(ev.clientX)), y: Math.min(y0, ny(ev.clientY)),
        w: Math.abs(nx(ev.clientX) - x0), h: Math.abs(ny(ev.clientY) - y0),
      };
      setDrawRect(null);
      setDrawMode(null);
      if (r.w < 0.01 || r.h < 0.01) return;
      const el = {
        type: drawMode.type,
        ...(drawMode.src ? { src: drawMode.src } : {}),
        ...(drawMode.type === "text" ? { text: "Label" } : {}),
        x: +r.x.toFixed(3), y: +r.y.toFixed(3), w: +r.w.toFixed(3), h: +r.h.toFixed(3),
        start: +playheadBaked().toFixed(1), end: +(playheadBaked() + 3).toFixed(1),
      };
      setScriptD((s) => ({ ...s, elements: [...(s.elements || []), el] }));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div className="app">
      <header>
        <a className="crumb" href="/editor/" title="Back to Library">←</a>
        {editingName ? (
          <input className="nameedit" autoFocus defaultValue={cfg.name || pid}
                 onBlur={(e) => commitName(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); if (e.key === "Escape") setEditingName(false); }} />
        ) : (
          <span className="projname" title="Click to rename" onClick={() => setEditingName(true)}>{cfg.name || pid}</span>
        )}
        <span className="grow" />
        <ThemeToggle />
        {(() => {
          const PL = { google: "Google", elevenlabs: "ElevenLabs", soniox: "Soniox", piper: "Piper", original: "Original" };
          let vn;
          if (cfg.original_voice || effProvider === "original") vn = "your recording";
          else {
            const v = cfg.voice || "";
            if (effProvider === "google") { const p = v.split("-"); vn = p[p.length - 1] || v; }
            else if (effProvider === "piper") vn = v || "default";
            else vn = voiceList.find((x) => x.id === v)?.name || (/^[0-9a-f-]{20,}$/i.test(v) ? "cloned voice" : (v || "default"));
          }
          return (
            <button className="voicebadge" title="Narration voice used when you render — click to change"
                    onClick={() => setTab("audio")}>🎙 <b>{PL[effProvider] || effProvider}</b> · {vn}</button>
          );
        })()}
        <span className="status">{status}</span>
        {modal?.phase === "running" && modal.minimized && (
          <button className="chip" onClick={() => setModal((m) => ({ ...m, minimized: false }))}>
            <span className="chip-bar"><span style={{ width: `${Math.round(prog * 100)}%` }} /></span>
            Rendering {Math.round(prog * 100)}%
          </button>
        )}
        {job && !modal?.minimized && <button className="btn sm danger" onClick={cancel}>Cancel</button>}
        <label className="chk" title="Re-render in the background a few seconds after you stop editing. Only the changed stage runs (e.g. a sound effect = fast pass, no re-voice).">
          <input type="checkbox" checked={autoRender} onChange={toggleAuto} /> Auto-render
        </label>
        <button className="btn" disabled={!!job}
                title="Build the video from your current edits. Runs only the stages your edits changed — framing is instant, re-voicing only when the script or voice changed."
                onClick={() => run("render", "Rendering video")}>▶ Render</button>
        <button className="btn sm ghost" disabled={!!job} title="Download the finished video (any size) + thumbnail, GIF and audio"
                onClick={() => setShowExport(true)}>⬇ Export ▾</button>
        <button className="btn sm ghost" title="Captions, transcript, step-by-step guide and other-language versions"
                onClick={() => setShowPublish(true)}>Publish ▾</button>
      </header>

      {showExport && (
        <ExportDrawer pid={pid} cfg={cfg} downloadKey={downloadKey}
                      onClose={() => setShowExport(false)} onRender={() => { setShowExport(false); run("render", "Rendering video"); }} />
      )}
      {showPublish && (
        <PublishDrawer pid={pid} cfg={cfg} downloadKey={downloadKey}
                       onClose={() => setShowPublish(false)} setStatus={setStatus}
                       onRender={() => { setShowPublish(false); run("render", "Rendering video"); }} />
      )}

      {coach && src && (
        <div className="coach">
          <div className="coach-head"><b>Quick start</b><button className="mini" onClick={dismissCoach}>×</button></div>
          <ol>
            <li><b>Script</b> — edit any line; the voice re-renders to match.</li>
            <li><b>Zooms</b> — add or drag zoom blocks on the timeline.</li>
            <li><b>Style</b> — pick a brand or preset; tweak cards & captions.</li>
            <li><b>Render → Export → Publish</b> — build the video, download it, then share captions & other languages.</li>
          </ol>
          <button className="btn sm wide" onClick={dismissCoach}>Got it</button>
        </div>
      )}

      {modal && !modal.minimized && (
        <div className="modal-wrap">
          <div className="modal">
            {modal.phase === "running" && (
              <>
                <div className="pct">{Math.round(prog * 100)}%</div>
                <div className="pbar"><span style={{ width: `${Math.round(prog * 100)}%` }} /></div>
                <p className="modal-step">{modal.step}</p>
                <p className="hint">Fast pass when only zooms/captions/style changed; full re-voice when the script or voice changed.</p>
                <div className="row gap" style={{ justifyContent: "center" }}>
                  <button className="btn sm ghost" onClick={() => setModal((m) => ({ ...m, minimized: true }))}>
                    Run in background
                  </button>
                  <button className="btn sm danger" onClick={cancel}>Cancel render</button>
                </div>
              </>
            )}
            {modal.phase === "done" && (
              <>
                <div className="modal-ok">✓</div>
                <h3>Render complete</h3>
                <p className="hint">Your video is built. Download it below, or open Publish for captions &amp; other languages.</p>
                <div className="row gap" style={{ justifyContent: "center" }}>
                  {(cfg.aspects || ["16x9", "9x16"]).map((a) => (
                    <a key={a} className="btn" href={`/media/${pid}/framed-${a}.mp4?v=${downloadKey}`} download>
                      ⬇ {a.replace("x", ":")}
                    </a>
                  ))}
                </div>
                <div className="row gap" style={{ justifyContent: "center" }}>
                  <button className="btn sm ghost" onClick={() => { setModal(null); setShowExport(true); }}>All sizes &amp; files → Export</button>
                  <button className="btn sm ghost" onClick={() => { setModal(null); setShowPublish(true); }}>Captions &amp; languages → Publish</button>
                </div>
                <button className="btn sm ghost" onClick={() => setModal(null)}>Close</button>
              </>
            )}
            {modal.phase === "cancelled" && (
              <>
                <h3>Render cancelled</h3>
                <button className="btn sm ghost" onClick={() => setModal(null)}>Close</button>
              </>
            )}
            {modal.phase === "error" && (
              <>
                <div className="modal-err">!</div>
                <h3>Render failed</h3>
                <p className="modal-step">{modal.error}</p>
                {modal.log && <pre className="joblog">{modal.log}</pre>}
                <div className="row gap" style={{ justifyContent: "center" }}>
                  <button className="btn sm ghost" onClick={() => setModal(null)}>Close</button>
                  <button className="btn sm ghost" onClick={viewLog}>{modal.log ? "Hide log" : "View log"}</button>
                  <button className="btn sm" onClick={() => run("render", "Rendering video")}>Retry</button>
                </div>
                <a className="hint" href={`https://github.com/issues/new?body=${encodeURIComponent("Export failed:\n" + (modal.error || ""))}`}
                   target="_blank" rel="noreferrer">Report this issue →</a>
              </>
            )}
          </div>
        </div>
      )}

      <main>
        <section className="stage">
          <div className="player-wrap" ref={stageRef} style={{ position: "relative" }}>
            <Player
              key={videoKey}
              ref={playerRef}
              component={DemoComposition}
              inputProps={{
                videoSrc: `/media/${pid}/${src.file}?v=${videoKey}`,
                script,
                cfg,
                musicSrc,
                pid,
                srcAr: src.w / src.h,
                srcDur: src.dur,
              }}
              durationInFrames={durF}
              fps={FPS}
              compositionWidth={1920}
              compositionHeight={1080}
              controls
              acknowledgeRemotionLicense
              style={{ width: "100%" }}
            />
            {drawMode && (
              <div className="drawlayer" onPointerDown={onDrawDown}>
                {drawRect && (
                  <div
                    className="drawrect"
                    style={{
                      left: `${drawRect.x * 100}%`, top: `${drawRect.y * 100}%`,
                      width: `${drawRect.w * 100}%`, height: `${drawRect.h * 100}%`,
                    }}
                  />
                )}
              </div>
            )}
          </div>
          <Timeline
            script={script}
            setScript={setScriptD}
            total={T.total}
            shift={T.shift}
            playhead={playheadS}
            seekTo={seekTo}
            selZ={selZ}
            setSelZ={setSelZ}
          />
          {generated && (
            <div className="downloads">
              {(cfg.aspects || ["16x9", "9x16"]).map((a) => (
                <a key={a} className={dirty ? "stale" : ""} href={`/media/${pid}/framed-${a}.mp4?v=${downloadKey}`} download>
                  ↓ {a.replace("x", ":")}
                </a>
              ))}
              {dirty && <span className="stalehint">
                {autoRender ? "auto-rendering…" : "edited since last render — press “Render” to rebuild"}
              </span>}
            </div>
          )}
        </section>

        <aside>
          <nav>
            {["script", "zooms", "elements", "audio", "style"].map((t) => (
              <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
                {t[0].toUpperCase() + t.slice(1)}
              </button>
            ))}
          </nav>
          {tab === "script" && (
            <ScriptPanel pid={pid} script={script} setScript={setScriptD} seekTo={seekBaked} busy={busy} setStatus={setStatus} />
          )}
          {tab === "zooms" && (
            <ZoomPanel pid={pid} script={script} setScript={setScriptD} sel={selZ} setSel={setSelZ}
                       playhead={playheadBaked} setStatus={setStatus} />
          )}
          {tab === "elements" && (
            <ElementsPanel pid={pid} script={script} setScript={setScriptD}
                           drawMode={drawMode} setDrawMode={setDrawMode} playhead={playheadBaked} setStatus={setStatus} />
          )}
          {tab === "audio" && (
            <AudioPanel pid={pid} cfg={cfg} setCfg={setCfgD} script={script} setScript={setScriptD}
                        playheadBaked={playheadBaked} setStatus={setStatus}
                        onProviderChange={setTtsProvider} onVoicesLoaded={setVoiceList} />
          )}
          {tab === "style" && <StylePanel pid={pid} cfg={cfg} setCfg={setCfgD} setStatus={setStatus} />}
        </aside>
      </main>
    </div>
  );
}
