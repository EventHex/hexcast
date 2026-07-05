import React, { useEffect, useState } from "react";
import { api, jput } from "../api.js";

// Workspace settings, restructured into tabs: Providers & Keys / Workspace / About.
// Everything here is workspace-global (one settings.json shared by all projects).

const PROVIDERS = [
  { k: "stt", label: "Transcription", note: "Turn your narration into a timed script.", opts: [
    ["auto", "Auto — Groq if a key is set, else local (recommended)"],
    ["groq", "Groq Whisper — cloud, fast"], ["local", "Local Whisper — free, slower"]] },
  { k: "llm", label: "Script cleanup / rewrite", note: "Remove filler, fix grammar, translate.", opts: [
    ["auto", "Auto — first configured (recommended)"], ["cerebras", "Cerebras"], ["gemini", "Google Gemini"],
    ["openai", "OpenAI-compatible (OpenAI, Ollama, OpenRouter…)"], ["none", "Off — keep my words as spoken"]] },
  { k: "vision", label: "AI zoom targeting", note: "Zoom on the UI your narration mentions.", opts: [
    ["auto", "Auto — first configured (recommended)"], ["cerebras", "Cerebras vision"], ["gemini", "Gemini vision"],
    ["none", "Off — recorded clicks only"]] },
  { k: "tts", label: "Voice (TTS)", note: "The voice that reads your script.", opts: [
    ["auto", "Auto — Google if a key is set, else your recorded voice (recommended)"],
    ["google", "Google Chirp3-HD"], ["elevenlabs", "ElevenLabs"],
    ["soniox", "Soniox — clone your own voice"], ["piper", "Piper — local, free"],
    ["original", "My original recorded voice"]] },
];
const KEYS = [
  ["groq", "Groq", "console.groq.com/keys"], ["cerebras", "Cerebras", "cloud.cerebras.ai"],
  ["gemini", "Google Gemini", "aistudio.google.com/apikey"], ["google", "Google Cloud (TTS)", "console.cloud.google.com"],
  ["elevenlabs", "ElevenLabs", "elevenlabs.io/app/settings/api-keys"], ["soniox", "Soniox (TTS + cloning)", "console.soniox.com"],
  ["openai", "OpenAI-compatible", "platform.openai.com/api-keys"],
];
const LANGS = ["English", "Hindi", "Tamil", "Telugu", "Malayalam", "Kannada", "Bengali", "Gujarati",
  "Marathi", "Arabic", "Spanish", "French", "German", "Portuguese", "Japanese", "Korean", "Indonesian"];

