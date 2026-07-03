import React, { useCallback, useEffect, useRef, useState } from "react";
import { Player } from "@remotion/player";
import { DemoComposition, timing } from "./composition/DemoComposition.jsx";
import { ScriptPanel } from "./components/ScriptPanel.jsx";
import { ZoomPanel } from "./components/ZoomPanel.jsx";
import { StylePanel } from "./components/StylePanel.jsx";
import { AudioPanel } from "./components/AudioPanel.jsx";
import { ElementsPanel } from "./components/ElementsPanel.jsx";
import { SettingsPanel } from "./components/SettingsPanel.jsx";
import { Library } from "./components/Library.jsx";
import { Timeline } from "./components/Timeline.jsx";
import { api, jput, post, pollJob } from "./api.js";

const FPS = 30;

export default function App() {
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

  // edits after the last export make the downloadable files stale
  const setScriptD = useCallback((v) => { setDirty(true); setScript(v); }, []);
  const setCfgD = useCallback((v) => { setDirty(true); setCfg(v); }, []);

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

  const run = async (endpoint, label) => {
    if (busy.current) return;
    busy.current = true;
    setStatus(label + "…");
    setProg(0);
    setModal({ phase: "running", step: label + "…", minimized: false });
    try {
      await saveAll();
      const r = await post(`/api/projects/${pid}/${endpoint}`);
      if (r.nothing) {
        setStatus("Already up to date");
        setModal({ phase: "done" });
        busy.current = false;
        return;
      }
      setJob(r.job);
      const end = await pollJob(r.job, (s) => {
        const step = s.status === "queued" ? "Queued — waiting for a free render slot…" : s.step || s.status;
        setStatus(step);
        setProg(s.progress || 0);
        setModal((m) => (m ? { ...m, step } : m));
      });
      setJob(null);
      if (end === "done") {
        await load(pid);
        setVideoKey((k) => k + 1);
        setDirty(false);
        setProg(1);
        setStatus("Export complete");
        setModal({ phase: "done" });
      } else {
        const s = await api(`/api/jobs/${r.job}`).catch(() => null);
        const err = (s?.error || "").split("\n").filter(Boolean).pop() || end;
        setStatus(end === "cancelled" ? "Cancelled" : "Failed — " + err);
        setModal(end === "cancelled" ? { phase: "cancelled" } : { phase: "error", error: err });
      }
    } catch (e) {
      setStatus("Request failed");
      setModal({ phase: "error", error: "Request failed — is the server running?" });
    }
    busy.current = false;
  };

  const cancel = () => job && post(`/api/jobs/${job}/cancel`).catch(() => {});

  if (noProject) {
    return <Library />;
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
        <span className="brand">Remaster <em>editor</em></span>
        <span className="pid">{pid}</span>
        <span className="grow" />
        <span className="status">{status}</span>
        {modal?.phase === "running" && modal.minimized && (
          <button className="chip" onClick={() => setModal((m) => ({ ...m, minimized: false }))}>
            <span className="chip-bar"><span style={{ width: `${Math.round(prog * 100)}%` }} /></span>
            Exporting {Math.round(prog * 100)}%
          </button>
        )}
        {job && !modal?.minimized && <button className="btn sm danger" onClick={cancel}>Cancel</button>}
        <button className="btn" disabled={!!job}
                title="Renders the final video files. Runs only the stages your edits changed — framing is instant, re-voicing only when the script or voice changed."
                onClick={() => run("export", "Exporting video")}>⬇ Export video</button>
        <a className="btn sm ghost" href="/editor/">Library</a>
      </header>

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
                  <button className="btn sm danger" onClick={cancel}>Cancel export</button>
                </div>
              </>
            )}
            {modal.phase === "done" && (
              <>
                <div className="modal-ok">✓</div>
                <h3>Export complete</h3>
                <p className="hint">Your videos are ready to download.</p>
                <div className="row gap" style={{ justifyContent: "center" }}>
                  {(cfg.aspects || ["16x9", "9x16"]).map((a) => (
                    <a key={a} className="btn" href={`/media/${pid}/framed-${a}.mp4`} download>
                      ⬇ {a.replace("x", ":")}
                    </a>
                  ))}
                </div>
                <button className="btn sm ghost" onClick={() => setModal(null)}>Close</button>
              </>
            )}
            {modal.phase === "cancelled" && (
              <>
                <h3>Export cancelled</h3>
                <button className="btn sm ghost" onClick={() => setModal(null)}>Close</button>
              </>
            )}
            {modal.phase === "error" && (
              <>
                <div className="modal-err">!</div>
                <h3>Export failed</h3>
                <p className="modal-step">{modal.error}</p>
                <button className="btn sm ghost" onClick={() => setModal(null)}>Close</button>
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
                <a key={a} className={dirty ? "stale" : ""} href={`/media/${pid}/framed-${a}.mp4`} download>
                  ↓ {a.replace("x", ":")}
                </a>
              ))}
              {dirty && <span className="stalehint">edited since last export — press “Export video” to refresh</span>}
            </div>
          )}
        </section>

        <aside>
          <nav>
            {["script", "zooms", "elements", "audio", "style", "settings"].map((t) => (
              <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
                {t === "settings" ? "⚙" : t[0].toUpperCase() + t.slice(1)}
              </button>
            ))}
          </nav>
          {tab === "script" && (
            <ScriptPanel pid={pid} script={script} setScript={setScriptD} seekTo={seekBaked} busy={busy} setStatus={setStatus} />
          )}
          {tab === "zooms" && (
            <ZoomPanel script={script} setScript={setScriptD} sel={selZ} setSel={setSelZ} playhead={playheadBaked} />
          )}
          {tab === "elements" && (
            <ElementsPanel pid={pid} script={script} setScript={setScriptD}
                           drawMode={drawMode} setDrawMode={setDrawMode} playhead={playheadBaked} />
          )}
          {tab === "audio" && (
            <AudioPanel pid={pid} cfg={cfg} setCfg={setCfgD} script={script} setScript={setScriptD}
                        playheadBaked={playheadBaked} setStatus={setStatus} />
          )}
          {tab === "style" && <StylePanel pid={pid} cfg={cfg} setCfg={setCfgD} />}
          {tab === "settings" && <SettingsPanel setStatus={setStatus} />}
        </aside>
      </main>
    </div>
  );
}
