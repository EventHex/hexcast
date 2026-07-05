import React, { useState } from "react";
import { post } from "../api.js";

export function ZoomPanel({ pid, script, setScript, sel, setSel, playhead, setStatus }) {
  const zooms = script.zooms || [];
  const z = zooms[sel];
  const [busy, setBusy] = useState(false);

  const upd = (patch) => {
    const zs = zooms.map((x, i) => (i === sel ? { ...x, ...patch } : x));
    setScript({ ...script, zooms: zs, zoomsEdited: true });
  };
  const add = () => {
    const st = Math.max(0, playhead());
    const zs = [...zooms, { start: +st.toFixed(2), end: +(st + 2.5).toFixed(2), cx: 0.5, cy: 0.5, scale: 1.5, speed: 3 }];
    setScript({ ...script, zooms: zs, zoomsEdited: true });
    setSel(zs.length - 1);
    setStatus?.(`Zoom added at ${st.toFixed(1)}s. Re-render to apply.`);
  };
  const del = () => {
    if (!z) return;
    setScript({ ...script, zooms: zooms.filter((_, i) => i !== sel), zoomsEdited: true });
    setSel(-1);
    setStatus?.("Zoom removed. Re-render to apply.");
  };
  // re-run AI zoom targeting on the whole script (replaces the current zooms)
  const regen = async () => {
    if (busy) return;
    setBusy(true);
    setStatus?.("AI is choosing zooms from your recording…");
    try {
      const r = await post(`/api/projects/${pid}/zooms/auto`);
      setScript((s) => ({ ...s, zooms: r.zooms || [], zoomsEdited: true }));
      setSel(-1);
      if (!r.vision) setStatus?.("No AI vision key — add Gemini or Cerebras in ⚙ Settings, then retry.");
      else setStatus?.(`AI set ${r.count} zoom${r.count === 1 ? "" : "s"} across ${r.segments} lines. Re-render to apply.`);
    } catch (e) {
      setStatus?.(`Zoom regeneration failed (${String(e.message || e).slice(0, 60)}).`);
    } finally { setBusy(false); }
  };

  return (
    <div className="panel-body">
      <button className="btn sm wide" disabled={busy} onClick={regen}>
        {busy ? "✨ Regenerating…" : "✨ Regenerate zooms with AI"}
      </button>
      <button className="btn sm wide ghost" onClick={add}>＋ Add zoom at playhead</button>
      <p className="hint">AI picks what to zoom on from your narration + screen. Or add/adjust zooms by hand — manual edits are kept until you regenerate.</p>
      {z ? (
        <>
          <hr className="sep" />
          <div className="posgrid">
            {Array.from({ length: 18 }, (_, k) => {
              const c = k % 6, r = (k / 6) | 0;
              const on = Math.abs(z.cx - (c + 0.5) / 6) < 0.09 && Math.abs(z.cy - (r + 0.5) / 3) < 0.17;
              return (
                <div key={k} className={on ? "on" : ""}
                     onClick={() => upd({ cx: (c + 0.5) / 6, cy: (r + 0.5) / 3 })} />
              );
            })}
          </div>
          <label className="lab">Level <b>{Math.round((z.scale || 1.5) * 100)}%</b></label>
          <input type="range" min="110" max="200" value={Math.round((z.scale || 1.5) * 100)}
                 onChange={(e) => upd({ scale: +e.target.value / 100 })} />
          <label className="lab">Speed <b>{z.speed || 3}</b></label>
          <input type="range" min="1" max="5" value={z.speed || 3}
                 onChange={(e) => upd({ speed: +e.target.value })} />
          <div className="row gap">
            <label className="lab">Start <input className="num" type="number" step="0.1" value={z.start}
                   onChange={(e) => upd({ start: +e.target.value })} /></label>
            <label className="lab">End <input className="num" type="number" step="0.1" value={z.end}
                   onChange={(e) => upd({ end: +e.target.value })} /></label>
          </div>
          <button className="btn sm wide danger" onClick={del}>🗑 Delete zoom</button>
        </>
      ) : (
        <p className="hint">Select a zoom block on the timeline to fine-tune it. Drag blocks to move; drag edges to resize.</p>
      )}
    </div>
  );
}
