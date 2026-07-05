import React, { useRef } from "react";

const TYPES = [
  ["box", "▭ Box"],
  ["redact", "■ Redact"],
  ["blur", "◍ Blur"],
  ["text", "T Text"],
  ["image", "🖼 Image"],
];

export function ElementsPanel({ pid, script, setScript, drawMode, setDrawMode, playhead, setStatus }) {
  const fileRef = useRef(null);
  const els = script.elements || [];

  const upd = (i, patch) =>
    setScript({ ...script, elements: els.map((e, k) => (k === i ? { ...e, ...patch } : e)) });
  const del = (i) => {
    setScript({ ...script, elements: els.filter((_, k) => k !== i) });
    setStatus?.("Element removed. Re-render to apply.");
  };

  const pick = (t) => {
    if (t === "image") { fileRef.current.click(); return; }
    setDrawMode(drawMode?.type === t ? null : { type: t });
  };
  const pickedImage = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch(`/api/projects/${pid}/element-image`, { method: "POST", body: fd }).then((x) => x.json());
    setDrawMode({ type: "image", src: r.src });
  };

  return (
    <div className="panel-body">
      <input ref={fileRef} type="file" accept="image/*" hidden onChange={pickedImage} />
      <div className="row gap wrap">
        {TYPES.map(([t, n]) => (
          <button key={t} className={`btn sm ${drawMode?.type === t ? "" : "ghost"}`} onClick={() => pick(t)}>
            {n}
          </button>
        ))}
      </div>
      <p className="hint">
        {drawMode
          ? `Drag on the preview to place the ${drawMode.type}. Esc/re-click to cancel.`
          : "Pick a type, then drag a region on the preview. Redact hides; Blur softens."}
      </p>
      {els.map((el, i) => (
        <div className="seg" key={i}>
          <div className="seg-top">
            <span className="tag">{el.type}</span>
            <span className="grow" />
            <button className="mini" title="Start at playhead" onClick={() => upd(i, { start: +playhead().toFixed(1) })}>⇤</button>
            <button className="mini" title="End at playhead" onClick={() => upd(i, { end: +playhead().toFixed(1) })}>⇥</button>
            <button className="mini" onClick={() => del(i)}>×</button>
          </div>
          {el.type === "text" && (
            <input value={el.text || ""} placeholder="Text…" onChange={(e) => upd(i, { text: e.target.value })} />
          )}
          <div className="row gap">
            <label className="lab">From <input className="num" type="number" step="0.1" value={el.start ?? 0}
                   onChange={(e) => upd(i, { start: +e.target.value })} /></label>
            <label className="lab">To <input className="num" type="number" step="0.1" value={el.end ?? 0}
                   onChange={(e) => upd(i, { end: +e.target.value })} /></label>
            {(el.type === "box" || el.type === "text") && (
              <label className="lab">Color <input type="color" value={el.color || "#FF6B57"}
                     onChange={(e) => upd(i, { color: e.target.value })} /></label>
            )}
          </div>
          {el.type === "text" && (
            <>
              <label className="lab">Size <b>{Math.round((el.size || 0.045) * 1000)}</b></label>
              <input type="range" min="20" max="120" value={Math.round((el.size || 0.045) * 1000)}
                     onChange={(e) => upd(i, { size: +e.target.value / 1000 })} />
            </>
          )}
        </div>
      ))}
      {!els.length && <p className="hint">No elements yet.</p>}
    </div>
  );
}
