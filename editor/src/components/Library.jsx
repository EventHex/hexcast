import React, { useEffect, useRef, useState } from "react";
import { api, post } from "../api.js";
import { SettingsPanel } from "./SettingsPanel.jsx";

// Home screen: every project in the workspace. Upload here (or record with
// the extension) and jump into the editor. Settings (keys/providers) live at
// the workspace level — reachable here, not buried inside a project.

const STATUS = {
  exported: ["Exported", "#34d8c9"],
  ready: ["Script ready", "#7fb2ff"],
  recorded: ["Recorded", "#e8b45a"],
  empty: ["Empty", "#8a93a6"],
};

const fmtSize = (b) => (b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(b / 1e6)) + " MB");
const fmtDate = (t) => new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export function Library() {
  const [projects, setProjects] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [anyKey, setAnyKey] = useState(null);   // null=unknown, else bool
  const [onboard, setOnboard] = useState(false);
  const fileRef = useRef(null);

  const refresh = () => api("/api/projects").then((r) => setProjects(r.projects || [])).catch(() => setProjects([]));
  const loadKeys = () => api("/api/settings")
    .then((v) => setAnyKey(Object.values(v.keys || {}).some((k) => k.set)))
    .catch(() => setAnyKey(false));
  useEffect(() => { refresh(); loadKeys(); }, []);

  // first run: no projects yet and setup never dismissed -> show onboarding
  useEffect(() => {
    if (projects && projects.length === 0 && anyKey != null &&
        !localStorage.getItem("remaster_onboarded")) {
      setOnboard(true);
    }
  }, [projects, anyKey]);

  const dismissOnboard = () => { localStorage.setItem("remaster_onboarded", "1"); setOnboard(false); };
  const open = (id) => { location.href = `/editor/?project=${id}`; };

  const newFromFile = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    setBusy(true);
    try {
      const { id } = await post("/api/projects");
      const fd = new FormData();
      fd.append("file", f);
      await fetch(`/api/projects/${id}/upload`, { method: "POST", body: fd });
      open(id);
    } catch {
      setBusy(false);
    }
  };
  const rename = async (p) => {
    const name = window.prompt("Project name:", p.name);
    if (name == null) return;
    await fetch(`/api/projects/${p.id}/name`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
    });
    refresh();
  };
  const duplicate = async (p) => { await post(`/api/projects/${p.id}/duplicate`); refresh(); };
  const del = async (p) => {
    if (!window.confirm(`Delete "${p.name}" and all its files?`)) return;
    await fetch(`/api/projects/${p.id}`, { method: "DELETE" });
    refresh();
  };

  return (
    <div className="app">
      <header>
        <span className="brand">Remaster</span>
        <span className="grow" />
        <button className="btn sm ghost" onClick={() => setShowSettings(true)}>
          ⚙ Settings{anyKey === false ? " · no keys" : ""}
        </button>
        <input ref={fileRef} type="file" accept="video/*,.webm,.mp4,.mov,.mkv" hidden onChange={newFromFile} />
        <button className="btn" disabled={busy} onClick={() => fileRef.current.click()}>
          {busy ? "Uploading…" : "＋ New video"}
        </button>
      </header>

      <div style={{ padding: "18px 22px", overflowY: "auto" }}>
        <p className="hint" style={{ marginTop: 0 }}>
          Upload a screen recording, or record one with the Remaster recorder extension — it lands here automatically.
        </p>
        {projects == null && <p className="hint">Loading…</p>}
        {projects?.length === 0 && <p className="hint">No videos yet. Hit “＋ New video”.</p>}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 14 }}>
          {(projects || []).map((p) => {
            const [label, color] = STATUS[p.status] || STATUS.empty;
            return (
              <div key={p.id}
                   style={{ border: "1px solid rgba(140,150,170,.25)", borderRadius: 10, overflow: "hidden",
                            cursor: "pointer", background: "rgba(128,140,160,.06)" }}
                   onClick={() => open(p.id)}>
                <div style={{ aspectRatio: "16/10", background: "#0d1320", position: "relative" }}>
                  <img src={`/api/projects/${p.id}/thumb`} alt=""
                       style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                       onError={(e) => { e.target.style.display = "none"; }} />
                  <span style={{ position: "absolute", left: 8, bottom: 8, fontSize: 11, fontWeight: 600,
                                 color: "#0d1320", background: color, borderRadius: 4, padding: "2px 7px" }}>
                    {label}
                  </span>
                </div>
                <div style={{ padding: "9px 11px" }}>
                  <div style={{ fontWeight: 600, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis",
                                whiteSpace: "nowrap" }}>{p.name}</div>
                  <div className="hint" style={{ margin: "3px 0 6px" }}>{fmtDate(p.mtime)} · {fmtSize(p.size)}</div>
                  <div className="row gap" onClick={(e) => e.stopPropagation()}>
                    <button className="mini" title="Rename" onClick={() => rename(p)}>✎</button>
                    <button className="mini" title="Duplicate settings into a new project" onClick={() => duplicate(p)}>⧉</button>
                    <span className="grow" />
                    <button className="mini" title="Delete" onClick={() => del(p)}>🗑</button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {onboard && (
        <div className="modal-wrap">
          <div className="modal" style={{ maxWidth: 460, textAlign: "left" }}>
            <h3>Welcome to Remaster 👋</h3>
            <p className="hint">
              Turn a raw screen recording into a polished, revoiced, brand-framed demo video —
              on your machine, with your own keys.
            </p>
            <p className="hint" style={{ marginTop: 10 }}>
              <b>Works with zero keys</b> right now: local transcription, your own recorded
              voice, click-driven zooms. Add API keys once (they’re shared across every project)
              to unlock AI voices, script cleanup and AI zoom targeting.
            </p>
            <div className="row gap" style={{ justifyContent: "flex-end", marginTop: 14 }}>
              <button className="btn sm ghost" onClick={dismissOnboard}>Start key-free</button>
              <button className="btn sm" onClick={() => { dismissOnboard(); setShowSettings(true); }}>
                Add API keys →
              </button>
            </div>
          </div>
        </div>
      )}

      {showSettings && (
        <div className="modal-wrap" onClick={(e) => e.target === e.currentTarget && setShowSettings(false)}>
          <div className="modal" style={{ maxWidth: 460, maxHeight: "88vh", overflowY: "auto", textAlign: "left" }}>
            <div className="row gap" style={{ alignItems: "center" }}>
              <h3 style={{ margin: 0 }}>Workspace settings</h3>
              <span className="grow" />
              <button className="mini" onClick={() => { setShowSettings(false); loadKeys(); }}>×</button>
            </div>
            <p className="hint">Keys and provider choices apply to every project in this workspace.</p>
            <SettingsPanel setStatus={() => {}} />
          </div>
        </div>
      )}
    </div>
  );
}
