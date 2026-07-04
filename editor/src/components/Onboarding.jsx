import React from "react";

// First-run welcome. Branded, sets the two paths (key-free vs BYOK).
export function Onboarding({ onKeys, onSkip }) {
  return (
    <div className="modal-wrap">
      <div className="onboard">
        <div className="onboard-mark">◆</div>
        <h2>Welcome to Remaster</h2>
        <p className="onboard-lead">
          Turn a raw screen recording into a polished, revoiced, brand-framed demo video —
          on your machine, with your own keys.
        </p>
        <div className="onboard-steps">
          <div><b>1 · Record or upload</b><span>Use the recorder extension, or drop in any screen recording.</span></div>
          <div><b>2 · Edit</b><span>AI cleans the script, revoices it, and zooms on what you click.</span></div>
          <div><b>3 · Export</b><span>Brand-framed 16:9 / 9:16 / 1:1 — plus other languages in one click.</span></div>
        </div>
        <p className="hint" style={{ textAlign: "center" }}>
          <b>Runs with zero keys</b> today (local voice, click zooms). Add API keys once —
          shared across every project — for AI voices and script cleanup.
        </p>
        <div className="row gap" style={{ justifyContent: "center" }}>
          <button className="btn ghost" onClick={onSkip}>Start key-free</button>
          <button className="btn" onClick={onKeys}>Set up API keys →</button>
        </div>
      </div>
    </div>
  );
}
