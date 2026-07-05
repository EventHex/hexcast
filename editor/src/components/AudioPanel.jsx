import React, { useEffect, useRef, useState } from "react";
import { api, jput, post } from "../api.js";

export const LANGS = [
  { n: "English (India)", t: "English", v: "en-IN-Chirp3-HD-Aoede" },
  { n: "English (US)", t: "English", v: "en-US-Chirp3-HD-Aoede" },
  { n: "Hindi", t: "Hindi", v: "hi-IN-Chirp3-HD-Aoede" },
  { n: "Tamil", t: "Tamil", v: "ta-IN-Chirp3-HD-Aoede" },
  { n: "Telugu", t: "Telugu", v: "te-IN-Chirp3-HD-Aoede" },
  { n: "Malayalam", t: "Malayalam", v: "ml-IN-Chirp3-HD-Aoede" },
  { n: "Kannada", t: "Kannada", v: "kn-IN-Chirp3-HD-Aoede" },
  { n: "Bengali", t: "Bengali", v: "bn-IN-Chirp3-HD-Aoede" },
  { n: "Gujarati", t: "Gujarati", v: "gu-IN-Chirp3-HD-Aoede" },
  { n: "Marathi", t: "Marathi", v: "mr-IN-Chirp3-HD-Aoede" },
  { n: "Arabic", t: "Arabic", v: "ar-XA-Chirp3-HD-Aoede" },
  { n: "Spanish", t: "Spanish", v: "es-US-Chirp3-HD-Aoede" },
  { n: "French", t: "French", v: "fr-FR-Chirp3-HD-Aoede" },
  { n: "German", t: "German", v: "de-DE-Chirp3-HD-Aoede" },
  { n: "Portuguese", t: "Portuguese", v: "pt-BR-Chirp3-HD-Aoede" },
  { n: "Japanese", t: "Japanese", v: "ja-JP-Chirp3-HD-Aoede" },
  { n: "Korean", t: "Korean", v: "ko-KR-Chirp3-HD-Aoede" },
  { n: "Indonesian", t: "Indonesian", v: "id-ID-Chirp3-HD-Aoede" },
];

const EN_VOICES = {
  "Indian English": [
    ["en-IN-Chirp3-HD-Aoede", "Aoede · warm female"],
    ["en-IN-Chirp3-HD-Despina", "Despina · female"],
    ["en-IN-Chirp3-HD-Kore", "Kore · bright female"],
    ["en-IN-Chirp3-HD-Leda", "Leda · soft female"],
    ["en-IN-Chirp3-HD-Charon", "Charon · male"],
    ["en-IN-Chirp3-HD-Algenib", "Algenib · male"],
    ["en-IN-Chirp3-HD-Puck", "Puck · upbeat male"],
    ["en-IN-Chirp3-HD-Fenrir", "Fenrir · deep male"],
  ],
  "US English": [
    ["en-US-Chirp3-HD-Aoede", "Aoede · female"],
    ["en-US-Chirp3-HD-Kore", "Kore · bright female"],
    ["en-US-Chirp3-HD-Zephyr", "Zephyr · female"],
    ["en-US-Chirp3-HD-Leda", "Leda · soft female"],
    ["en-US-Chirp3-HD-Orus", "Orus · cinematic male"],
    ["en-US-Chirp3-HD-Charon", "Charon · male"],
    ["en-US-Chirp3-HD-Puck", "Puck · upbeat male"],
    ["en-US-Chirp3-HD-Enceladus", "Enceladus · calm male"],
  ],
};
const ALL_EN = Object.values(EN_VOICES).flat().map(([v]) => v);

