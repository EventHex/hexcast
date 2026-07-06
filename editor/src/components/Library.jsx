import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, post, postJson } from "../api.js";

// Library view (inside the shell). Project grid with search/sort/filter, a
// New-video upload, native screen recording, and per-card actions. Language
// variants group under their parent card.

const STATUS = {
  exported: ["Exported", "var(--ok)"],
  ready: ["Script ready", "var(--accent)"],
  recorded: ["Recorded", "var(--warn)"],
  empty: ["Empty", "var(--faint)"],
};
const fmtSize = (b) => (b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(b / 1e6)) + " MB");
const fmtDate = (t) => new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const fmtClock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export function Library({ onChange }) {
  const [projects, setProjects] = useState(null);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("recent");
  const [filter, setFilter] = useState("all");
  const [hasSample, setHasSample] = useState(false);
  const [expanded, setExpanded] = useState(new Set());
  const fileRef = useRef(null);

  // native screen recording
  const [recModal, setRecModal] = useState(false);   // device-picker open
  const [devices, setDevices] = useState(null);       // {screens,windows,mics} | {error}
  const [target, setTarget] = useState("");           // token of the screen/window to capture
  const [mic, setMic] = useState("");                 // mic token, "" = no mic
  const [rec, setRec] = useState(null);               // {pid} while capturing
  const [elapsed, setElapsed] = useState(0);

  const refresh = () => api("/api/projects").then((r) => { setProjects(r.projects || []); onChange?.(); }).catch(() => setProjects([]));
  useEffect(() => { refresh(); api("/api/health").then((h) => setHasSample(!!h.has_sample)).catch(() => {}); }, []);
  const trySample = async () => { const { id } = await post("/api/sample"); open(id); };

  const open = (id) => { location.href = `/editor/?project=${id}`; };
  const newFromFile = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    setBusy(true);
    try {
      const { id } = await post("/api/projects");
      const fd = new FormData(); fd.append("file", f);
      await fetch(`/api/projects/${id}/upload`, { method: "POST", body: fd });
      open(id);
    } catch { setBusy(false); }
  };

  // --- recording -----------------------------------------------------------
  const openRecorder = async () => {
    setRecModal(true); setDevices(null);
    try {
      const d = await api("/api/record/devices");
      setDevices(d);
      setTarget(d.screens?.[0]?.index ?? d.windows?.[0]?.index ?? "");
      setMic(d.mics?.[0]?.index ?? "");
    } catch (e) { setDevices({ error: e.message || "Capture devices unavailable" }); }
  };
  const startRec = async () => {
    if (!target) return;
    try {
      const { id } = await postJson("/api/record/start", { target, mic: mic || null });
      setRecModal(false); setElapsed(0); setRec({ pid: id });
    } catch (e) { alert(e.message || "Could not start recording"); }
  };
  const stopRec = async () => {
    const active = rec; setRec(null);
    try {
      const { id } = await postJson("/api/record/stop");
      open(id);   // straight into the editor with the fresh capture
    } catch (e) {
      alert(e.message || "Recording failed");
      if (active) refresh();
    }
  };
  useEffect(() => {
    if (!rec) return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [rec]);

  const rename = async (p) => {
    const name = window.prompt("Project name:", p.name); if (name == null) return;
    await fetch(`/api/projects/${p.id}/name`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    refresh();
  };
  const duplicate = async (p) => { await post(`/api/projects/${p.id}/duplicate`); refresh(); };
  const del = async (p) => {
    if (!window.confirm(`Delete "${p.name}" and all its files?`)) return;
    await fetch(`/api/projects/${p.id}`, { method: "DELETE" }); refresh();
  };

  // group language variants ("<parent> (Lang)") under their parent card
  const groups = useMemo(() => {
    if (!projects) return [];
    const byId = Object.fromEntries(projects.map((p) => [p.id, p]));
    const childOf = {};        // parentId -> [variant]
    const tops = [];
    for (const p of projects) {
      const m = p.id.match(/^(.*)-[a-z]+$/);
      if (m && byId[m[1]] && /\([A-Z]/.test(p.name || "")) (childOf[m[1]] ||= []).push(p);
      else tops.push(p);
    }
    let list = tops.map((p) => ({ ...p, variants: childOf[p.id] || [] }));
    if (q) list = list.filter((p) => (p.name || "").toLowerCase().includes(q.toLowerCase()));
    if (filter !== "all") list = list.filter((p) => p.status === filter);
    list.sort(sort === "name" ? (a, b) => (a.name || "").localeCompare(b.name || "")
      : sort === "size" ? (a, b) => b.size - a.size : (a, b) => b.mtime - a.mtime);
    return list;
  }, [projects, q, sort, filter]);

  const card = (p, isVariant) => {
    const [label, color] = STATUS[p.status] || STATUS.empty;
    const nv = p.variants?.length || 0;
    const isOpen = expanded.has(p.id);
    return (
      <div key={p.id} className={`pcard ${isVariant ? "variant" : ""}`} onClick={() => open(p.id)}>
        <div className="pcard-thumb">
          <img src={`/api/projects/${p.id}/thumb`} alt="" onError={(e) => { e.target.style.display = "none"; }} />
          <span className="pcard-badge" style={{ background: color }}>{label}</span>
          {nv > 0 && (
            <button className="pcard-langs" title="Language versions"
                    onClick={(e) => { e.stopPropagation(); setExpanded((s) => { const n = new Set(s); n.has(p.id) ? n.delete(p.id) : n.add(p.id); return n; }); }}>
              🌐 {nv} {isOpen ? "▴" : "▾"}
            </button>
          )}
        </div>
        <div className="pcard-meta">
          <div className="pcard-name">{p.name}</div>
          <div className="hint">{fmtDate(p.mtime)} · {fmtSize(p.size)}</div>
          <div className="row gap" onClick={(e) => e.stopPropagation()} style={{ marginTop: 6 }}>
            <button className="mini" title="Rename" onClick={() => rename(p)}>✎</button>
            <button className="mini" title="Duplicate settings" onClick={() => duplicate(p)}>⧉</button>
            <span className="grow" />
            <button className="mini" title="Delete" onClick={() => del(p)}>🗑</button>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Library</h1>
        <span className="grow" />
        <input className="search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: "auto" }}>
          <option value="all">All</option><option value="exported">Exported</option>
          <option value="ready">Ready</option><option value="recorded">Recorded</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} style={{ width: "auto" }}>
          <option value="recent">Recent</option><option value="name">Name</option><option value="size">Size</option>
        </select>
        <input ref={fileRef} type="file" accept="video/*,.webm,.mp4,.mov,.mkv" hidden onChange={newFromFile} />
        <button className="btn ghost" onClick={openRecorder} title="Record your screen">● Record</button>
        <button className="btn" disabled={busy} onClick={() => fileRef.current.click()}>{busy ? "Uploading…" : "＋ New video"}</button>
      </div>
      <div className="page-body">
        {projects == null && <p className="hint">Loading…</p>}
        {projects?.length === 0 && (
          <div className="empty">
            <p className="hint">No videos yet.</p>
            <p className="hint">Record your screen right here, or upload an existing screen recording.</p>
            <div className="row gap">
              <button className="btn" onClick={openRecorder}>● Record your screen</button>
              <button className="btn ghost" disabled={busy} onClick={() => fileRef.current.click()}>＋ Upload a video</button>
              {hasSample && <button className="btn ghost" onClick={trySample}>Try the sample</button>}
            </div>
          </div>
        )}
        <div className="pgrid">
          {groups.map((p) => [
            card(p),
            p.variants.length > 0 && expanded.has(p.id) && (
              <div key={p.id + "-vars"} className="variant-strip">
                {p.variants.map((vp) => card(vp, true))}
              </div>
            ),
          ])}
        </div>
      </div>

      {/* device picker */}
      {recModal && (
        <div className="modal-wrap" onClick={(e) => e.target === e.currentTarget && setRecModal(false)}>
          <div className="modal">
            <h3>Record your screen</h3>
            {devices == null && <p className="modal-step">Finding screens &amp; windows…</p>}
            {devices?.error && <p className="modal-step" style={{ color: "var(--bad)" }}>{devices.error}</p>}
            {devices && !devices.error && (
              <>
                <label className="field">
                  <span className="hint">What to record</span>
                  <select value={target} onChange={(e) => setTarget(e.target.value)}>
                    {devices.screens?.length > 0 && (
                      <optgroup label="Whole screen">
                        {devices.screens.map((s) => <option key={s.index} value={s.index}>{s.name}</option>)}
                      </optgroup>
                    )}
                    {devices.windows?.length > 0 && (
                      <optgroup label="A window">
                        {devices.windows.map((w) => <option key={w.index} value={w.index}>{w.name}</option>)}
                      </optgroup>
                    )}
                  </select>
                </label>
                <label className="field">
                  <span className="hint">Microphone</span>
                  <select value={mic} onChange={(e) => setMic(e.target.value)}>
                    <option value="">No microphone (silent)</option>
                    {devices.mics.map((m) => <option key={m.index} value={m.index}>{m.name}</option>)}
                  </select>
                </label>
                {devices.permission === false ? (
                  <div className="field" style={{ gap: 8 }}>
                    <p className="hint" style={{ color: "var(--warn)" }}>
                      HexCast needs <b>Screen Recording</b> permission to list your windows. Approve the
                      macOS prompt, or enable HexCast under <b>System Settings → Privacy &amp; Security →
                      Screen&nbsp;Recording</b>, then recheck.
                    </p>
                    <button className="btn sm ghost" onClick={openRecorder}>I’ve enabled it — recheck</button>
                  </div>
                ) : devices.windows?.length === 0 ? (
                  <p className="hint">No separate windows detected — you can still record the whole screen.</p>
                ) : (
                  <p className="hint">Tip: pick a single window to keep everything else out of the shot.</p>
                )}
              </>
            )}
            <div className="row gap" style={{ marginTop: 6 }}>
              <button className="btn ghost" onClick={() => setRecModal(false)}>Cancel</button>
              <span className="grow" />
              <button className="btn" disabled={!devices || devices.error || !target} onClick={startRec}>● Start recording</button>
            </div>
          </div>
        </div>
      )}

      {/* live recording bar */}
      {rec && (
        <div className="rec-bar">
          <span className="rec-dot" />
          <span>Recording · {fmtClock(elapsed)}</span>
          <span className="grow" />
          <button className="btn sm" onClick={stopRec}>■ Stop &amp; edit</button>
        </div>
      )}
    </div>
  );
}
