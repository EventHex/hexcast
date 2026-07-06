import React, { useEffect, useState } from "react";
import { api } from "../api.js";

const SHORTCUTS = [
  ["Space", "Play / pause preview"],
  ["⌘ S", "Save now"],
  ["⌘ E", "Export video"],
  ["⌘ Z / ⇧⌘Z", "Undo / redo edit"],
  ["Esc", "Cancel the active draw tool"],
];

export function HelpPage() {
  const [info, setInfo] = useState({});
  useEffect(() => { api("/api/health").then(setInfo).catch(() => {}); }, []);
  return (
    <div className="page">
      <div className="page-head"><h1>Help</h1></div>
      <div className="page-body" style={{ maxWidth: 640 }}>
        <section className="card">
          <span className="eyebrow">Keyboard shortcuts</span>
          <table className="kv">
            <tbody>
              {SHORTCUTS.map(([k, d]) => (
                <tr key={k}><td><kbd>{k}</kbd></td><td>{d}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card">
          <span className="eyebrow">Recorder extension</span>
          <p className="hint">
            {info.extension_seen
              ? "Recorder extension detected — recordings land in your Library automatically."
              : "Install the HexCast recorder extension to capture a tab + your mic straight into the Library. Not detected yet."}
          </p>
        </section>
        <section className="card">
          <span className="eyebrow">Docs &amp; support</span>
          <p className="hint">HexCast is local-first and stores nothing off your machine — zero telemetry.</p>
          <div className="row gap wrap">
            <a className="btn sm ghost" href="https://github.com" target="_blank" rel="noreferrer">README &amp; docs</a>
            <a className="btn sm ghost" href="https://github.com" target="_blank" rel="noreferrer">Report an issue</a>
          </div>
        </section>
        <p className="hint">HexCast {info.version ? `v${info.version}` : ""} · AGPL-3.0</p>
      </div>
    </div>
  );
}
