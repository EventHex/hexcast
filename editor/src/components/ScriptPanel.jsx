import React, { useRef } from "react";
import { api, post } from "../api.js";

export function ScriptPanel({ pid, script, setScript, seekTo, busy, setStatus }) {
  const audioRef = useRef(null);
  const segs = script.segments || [];

  const upd = (i, patch) => {
    const segments = segs.map((s, k) => (k === i ? { ...s, ...patch } : s));
    setScript({ ...script, segments });
  };
  const del = (i) => setScript({ ...script, segments: segs.filter((_, k) => k !== i) });
  const addLine = (i) => {
    const anchor = segs[i]?.start ?? segs[i]?.rstart ?? 0;
    const segments = [...segs];
    segments.splice(i + 1, 0, { type: "added", en: "New narration line.", anchor });
    setScript({ ...script, segments });
  };
  const addScene = (i) => {
    const segments = [...segs];
    segments.splice(i + 1, 0, { type: "scene", title: "Section title", subtitle: "", dur: 3 });
    setScript({ ...script, segments });
  };
  const playLine = (s) => {
    if (!s.tts_file) return;
    const a = audioRef.current;
    if (!a.paused && a.dataset.src === s.tts_file) { a.pause(); return; }
    a.src = `/media/${pid}/${s.tts_file}`;
    a.dataset.src = s.tts_file;
    a.play().catch(() => {});
  };
  const rewrite = async () => {
    if (busy.current) return;
    busy.current = true;
    setStatus("Rewriting the script with AI…");
    try {
      // rewrite works on the server copy — push current edits first
      await fetch(`/api/projects/${pid}/script`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(script),
      });
      const data = await api(`/api/projects/${pid}/script/rewrite`, { method: "POST" });
      setScript(data);
      setStatus("Script rewritten — review, then Render");
    } catch (e) {
      setStatus("Rewrite failed");
    }
    busy.current = false;
  };
  const revert = async () => {
    try {
      const data = await post(`/api/projects/${pid}/script/revert`);
      setScript(data);
      setStatus("Reverted");
    } catch {
      setStatus("Nothing to revert");
    }
  };

  return (
    <div className="panel-body">
      <div className="row gap">
        <button className="btn sm" onClick={rewrite}>✦ AI rewrite</button>
        <button className="btn sm" onClick={revert}>↩ Revert</button>
      </div>
      <audio ref={audioRef} hidden />
      {segs.map((s, i) =>
        s.type === "scene" ? (
          <div className="seg scene" key={i}>
            <div className="seg-top">
              <span className="tag">Scene</span>
              <span className="grow" />
              <button className="mini" onClick={() => addLine(i)} title="Add line after">＋</button>
              <button className="mini" onClick={() => del(i)} title="Remove">×</button>
            </div>
            <input value={s.title || ""} placeholder="Title" onChange={(e) => upd(i, { title: e.target.value })} />
            <input value={s.subtitle || ""} placeholder="Subtitle" onChange={(e) => upd(i, { subtitle: e.target.value })} />
          </div>
        ) : (
          <div className="seg" key={i} onClick={() => s.rstart != null && seekTo(s.rstart)}>
            <div className="seg-top">
              <span className="tc">{s.rstart != null ? `${s.rstart.toFixed(1)}s` : s.type === "added" ? "added" : "—"}</span>
              <span className="grow" />
              {s.tts_file && (
                <button className="mini" onClick={(e) => { e.stopPropagation(); playLine(s); }} title="Play this line's voice">▶</button>
              )}
              <button className="mini" onClick={(e) => { e.stopPropagation(); addLine(i); }} title="Add line after">＋</button>
              <button className="mini" onClick={(e) => { e.stopPropagation(); addScene(i); }} title="Add scene after">▤</button>
              {(s.type === "added" || s.start != null) && (
                <button className="mini" title={s.type === "added" ? "Remove" : "Cut this line and its footage from the video"}
                        onClick={(e) => { e.stopPropagation(); del(i); }}>×</button>
              )}
            </div>
            <textarea
              rows={2}
              value={s.en || ""}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => upd(i, { en: e.target.value })}
            />
            {s.start != null && s.end != null && (
              // trim the raw footage this line uses; the timeline ripples on export
              <div className="row gap" onClick={(e) => e.stopPropagation()}>
                <label className="lab">Footage in <input className="num" type="number" step="0.1" min="0"
                       value={s.start} onChange={(e) => upd(i, { start: +e.target.value })} /></label>
                <label className="lab">out <input className="num" type="number" step="0.1"
                       value={s.end} onChange={(e) => upd(i, { end: Math.max(+e.target.value, (+s.start || 0) + 0.2) })} /></label>
                <span className="tc">{Math.max(0.2, (s.end - s.start)).toFixed(1)}s</span>
              </div>
            )}
          </div>
        )
      )}
      {!segs.length && <p className="hint">No script yet — upload a recording from the Library first.</p>}
    </div>
  );
}
