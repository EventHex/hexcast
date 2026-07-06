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
      <div className="page-head narrow"><h1>Help</h1></div>
      <div className="page-body narrow">
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
          <span className="eyebrow">Screen recording</span>
          <p className="hint">
            Record your screen and mic straight into the Library — no browser extension.
            Hit <b>● Record</b> on the Library page, pick your screen and microphone, and the
            capture drops into a new project ready to edit. First recording asks for macOS
            Screen&nbsp;Recording permission.
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
