import React, { useEffect, useRef, useState } from "react";
import { api, post } from "../api.js";

const SEL = (label, value, onChange, opts) => (
  <label className="lab col" key={label}>
    {label}
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {opts.map(([v, n]) => (
        <option key={v} value={v}>{n}</option>
      ))}
    </select>
  </label>
);

export function StylePanel({ pid, cfg, setCfg }) {
  const logoRef = useRef(null);
  const bgRef = useRef(null);
  const [brandList, setBrandList] = useState([]);
  const [brandSel, setBrandSel] = useState("");
  const u = (patch) => setCfg({ ...cfg, ...patch });

  const loadBrands = () => api("/api/brands").then((r) => setBrandList(r.brands || [])).catch(() => {});
  useEffect(() => { loadBrands(); }, []);
  useEffect(() => { setBrandSel(cfg.brand_id || ""); }, [cfg.brand_id]);

  const applyBrand = async (bid) => {
    setBrandSel(bid);
    if (!bid) return;
    try {
      const fresh = await api(`/api/projects/${pid}/apply-brand/${bid}`, { method: "POST" });
      setCfg(fresh);   // brand merge happens server-side; take its result verbatim
    } catch {}
  };
  const saveAsBrand = async () => {
    const name = window.prompt("Save current style as a brand — name:");
    if (!name) return;
    try {
      // style keys live in local state; persist before snapshotting
      await fetch(`/api/projects/${pid}/config`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg),
      });
      await fetch(`/api/brands/from-project/${pid}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
      });
      await loadBrands();
    } catch {}
  };

  // server-side uploads change ONE key — merge just that key back so
  // unsaved local edits are never clobbered
  const mergeKey = async (key) => {
    try {
      const fresh = await api(`/api/projects/${pid}/config`);
      setCfg((c) => ({ ...c, [key]: fresh[key] }));
    } catch {}
  };
  const upload = async (ref, endpoint) => {
    const f = ref.current.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    await fetch(`/api/projects/${pid}/${endpoint}`, { method: "POST", body: fd });
    await mergeKey(endpoint === "logo" ? "logo" : "background");
  };
  const aspects = cfg.aspects || ["16x9", "9x16"];
  const toggleAspect = (a) =>
    u({ aspects: aspects.includes(a) ? aspects.filter((x) => x !== a) : [...aspects, a] });

  return (
    <div className="panel-body">
      <span className="eyebrow">Brand kit</span>
      <div className="row gap">
        <select value={brandSel} onChange={(e) => applyBrand(e.target.value)} style={{ flex: 1 }}>
          <option value="">— pick a brand to apply —</option>
          {brandList.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <button className="btn sm ghost" title="Save this project's colors, logo, cards, voice and music as a reusable brand"
                onClick={saveAsBrand}>＋ Save as brand</button>
      </div>
      <p className="hint">A brand applies colors, logo, cards, frame, voice and music in one click — set once, reuse on every video.</p>

      <hr className="sep" />
      <span className="eyebrow">Intro / outro cards</span>
      <label className="lab col">Intro title
        <input value={cfg.title || ""} onChange={(e) => u({ title: e.target.value })} /></label>
      <label className="lab col">Intro subtitle
        <input value={cfg.subtitle || ""} onChange={(e) => u({ subtitle: e.target.value })} /></label>
      <label className="lab col">Outro title
        <input value={cfg.outro_title || ""} onChange={(e) => u({ outro_title: e.target.value })} /></label>
      <label className="lab col">Outro subtitle
        <input value={cfg.outro_subtitle || ""} onChange={(e) => u({ outro_subtitle: e.target.value })} /></label>
      <div className="row gap">
        <label className="lab">Intro s <input className="num" type="number" step="0.5" min="0" max="8"
               value={cfg.intro_dur ?? 2.5} onChange={(e) => u({ intro_dur: +e.target.value })} /></label>
        <label className="lab">Outro s <input className="num" type="number" step="0.5" min="0" max="8"
               value={cfg.outro_dur ?? 2.5} onChange={(e) => u({ outro_dur: +e.target.value })} /></label>
      </div>
      {SEL("Card style", cfg.card_style || "gradient", (v) => u({ card_style: v }), [
        ["gradient", "Gradient"], ["diagonal", "Diagonal"], ["radial", "Radial glow"],
        ["accent", "Accent bar"], ["minimal", "Minimal light"]])}
      <div className="row gap">
        <label className="lab">Card bg <input type="color" value={cfg.card_top || cfg.brand_top || "#005DBC"}
               onChange={(e) => u({ card_top: e.target.value })} /></label>
        <label className="lab"><input type="color" value={cfg.card_bottom || cfg.brand_bottom || "#081428"}
               onChange={(e) => u({ card_bottom: e.target.value })} /></label>
        {(cfg.card_top || cfg.card_bottom) && (
          <button className="btn sm ghost" onClick={() => u({ card_top: null, card_bottom: null })}>
            Match brand
          </button>
        )}
      </div>

      <hr className="sep" />
      <span className="eyebrow">Frame</span>
      <div className="row gap">
        {SEL("Theme", cfg.frame_theme || "float", (v) => u({ frame_theme: v }), [
          ["float", "Floating card"], ["full", "Full screen"], ["browser", "Browser"], ["split", "Split panel"]])}
        {SEL("Background", cfg.bg_style || "gradient", (v) => u({ bg_style: v }), [
          ["gradient", "Gradient"], ["mesh", "Mesh glow"], ["noise", "Grainy"]])}
      </div>
      {(cfg.frame_theme || "float") === "browser" && (
        <label className="lab col">URL bar text
          <input value={cfg.browser_url || ""} placeholder="app.yoursite.com"
                 onChange={(e) => u({ browser_url: e.target.value || null })} /></label>
      )}
      <div className="row gap">
        {SEL("Shadow", cfg.shadow || "medium", (v) => u({ shadow: v }), [
          ["none", "None"], ["light", "Light"], ["medium", "Medium"], ["heavy", "Heavy"]])}
        {SEL("Transition", cfg.transition || "none", (v) => u({ transition: v }), [
          ["none", "Cut"], ["dissolve", "Dissolve"], ["fade", "Fade"], ["slide", "Slide"]])}
      </div>
      <label className="lab">Corner radius <b>{cfg.radius ?? 24}</b></label>
      <input type="range" min="0" max="80" value={cfg.radius ?? 24} onChange={(e) => u({ radius: +e.target.value })} />
      <label className="lab">Padding <b>{cfg.padding ?? 16}</b></label>
      <input type="range" min="2" max="22" value={cfg.padding ?? 16} onChange={(e) => u({ padding: +e.target.value })} />
      <label className="chk"><input type="checkbox" checked={cfg.vertical_stack !== false}
             onChange={(e) => u({ vertical_stack: e.target.checked })} /> 9:16 stacked layout</label>

      <hr className="sep" />
      <span className="eyebrow">Brand</span>
      <div className="row gap">
        <label className="lab">Top <input type="color" value={cfg.brand_top || "#005DBC"}
               onChange={(e) => u({ brand_top: e.target.value })} /></label>
        <label className="lab">Bottom <input type="color" value={cfg.brand_bottom || "#081428"}
               onChange={(e) => u({ brand_bottom: e.target.value })} /></label>
        <span className="grow" />
        {SEL("Logo corner", cfg.logo_corner || "tr", (v) => u({ logo_corner: v }), [
          ["tl", "Top left"], ["tr", "Top right"], ["bl", "Bottom left"], ["br", "Bottom right"]])}
      </div>
      <div className="row gap">
        <input ref={logoRef} type="file" accept="image/png" hidden onChange={() => upload(logoRef, "logo")} />
        <input ref={bgRef} type="file" accept="image/*" hidden onChange={() => upload(bgRef, "background")} />
        <button className="btn sm ghost" onClick={() => logoRef.current.click()}>Upload logo</button>
        <button className="btn sm ghost" onClick={() => bgRef.current.click()}>Upload wallpaper</button>
        {cfg.background && (
          <button className="btn sm ghost" onClick={async () => {
            await fetch(`/api/projects/${pid}/background/off`, { method: "POST" });
            await mergeKey("background");
          }}>Use gradient</button>
        )}
      </div>

      <hr className="sep" />
      <span className="eyebrow">Render</span>
      <label className="chk"><input type="checkbox" checked={cfg.zoom !== false}
             onChange={(e) => u({ zoom: e.target.checked })} /> Auto-zoom on generate</label>
      <label className="chk"><input type="checkbox" checked={cfg.captions !== false}
             onChange={(e) => u({ captions: e.target.checked })} /> Burn captions</label>
      <div className="row gap">
        {["16x9", "9x16", "1x1"].map((a) => (
          <label className="chk" key={a}>
            <input type="checkbox" checked={aspects.includes(a)} onChange={() => toggleAspect(a)} /> {a.replace("x", ":")}
          </label>
        ))}
      </div>
    </div>
  );
}
