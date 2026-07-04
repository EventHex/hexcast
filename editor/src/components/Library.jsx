import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, post } from "../api.js";

// Library view (inside the shell). Project grid with search/sort/filter, a
// New-video upload, and per-card actions. Language variants group under parent.

const STATUS = {
  exported: ["Exported", "#34d8c9"],
  ready: ["Script ready", "#7fb2ff"],
  recorded: ["Recorded", "#e8b45a"],
  empty: ["Empty", "#8a93a6"],
};
const fmtSize = (b) => (b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(b / 1e6)) + " MB");
const fmtDate = (t) => new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export function Library({ onChange }) {
  const [projects, setProjects] = useState(null);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("recent");
  const [filter, setFilter] = useState("all");
  const [hasSample, setHasSample] = useState(false);
  const fileRef = useRef(null);

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
    return (
      <div key={p.id} className={`pcard ${isVariant ? "variant" : ""}`} onClick={() => open(p.id)}>
        <div className="pcard-thumb">
          <img src={`/api/projects/${p.id}/thumb`} alt="" onError={(e) => { e.target.style.display = "none"; }} />
          <span className="pcard-badge" style={{ background: color }}>{label}</span>
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
        <button className="btn" disabled={busy} onClick={() => fileRef.current.click()}>{busy ? "Uploading…" : "＋ New video"}</button>
      </div>
      <div className="page-body">
        {projects == null && <p className="hint">Loading…</p>}
        {projects?.length === 0 && (
          <div className="empty">
            <p className="hint">No videos yet.</p>
            <p className="hint">Upload a screen recording, or record one with the Remaster recorder extension — it lands here automatically.</p>
            <div className="row gap">
              <button className="btn" disabled={busy} onClick={() => fileRef.current.click()}>＋ New video</button>
              {hasSample && <button className="btn ghost" onClick={trySample}>Try the sample</button>}
            </div>
          </div>
        )}
        <div className="pgrid">
          {groups.map((p) => (
            p.variants.length
              ? <div key={p.id} className="pgroup">
                  {card(p)}
                  <div className="pgroup-variants">{p.variants.map((v) => card(v, true))}</div>
                </div>
              : card(p)
          ))}
        </div>
      </div>
    </div>
  );
}
