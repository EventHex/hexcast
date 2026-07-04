import React, { useEffect, useState } from "react";
import { api, post, pollJob } from "../api.js";

const LANGS = ["Hindi", "Tamil", "Telugu", "Malayalam", "Kannada", "Bengali", "Gujarati", "Marathi",
  "Arabic", "Spanish", "French", "German", "Portuguese", "Japanese", "Korean", "Indonesian"];

export function PublishDrawer({ pid, cfg, downloadKey, onClose, setStatus }) {
  const [out, setOut] = useState(null);
  const [langs, setLangs] = useState([]);
  const [batchBusy, setBatchBusy] = useState(false);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => { api(`/api/projects/${pid}/outputs`).then(setOut).catch(() => setOut({})); }, [pid, downloadKey]);
  const v = (u) => `${u}${u.includes("?") ? "&" : "?"}v=${downloadKey}`;

  const reveal = async () => { try { await post(`/api/projects/${pid}/reveal`); setRevealed(true); } catch {} };
  const runBatch = async () => {
    if (!langs.length || batchBusy) return;
    setBatchBusy(true);
    try {
      const r = await fetch(`/api/projects/${pid}/export-langs`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ langs }),
      }).then((x) => { if (!x.ok) throw new Error(x.status); return x.json(); });
      const end = await pollJob(r.job, (s) => setStatus?.(s.status === "queued" ? "Batch queued…" : `Batch: ${s.step || s.status}`));
      setStatus?.(end === "done" ? `${langs.length} language version(s) in the Library` : `Batch ${end}`);
    } catch { setStatus?.("Batch export failed"); }
    setBatchBusy(false);
  };

  const rendered = out?.has_render;
  const dl = (label, href) => <a className="pub-item" href={v(href)} download><span>{label}</span><span className="pub-dl">↓</span></a>;

  return (
    <div className="drawer-wrap" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="drawer">
        <div className="drawer-head">
          <h3>Publish</h3><span className="grow" /><button className="mini" onClick={onClose}>×</button>
        </div>
        <div className="drawer-body">
          {!rendered && <p className="hint">Export the video first to unlock downloads.</p>}

          {rendered && <>
            <section>
              <span className="eyebrow">Video</span>
              {out.aspects.map((a) => dl(`Video ${a.replace("x", ":")}`, `/media/${pid}/framed-${a}.mp4`))}
              {out.platform === "darwin" && (
                <button className="pub-item asbtn" onClick={reveal}>
                  <span>{revealed ? "Revealed in Finder ✓" : "Reveal in Finder"}</span><span className="pub-dl">↗</span>
                </button>
              )}
            </section>
            <section>
              <span className="eyebrow">Captions &amp; transcript</span>
              {dl("Captions (.srt)", `/api/projects/${pid}/captions.srt`)}
              {dl("Captions (.vtt)", `/api/projects/${pid}/captions.vtt`)}
              {dl("Transcript (.txt)", `/api/projects/${pid}/transcript.txt`)}
            </section>
            <section>
              <span className="eyebrow">More</span>
              {dl("Narration audio (.mp3)", `/api/projects/${pid}/audio.mp3`)}
              {dl("Thumbnail (.png)", `/api/projects/${pid}/poster.png`)}
              {dl("Looping GIF (.gif)", `/api/projects/${pid}/preview.gif`)}
            </section>
            <section>
              <span className="eyebrow">Documentation</span>
              <p className="hint">A step-by-step guide (SOP) built from your narration + screenshots.</p>
              {dl("Step-by-step guide (.html)", `/api/projects/${pid}/doc.html`)}
            </section>
          </>}

          <section>
            <span className="eyebrow">Other languages</span>
            <p className="hint">Each renders as its own Library project — same edits, translated narration, native voice.</p>
            <div className="row gap wrap">
              {LANGS.filter((l) => l !== (cfg.lang || "English")).map((l) => (
                <label className="chk" key={l}>
                  <input type="checkbox" checked={langs.includes(l)}
                         onChange={() => setLangs((x) => x.includes(l) ? x.filter((y) => y !== l) : [...x, l])} /> {l}
                </label>
              ))}
            </div>
            <button className="btn sm wide" disabled={!langs.length || batchBusy} onClick={runBatch}>
              {batchBusy ? "Rendering…" : `Export in ${langs.length || "N"} language(s)`}
            </button>
          </section>
        </div>
      </div>
    </div>
  );
}