export function SettingsPage() {
  const [tab, setTab] = useState("providers");
  const [view, setView] = useState(null);
  const [sel, setSel] = useState({});
  const [llm, setLlm] = useState({ openai_base_url: "", openai_model: "" });
  const [keys, setKeys] = useState({});
  const [retention, setRetention] = useState(0);
  const [ws, setWs] = useState({});
  const [health, setHealth] = useState({});
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);

  const refresh = async () => {
    const v = await api("/api/settings");
    setView(v);
    setSel({ stt: v.stt.provider, llm: v.llm.provider, vision: v.vision.provider, tts: v.tts.provider });
    setLlm({ openai_base_url: v.llm.openai_base_url || "", openai_model: v.llm.openai_model || "" });
    setRetention(v.retention?.days || 0);
    setWs(v.workspace || {});
    setKeys({}); setDirty(false);
  };
  useEffect(() => { refresh().catch(() => {}); api("/api/health").then(setHealth).catch(() => {}); }, []);
  if (!view) return <div className="page"><div className="page-body"><p className="hint">Loading…</p></div></div>;

  const mark = (fn) => (...a) => { fn(...a); setDirty(true); setSaved(false); };
  const save = async () => {
    await jput("/api/settings", {
      stt: { provider: sel.stt }, vision: { provider: sel.vision }, tts: { provider: sel.tts },
      llm: { provider: sel.llm, ...llm },
      retention: { days: +retention || 0 },
      workspace: ws,
      keys: Object.fromEntries(Object.entries(keys).filter(([, x]) => x !== "")),
    });
    await refresh(); setSaved(true);
  };
  const clearKey = async (name) => { await jput("/api/settings", { keys: { [name]: null } }); await refresh(); };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Settings</h1>
        <span className="grow" />
        {dirty && <button className="btn sm" onClick={save}>Save changes</button>}
        {saved && !dirty && <span className="hint">Saved ✓</span>}
      </div>
      <div className="subtabs">
        {[["providers", "Providers & Keys"], ["workspace", "Workspace"], ["about", "About"]].map(([v, l]) => (
          <button key={v} className={tab === v ? "on" : ""} onClick={() => setTab(v)}>{l}</button>
        ))}
      </div>
      <div className="page-body" style={{ maxWidth: 620 }}>
        {tab === "providers" && <>
          <section className="card">
            <span className="eyebrow">Providers</span>
            {PROVIDERS.map(({ k, label, note, opts }) => (
              <label className="lab col" key={k}>{label}
                <select value={sel[k] || "auto"} onChange={(e) => mark(setSel)({ ...sel, [k]: e.target.value })}>
                  {opts.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                </select>
                <span className="hint">{note}</span>
              </label>
            ))}
            {(sel.llm === "openai" || sel.llm === "auto") && (
              <div className="row gap">
                <label className="lab col">OpenAI-compatible base URL
                  <input value={llm.openai_base_url} placeholder="https://api.openai.com/v1"
                         onChange={(e) => mark(setLlm)({ ...llm, openai_base_url: e.target.value })} /></label>
                <label className="lab col">Model
                  <input value={llm.openai_model} placeholder="gpt-4o-mini"
                         onChange={(e) => mark(setLlm)({ ...llm, openai_model: e.target.value })} /></label>
              </div>
            )}
          </section>
          <section className="card">
            <span className="eyebrow">API keys — stored locally, never leave this machine</span>
            {KEYS.map(([name, label, where]) => {
              const k = view.keys[name] || {};
              return (
                <div className="row gap" key={name}>
                  <label className="lab col" style={{ flex: 1 }}>{label} <span className="hint">· {where}</span>
                    <input type="password" autoComplete="off"
                           placeholder={k.set ? `set ${k.hint} (${k.source})` : "not set"}
                           value={keys[name] || ""} onChange={(e) => mark(setKeys)({ ...keys, [name]: e.target.value })} /></label>
                  {k.set && k.source === "settings" && (
                    <button className="mini" title="Remove this key" onClick={() => clearKey(name)}>×</button>
                  )}
                </div>
              );
            })}
          </section>
        </>}

        {tab === "workspace" && <>
          <section className="card">
            <span className="eyebrow">Company profile</span>
            <label className="lab col">Company name
              <input value={ws.company || ""} placeholder="Acme Inc"
                     onChange={(e) => mark(setWs)({ ...ws, company: e.target.value })} /></label>
            <label className="lab col">Website
              <input value={ws.website || ""} placeholder="acme.com"
                     onChange={(e) => mark(setWs)({ ...ws, website: e.target.value })} /></label>
          </section>
          <section className="card">
            <span className="eyebrow">Defaults for new videos</span>
            <label className="lab col">Narration language
              <select value={ws.default_lang || "English"} onChange={(e) => mark(setWs)({ ...ws, default_lang: e.target.value })}>
                {LANGS.map((l) => <option key={l}>{l}</option>)}
              </select></label>
            <label className="lab col">Export sizes
              <div className="row gap wrap">
                {["16x9", "9x16", "1x1"].map((a) => {
                  const on = (ws.default_aspects || "16x9").split(",").includes(a);
                  return <label className="chk" key={a}>
                    <input type="checkbox" checked={on} onChange={() => {
                      const set = new Set((ws.default_aspects || "16x9").split(",").filter(Boolean));
                      on ? set.delete(a) : set.add(a);
                      mark(setWs)({ ...ws, default_aspects: [...set].join(",") || "16x9" });
                    }} /> {a.replace("x", ":")}</label>;
                })}
              </div></label>
          </section>
          <section className="card">
            <span className="eyebrow">Storage</span>
            <label className="lab col">Auto-clean exported projects after (days, 0 = never)
              <input className="num" type="number" min="0" max="365" value={retention}
                     onChange={(e) => mark(setRetention)(e.target.value)} /></label>
            <span className="hint">Removes raw recordings and render caches of old exported projects; scripts, settings and exported videos stay.</span>
          </section>
        </>}

        {tab === "about" && (
          <section className="card">
            <span className="eyebrow">About Remaster</span>
            <p className="hint">Version <b>{health.version || "—"}</b> · AGPL-3.0</p>
            <p className="hint">A local-first demo-video studio. Bring your own keys; nothing is sent off your machine. Zero telemetry.</p>
            <div className="row gap wrap">
              <a className="btn sm ghost" href="https://github.com" target="_blank" rel="noreferrer">GitHub</a>
              <a className="btn sm ghost" href="https://github.com" target="_blank" rel="noreferrer">Releases</a>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
