import React, { useRef } from "react";

// Tracks: captions · zooms (drag/resize) · sounds (drag), with a time ruler and
// a left label gutter. Stored times in script.json are on the BAKED timeline;
// `shift` maps them onto the live preview timeline (live = baked + shift).
export function Timeline({ script, setScript, total, shift, playhead, seekTo, selZ, setSelZ }) {
  const zref = useRef(null);
  const sref = useRef(null);
  const segs = (script.segments || []).filter((s) => s.rstart != null);
  const zooms = script.zooms || [];
  const sounds = script.sounds || [];
  const T = Math.max(total, 0.001);
  const pct = (t) => `${Math.max(0, Math.min(100, (t / T) * 100))}%`;

  // ruler ticks — pick a step that yields ~6-10 labels
  const step = T > 120 ? 30 : T > 60 ? 15 : T > 30 ? 5 : T > 12 ? 2 : 1;
  const ticks = [];
  for (let t = 0; t <= T + 0.001; t += step) ticks.push(t);
  const fmt = (t) => (T > 90 ? `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}` : `${Math.round(t)}s`);

  const seekFromEvent = (e, el) => {
    const r = el.getBoundingClientRect();
    seekTo(((e.clientX - r.left) / r.width) * T);
  };

  const dragZoom = (e, zi, mode) => {
    e.preventDefault(); e.stopPropagation();
    setSelZ(zi);
    const track = zref.current.getBoundingClientRect();
    const z0 = { ...zooms[zi] };
    const x0 = e.clientX;
    const move = (ev) => {
      const dt = ((ev.clientX - x0) / track.width) * T;
      const zs = zooms.map((z, i) => {
        if (i !== zi) return z;
        if (mode === "move") {
          const len = z0.end - z0.start;
          const ns = Math.max(-shift, Math.min(T - shift - len, z0.start + dt));
          return { ...z, start: +ns.toFixed(2), end: +(ns + len).toFixed(2) };
        }
        if (mode === "l") return { ...z, start: +Math.max(-shift, Math.min(z0.end - 0.3, z0.start + dt)).toFixed(2) };
        return { ...z, end: +Math.min(T - shift, Math.max(z0.start + 0.3, z0.end + dt)).toFixed(2) };
      });
      setScript((s) => ({ ...s, zooms: zs, zoomsEdited: true }));
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  const dragSound = (e, si) => {
    e.preventDefault(); e.stopPropagation();
    const track = sref.current.getBoundingClientRect();
    const s0 = +sounds[si].start;
    const x0 = e.clientX;
    const move = (ev) => {
      const dt = ((ev.clientX - x0) / track.width) * T;
      const ns = Math.max(-shift, Math.min(T - shift - 0.2, s0 + dt));
      setScript((s) => ({ ...s, sounds: (s.sounds || []).map((x, i) => (i === si ? { ...x, start: +ns.toFixed(1) } : x)) }));
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  return (
    <div className="tl2">
      <div className="tl-gutter">
        <div className="tl-ruler-lab" />
        <div className="tl-lab">Captions</div>
        <div className="tl-lab">Zooms</div>
        <div className="tl-lab">Sounds</div>
      </div>
      <div className="tl-lanes">
        <div className="tl-ruler" onPointerDown={(e) => seekFromEvent(e, e.currentTarget)}>
          {ticks.map((t, i) => (
            <div key={i} className="tl-tick" style={{ left: pct(t) }}><span>{fmt(t)}</span></div>
          ))}
          <div className="tl-head tall" style={{ left: pct(playhead) }} />
        </div>
        <div className="tl-track" onPointerDown={(e) => seekFromEvent(e, e.currentTarget)}>
          {shift > 0 && <div className="tl-seg card" style={{ left: 0, width: pct(shift) }}>intro</div>}
          {segs.map((s, i) => (
            <div key={i} className={`tl-seg ${s.type === "scene" ? "scene" : ""}`}
              style={{ left: pct(s.rstart + shift), width: pct(s.rdur || 0.5) }}
              title={s.en || s.title || ""}>{(s.en || s.title || "").slice(0, 34)}</div>
          ))}
          <div className="tl-head" style={{ left: pct(playhead) }} />
        </div>
        <div className="tl-track z" ref={zref} onPointerDown={(e) => seekFromEvent(e, e.currentTarget)}>
          {zooms.map((z, zi) => (
            <div key={zi} className={`tl-zoom ${zi === selZ ? "sel" : ""}`}
              style={{ left: pct(z.start + shift), width: `calc(${pct(z.end - z.start)})` }}
              onPointerDown={(e) => dragZoom(e, zi, "move")}>
              <span className="zh" onPointerDown={(e) => dragZoom(e, zi, "l")} />⌕<span className="zh r" onPointerDown={(e) => dragZoom(e, zi, "r")} />
            </div>
          ))}
          {zooms.length === 0 && <span className="tl-empty">no zooms — add one in the Zooms tab</span>}
          <div className="tl-head" style={{ left: pct(playhead) }} />
        </div>
        <div className="tl-track s" ref={sref} onPointerDown={(e) => seekFromEvent(e, e.currentTarget)}>
          {sounds.map((s, si) => (
            <div key={si} className="tl-sound" title={`${s.sfx} @ ${(+s.start).toFixed(1)}s`}
                 style={{ left: pct(+s.start + shift) }} onPointerDown={(e) => dragSound(e, si)}>♪</div>
          ))}
          {sounds.length === 0 && <span className="tl-empty">no sounds — add them in the Audio tab</span>}
          <div className="tl-head" style={{ left: pct(playhead) }} />
        </div>
      </div>
    </div>
  );
}