export function AudioPanel({ pid, cfg, setCfg, script, setScript, playheadBaked, setStatus }) {
  const [tracks, setTracks] = useState([]);
  const [sfxLib, setSfxLib] = useState([]);
  const [playing, setPlaying] = useState(null);
  const [tts, setTts] = useState("auto");        // provider selection (settings-level)
  const [keysSet, setKeysSet] = useState({});    // {google:{set}, elevenlabs:{set}, ...}
  const [elVoices, setElVoices] = useState([]);  // live ElevenLabs voice list
  const [snVoices, setSnVoices] = useState([]);  // Soniox built-in + cloned voices
  const [cloneName, setCloneName] = useState("");
  const [cloneBusy, setCloneBusy] = useState("");
  const audioRef = useRef(null);
  const fileRef = useRef(null);
  const cloneRef = useRef(null);
  useEffect(() => {
    api("/api/music").then((r) => setTracks(r.tracks || [])).catch(() => {});
    api("/api/sfx").then((r) => setSfxLib(r.sfx || [])).catch(() => {});
    api("/api/settings").then((v) => { setTts(v.tts.provider || "auto"); setKeysSet(v.keys || {}); }).catch(() => {});
  }, []);
  // provider is a settings-level choice (applies to every project)
  const effTts = tts === "auto" ? (keysSet.google?.set ? "google" : "original") : tts;
  useEffect(() => {
    if (effTts === "elevenlabs" && keysSet.elevenlabs?.set)
      api("/api/voices?provider=elevenlabs").then((r) => setElVoices(r.voices || [])).catch(() => setElVoices([]));
  }, [effTts, keysSet]);
  const loadSoniox = () =>
    api("/api/voices?provider=soniox").then((r) => setSnVoices(r.voices || [])).catch(() => setSnVoices([]));
  useEffect(() => {
    if (effTts === "soniox" && keysSet.soniox?.set) loadSoniox();
  }, [effTts, keysSet]);
  const snIds = snVoices.map((v) => v.id);
  const snGroups = () => {
    const m = {};
    snVoices.forEach((v) => { (m[v.group] = m[v.group] || []).push(v); });
    return Object.entries(m);
  };
  // clone: upload a short clip -> new voice id, then auto-select it
  const doClone = async (e) => {
    const f = e.target.files[0]; e.target.value = "";
    if (!f) return;
    const nm = cloneName.trim() || "My voice";
    setCloneBusy("Cloning your voice… (~15s)");
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await fetch(`/api/voices/clone?name=${encodeURIComponent(nm)}`, { method: "POST", body: fd });
      if (!r.ok) throw new Error(await r.text());
      const v = await r.json();
      setCloneName(""); setCloneBusy("");
      await loadSoniox();
      u({ voice: v.id });
    } catch { setCloneBusy("Clone failed — check the clip and your Soniox key."); }
  };
  const delClone = async (id) => {
    try { await api(`/api/voices/clone/${id}`, { method: "DELETE" }); } catch {}
    if (cfg.voice === id) u({ voice: "Priya" });
    await loadSoniox();
  };
  const setProvider = async (v) => {
    setTts(v);
    try { await jput("/api/settings", { tts: { provider: v } }); } catch {}
  };

  const u = (patch) => setCfg({ ...cfg, ...patch });
  const lang = cfg.lang || "English";
  const langName =
    LANGS.find((x) => x.t === lang && (x.t !== "English" || String(cfg.voice || "").startsWith(x.v.slice(0, 5))))?.n ||
    LANGS.find((x) => x.t === lang)?.n || LANGS[0].n;

  const setLang = (name) => {
    const L = LANGS.find((x) => x.n === name) || LANGS[0];
    u({ lang: L.t, voice: L.t === "English" ? (ALL_EN.includes(cfg.voice) ? cfg.voice : L.v) : L.v });
  };

  // one shared preview <audio>: click toggles play/stop, button shows ⏹ while
  // playing. vol is a linear multiplier so previews honour the gain sliders.
  const preview = (src, vol = 1) => {
    const a = audioRef.current;
    if (playing === src) { a.pause(); setPlaying(null); return; }
    a.src = src;
    a.volume = Math.max(0, Math.min(1, vol));
    a.onended = () => setPlaying(null);
    a.play().then(() => setPlaying(src)).catch(() => setPlaying(null));
  };

  const musicName = cfg.music ? String(cfg.music).split("/").pop().replace(/\.[^.]+$/, "") : "off";
  const musicVal = tracks.includes(musicName) ? musicName : cfg.music ? "upload" : "off";
  // server-side actions change ONE key — merge just that key back so
  // unsaved local edits (titles, colors, …) are never clobbered
  const mergeKey = async (key) => {
    try {
      const fresh = await api(`/api/projects/${pid}/config`);
      setCfg((c) => ({ ...c, [key]: fresh[key] }));
    } catch {}
  };
  const setMusic = async (v) => {
    if (v === "upload") { fileRef.current.click(); return; }
    try {
      await post(`/api/projects/${pid}/music/${v}`);
      await mergeKey("music");
    } catch {}
  };
  const uploadMusic = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    await fetch(`/api/projects/${pid}/music`, { method: "POST", body: fd });
    await mergeKey("music");
  };
  const musicSrc = cfg.music
    ? String(cfg.music).includes("assets/music")
      ? `/assets/music/${String(cfg.music).split("/").pop()}`
      : `/media/${pid}/${String(cfg.music).split("/").pop()}`
    : null;
  const gain = cfg.music_gain ?? -14;
  const pct = Math.round(Math.pow(10, gain / 20) * 100);

  // gain sliders affect a currently-playing preview live
  useEffect(() => {
    if (playing && playing === musicSrc)
      audioRef.current.volume = Math.max(0, Math.min(1, Math.pow(10, gain / 20)));
  }, [gain, playing, musicSrc]);

  const sounds = script.sounds || [];
  const addSound = (name) => {
    if (!name) return;
    const start = Math.max(0, +playheadBaked().toFixed(1));
    setScript({ ...script, sounds: [...sounds, { sfx: name, start, gain: 0 }] });
  };
  const updSound = (i, patch) =>
    setScript({ ...script, sounds: sounds.map((s, k) => (k === i ? { ...s, ...patch } : s)) });
  const delSound = (i) => setScript({ ...script, sounds: sounds.filter((_, k) => k !== i) });

  return (
    <div className="panel-body">
      <audio ref={audioRef} hidden />
      <input ref={fileRef} type="file" accept="audio/*" hidden onChange={uploadMusic} />
      <span className="eyebrow">AI voice</span>
      <label className="lab col">Voice provider
        <select value={tts} onChange={(e) => setProvider(e.target.value)}>
          <option value="auto">Auto ({keysSet.google?.set ? "Google" : "original voice"})</option>
          <option value="google">Google Chirp3-HD</option>
          <option value="elevenlabs">ElevenLabs</option>
          <option value="soniox">Soniox — clone your own voice</option>
          <option value="piper">Piper — local, free</option>
          <option value="original">My original recorded voice</option>
        </select>
      </label>
      <label className="lab col">Language
        <select value={langName} onChange={(e) => setLang(e.target.value)}>
          {LANGS.map((x) => <option key={x.n}>{x.n}</option>)}
        </select>
      </label>
      {effTts === "google" && lang === "English" && (
        <label className="lab col">Narration voice
          <div className="row gap">
            <select value={cfg.voice || ALL_EN[0]} onChange={(e) => u({ voice: e.target.value })}>
              {Object.entries(EN_VOICES).map(([group, vs]) => (
                <optgroup key={group} label={group}>
                  {vs.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                </optgroup>
              ))}
            </select>
            <button className="mini wideh" title="Preview voice"
                    onClick={() => preview(`/assets/voices/${cfg.voice || ALL_EN[0]}.mp3`)}>
              {playing === `/assets/voices/${cfg.voice || ALL_EN[0]}.mp3` ? "⏹" : "▶"}
            </button>
          </div>
        </label>
      )}
      {effTts === "elevenlabs" && (
        keysSet.elevenlabs?.set ? (
          <label className="lab col">Narration voice
            <div className="row gap">
              <select value={cfg.voice || ""} onChange={(e) => u({ voice: e.target.value })}>
                <option value="">Default (Rachel)</option>
                {elVoices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
              </select>
              {(() => {
                const pv = elVoices.find((v) => v.id === cfg.voice)?.preview_url;
                return pv ? (
                  <button className="mini wideh" title="Preview voice" onClick={() => preview(pv)}>
                    {playing === pv ? "⏹" : "▶"}
                  </button>
                ) : null;
              })()}
            </div>
          </label>
        ) : (
          <p className="hint">Add your ElevenLabs key in ⚙ Settings to list your voices.</p>
        )
      )}
      {effTts === "soniox" && (
        keysSet.soniox?.set ? (
          <>
            <label className="lab col">Narration voice
              {snVoices.length ? (
                <select value={snIds.includes(cfg.voice) ? cfg.voice : "Priya"}
                        onChange={(e) => u({ voice: e.target.value })}>
                  {snGroups().map(([g, vs]) => (
                    <optgroup key={g} label={g}>
                      {vs.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                    </optgroup>
                  ))}
                </select>
              ) : <span className="hint">Loading voices…</span>}
            </label>
            <span className="hint">Same voice speaks every language — pick one, set the language above.</span>
            <div className="seg">
              <div className="seg-top"><span className="tag">Clone your own voice</span></div>
              <label className="lab col">Voice name
                <input value={cloneName} placeholder="My voice"
                       onChange={(e) => setCloneName(e.target.value)} /></label>
              <button className="btn sm ghost" style={{ width: "100%", marginTop: 4 }}
                      onClick={() => cloneRef.current.click()}>＋ Upload a 10–20s clip</button>
              <input ref={cloneRef} type="file" accept="audio/*" hidden onChange={doClone} />
              {cloneBusy && <p className="hint">{cloneBusy}</p>}
              <p className="hint">Clean recording, one speaker, up to 20s (≤10 MB). It appears under “My cloned voices”.</p>
              {snVoices.filter((v) => v.cloned).map((v) => (
                <div className="row gap" key={v.id}>
                  <span className="tag">{v.name}</span><span className="grow" />
                  <button className="mini" title="Delete this cloned voice" onClick={() => delClone(v.id)}>×</button>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="hint">Add your Soniox key in ⚙ Settings to pick voices and clone your own.</p>
        )
      )}
      {effTts === "piper" && (
        <label className="lab col">Piper voice model
          <input value={String(cfg.voice || "").includes("Chirp") ? "" : cfg.voice || ""}
                 placeholder="en_US-lessac-medium"
                 onChange={(e) => u({ voice: e.target.value })} />
        </label>
      )}
      {effTts === "original" && (
        <p className="hint">Exports keep your recorded narration as-is — no revoicing.</p>
      )}
      <label className="chk"><input type="checkbox" checked={!!cfg.original_voice}
             onChange={(e) => u({ original_voice: e.target.checked })} /> Use my original recorded voice</label>

      <hr className="sep" />
      <span className="eyebrow">Music</span>
      <label className="lab col">Background bed
        <div className="row gap">
          <select value={musicVal} onChange={(e) => setMusic(e.target.value)}>
            <option value="off">None</option>
            {tracks.map((t) => <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}
            <option value="upload">Upload a track…</option>
          </select>
          <button className="mini wideh" title={playing === musicSrc ? "Stop" : "Preview at the set volume"} disabled={!musicSrc}
                  onClick={() => musicSrc && preview(musicSrc, Math.pow(10, gain / 20))}>
            {playing === musicSrc && musicSrc ? "⏹" : "▶"}
          </button>
        </div>
      </label>
      <label className="lab">Music volume <b>{pct}%</b> <span className="dim">({gain} dB under narration)</span></label>
      <input type="range" min="-30" max="-4" value={gain}
             onChange={(e) => u({ music_gain: +e.target.value })} />

      <hr className="sep" />
      <span className="eyebrow">Sound effects on the timeline</span>
      <label className="lab col">Add at playhead
        <select value="" onChange={(e) => { addSound(e.target.value); e.target.value = ""; }}>
          <option value="">＋ Pick a sound…</option>
          {sfxLib.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
        </select>
      </label>
      {sounds.map((s, i) => (
        <div className="seg" key={i}>
          <div className="seg-top">
            <span className="tag">{String(s.sfx).replace(/_/g, " ")}</span>
            <span className="tc">@{(+s.start).toFixed(1)}s</span>
            <span className="grow" />
            <button className="mini" title="Preview at the set volume"
                    onClick={() => preview(`/assets/sfx/${s.sfx}.wav`, Math.pow(10, (+(s.gain || 0)) / 20))}>
              {playing === `/assets/sfx/${s.sfx}.wav` ? "⏹" : "▶"}
            </button>
            <button className="mini" onClick={() => delSound(i)}>×</button>
          </div>
          <label className="lab">Volume <b>{Math.round(Math.pow(10, (+(s.gain || 0)) / 20) * 100)}%</b></label>
          <input type="range" min="-24" max="6" value={s.gain || 0}
                 onChange={(e) => {
                   updSound(i, { gain: +e.target.value });
                   if (playing === `/assets/sfx/${s.sfx}.wav`)
                     audioRef.current.volume = Math.max(0, Math.min(1, Math.pow(10, +e.target.value / 20)));
                 }} />
        </div>
      ))}
      {!sounds.length && <p className="hint">Placed sounds appear here and on the bottom timeline track — drag them there to move.</p>}

      <hr className="sep" />
      <span className="eyebrow">Automatic effects</span>
      <label className="chk"><input type="checkbox" checked={cfg.sfx_clicks !== false}
             onChange={(e) => u({ sfx_clicks: e.target.checked })} /> Click sounds (from recording)</label>
      <label className="chk"><input type="checkbox" checked={!!cfg.sfx_zoom}
             onChange={(e) => u({ sfx_zoom: e.target.checked })} /> Whoosh on zooms</label>
    </div>
  );
}
