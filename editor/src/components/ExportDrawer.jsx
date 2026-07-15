import React, { useEffect, useState } from "react";
import { api, post } from "../api.js";

// Export = download the finished video and its media files. Rendering is a
// separate step (the ▶ Render button); this drawer only hands you the files a
// render already produced. Captions / transcript / other languages live in
// Publish.
export function ExportDrawer({ pid, cfg, downloadKey, onClose, onRender }) {
  const [out, setOut] = useState(null);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => { api(`/api/projects/${pid}/outputs`).then(setOut).catch(() => setOut({})); }, [pid, downloadKey]);
  const v = (u) => `${u}${u.includes("?") ? "&" : "?"}v=${downloadKey}`;
  const reveal = async () => { try { await post(`/api/projects/${pid}/reveal`); setRevealed(true); } catch {} };
  const dl = (label, href) => <a className="pub-item" href={v(href)} download><span>{label}</span><span className="pub-dl">↓</span></a>;
  const rendered = out?.has_render;

  return (
    <div className="drawer-wrap" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="drawer">
        <div className="drawer-head">
          <h3>Export</h3><span className="grow" /><button className="mini" onClick={onClose}>×</button>
        </div>
        <div className="drawer-body">
          {!rendered ? (
            <section>
              <p className="hint">Nothing rendered yet. Build the video first, then download it here.</p>
              <button className="btn sm wide" onClick={onRender}>▶ Render now</button>
            </section>
          ) : <>
            <section>
              <span className="eyebrow">Video</span>
              {out.aspects.map((a) => dl(`Video ${a.replace("x", ":")}`, `/media/${pid}/framed-${a}.mp4`))}
              {(out.platform === "darwin" || out.platform === "win32") && (
                <button className="pub-item asbtn" onClick={reveal}>
                  <span>{revealed
                    ? (out.platform === "darwin" ? "Revealed in Finder ✓" : "Revealed in Explorer ✓")
                    : (out.platform === "darwin" ? "Reveal in Finder" : "Show in Explorer")}</span><span className="pub-dl">↗</span>
                </button>
              )}
            </section>
            <section>
              <span className="eyebrow">Media</span>
              {dl("Narration audio (.mp3)", `/api/projects/${pid}/audio.mp3`)}
              {dl("Thumbnail (.png)", `/api/projects/${pid}/poster.png`)}
              {dl("Looping GIF (.gif)", `/api/projects/${pid}/preview.gif`)}
            </section>
            <p className="hint">Need captions, a transcript or other-language versions? Those are in <b>Publish</b>.</p>
          </>}
        </div>
      </div>
    </div>
  );
}
