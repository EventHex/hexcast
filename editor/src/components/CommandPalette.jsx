import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";

// ⌘K quick nav: views, New video, and jump to any project by name.
export function CommandPalette({ onClose, go }) {
  const [q, setQ] = useState("");
  const [projects, setProjects] = useState([]);
  const [i, setI] = useState(0);
  const inputRef = useRef(null);

  useEffect(() => { api("/api/projects").then((r) => setProjects(r.projects || [])).catch(() => {}); inputRef.current?.focus(); }, []);

  const items = useMemo(() => {
    const base = [
      { label: "Go to Library", run: () => go("library") },
      { label: "Go to Brands", run: () => go("brands") },
      { label: "Go to Settings", run: () => go("settings") },
      { label: "Help & shortcuts", run: () => go("help") },
      { label: "New video (upload)", run: () => { go("library"); setTimeout(() => document.querySelector('.page-head .btn')?.click(), 60); } },
    ];
    const proj = projects.map((p) => ({ label: `Open · ${p.name}`, run: () => (location.href = `/editor/?project=${p.id}`) }));
    const all = [...base, ...proj];
    if (!q) return all.slice(0, 8);
    const ql = q.toLowerCase();
    return all.filter((x) => x.label.toLowerCase().includes(ql)).slice(0, 8);
  }, [q, projects, go]);

  useEffect(() => { setI(0); }, [q]);
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setI((x) => Math.min(items.length - 1, x + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setI((x) => Math.max(0, x - 1)); }
    else if (e.key === "Enter") { items[i]?.run(); onClose(); }
    else if (e.key === "Escape") onClose();
  };

  return (
    <div className="modal-wrap" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={inputRef} className="cmdk-input" placeholder="Search actions & projects…"
               value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} />
        <div className="cmdk-list">
          {items.map((x, k) => (
            <div key={k} className={`cmdk-item ${k === i ? "on" : ""}`}
                 onMouseEnter={() => setI(k)} onClick={() => { x.run(); onClose(); }}>{x.label}</div>
          ))}
          {items.length === 0 && <div className="cmdk-item dim">No matches</div>}
        </div>
      </div>
    </div>
  );
}
