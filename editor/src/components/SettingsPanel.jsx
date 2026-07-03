import React, { useEffect, useState } from "react";
import { api, jput } from "../api.js";

// BYOK settings. The server only ever sends {set, hint, source} per key —
// raw keys never reach the browser. Empty input = leave unchanged.

const CAPS = [
  { k: "stt", label: "Transcription", opts: [
    ["auto", "Auto (Groq if key, else local)"], ["groq", "Groq Whisper — cloud, fast"],
    ["local", "Local Whisper — free, slower"]] },
  { k: "llm", label: "Script cleanup / rewrite", opts: [
    ["auto", "Auto (first configured)"], ["cerebras", "Cerebras"], ["gemini", "Google Gemini"],
    ["openai", "OpenAI-compatible (OpenAI, Ollama…)"], ["none", "Off — keep my words as spoken"]] },
  { k: "vision", label: "AI zoom targeting", opts: [
    ["auto", "Auto (first configured)"], ["cerebras", "Cerebras vision"], ["gemini", "Gemini vision"],
    ["none", "Off — recorded clicks only"]] },
  { k: "tts", label: "Voice (TTS)", opts: [
    ["auto", "Auto (Google if key, else original voice)"], ["google", "Google Chirp3-HD"],
    ["elevenlabs", "ElevenLabs"], ["piper", "Piper — local, free"],
    ["original", "My original recorded voice"]] },
];

const KEYS = [
  ["groq", "Groq"], ["cerebras", "Cerebras"], ["gemini", "Google Gemini"],
  ["google", "Google Cloud (TTS)"], ["elevenlabs", "ElevenLabs"], ["openai", "OpenAI-compatible"],
];

export function SettingsPanel({ setStatus }) {
  const [view, setView] = useState(null);   // masked server snapshot
  const [sel, setSel] = useState({});       // {stt: "auto", ...}
  const [llm, setLlm] = useState({ openai_base_url: "", openai_model: "" });
  const [keys, setKeys] = useState({});     // local unsaved key inputs
  const [retention, setRetention] = useState(0);
  const [dirty, setDirty] = useState(false);

  const refresh = async () => {
    const v = await api("/api/settings");
    setView(v);
    setSel({ stt: v.stt.provider, llm: v.llm.provider, vision: v.vision.provider, tts: v.tts.provider });
    setLlm({ openai_base_url: v.llm.openai_base_url || "", openai_model: v.llm.openai_model || "" });
    setRetention(v.retention?.days || 0);
    setKeys({});
    setDirty(false);
  };
  useEffect(() => { refresh().catch(() => {}); }, []);

  if (!view) return <div className="panel-body"><p className="hint">Loading settings…</p></div>;

  const anyKey = Object.values(view.keys).some((k) => k.set);
  const save = async () => {
    const body = {
      stt: { provider: sel.stt }, vision: { provider: sel.vision }, tts: { provider: sel.tts },
      llm: { provider: sel.llm, ...llm },
      retention: { days: +retention || 0 },
      keys: Object.fromEntries(Object.entries(keys).filter(([, v]) => v !== "")),
    };
    await jput("/api/settings", body);
    await refresh();
    setStatus?.("Settings saved ✓");
  };
  const clearKey = async (name) => {
    await jput("/api/settings", { keys: { [name]: null } });
    await refresh();
  };

  return (
    <div className="panel-body">
      {!anyKey && (
        <p className="hint">
          Running <b>key-free</b>: local transcription, your original voice, click-driven
          zooms, no AI rewrite. Add any key below to unlock more.
        </p>
      )}
      <span className="eyebrow">Providers</span>
      {CAPS.map(({ k, label, opts }) => (
        <label className="lab col" key={k}>{label}
          <select value={sel[k] || "auto"} onChange={(e) => { setSel({ ...sel, [k]: e.target.value }); setDirty(true); }}>
            {opts.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
          </select>
        </label>
      ))}
      {(sel.llm === "openai" || sel.llm === "auto") && (
        <div className="row gap">
          <label className="lab col">OpenAI-compatible base URL
            <input value={llm.openai_base_url} placeholder="https://api.openai.com/v1"
                   onChange={(e) => { setLlm({ ...llm, openai_base_url: e.target.value }); setDirty(true); }} /></label>
          <label className="lab col">Model
            <input value={llm.openai_model} placeholder="gpt-4o-mini"
                   onChange={(e) => { setLlm({ ...llm, openai_model: e.target.value }); setDirty(true); }} /></label>
        </div>
      )}

      <hr className="sep" />
      <span className="eyebrow">Storage</span>
      <label className="lab col">Auto-clean exported projects after (days, 0 = never)
        <input className="num" type="number" min="0" max="365" value={retention}
               onChange={(e) => { setRetention(e.target.value); setDirty(true); }} />
      </label>
      <p className="hint">Cleaning removes the raw recording and render caches of old exported projects; scripts, settings and exported videos stay.</p>

      <hr className="sep" />
      <span className="eyebrow">API keys (stored locally, never leave this machine)</span>
      {KEYS.map(([name, label]) => {
        const k = view.keys[name] || {};
        return (
          <div className="row gap" key={name}>
            <label className="lab col" style={{ flex: 1 }}>{label}
              <input type="password" autoComplete="off"
                     placeholder={k.set ? `set ${k.hint} (${k.source})` : "not set"}
                     value={keys[name] || ""}
                     onChange={(e) => { setKeys({ ...keys, [name]: e.target.value }); setDirty(true); }} /></label>
            {k.set && k.source === "settings" && (
              <button className="mini" title="Remove this key" onClick={() => clearKey(name)}>×</button>
            )}
          </div>
        );
      })}
      <button className={`btn sm wide ${dirty ? "" : "ghost"}`} onClick={save}>Save settings</button>
    </div>
  );
}
