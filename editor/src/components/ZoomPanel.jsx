import React from "react";

export function ZoomPanel({ script, setScript, sel, setSel, playhead }) {
  const zooms = script.zooms || [];
  const z = zooms[sel];

  const upd = (patch) => {
    const zs = zooms.map((x, i) => (i === sel ? { ...x, ...patch } : x));
    setScript({ ...script, zooms: zs, zoomsEdited: true });
  };
  const add = () => {
    const st = Math.max(0, playhead());
    const zs = [...zooms, { start: +st.toFixed(2), end: +(st + 2.5).toFixed(2), cx: 0.5, cy: 0.5, scale: 1.5, speed: 3 }];
    setScript({ ...script, zooms: zs, zoomsEdited: true });
    setSel(zs.length - 1);
  };
  const del = () => {
    if (!z) return;
    setScript({ ...script, zooms: zooms.filter((_, i) => i !== sel), zoomsEdited: true });
    setSel(-1);
  };

  return (
    <div className="panel-body">
      <button className="btn sm wide" onClick={add}>＋ Add zoom at playhead</button>
      {z ? (
        <>
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
        <p className="hint">Select a zoom block on the timeline, or add one at the playhead. Drag blocks to move; drag edges to resize.</p>
      )}
    </div>
  );
}
