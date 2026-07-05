import React, { useEffect, useRef, useState } from "react";
import { api, post } from "../api.js";

// one-click coherent looks: frame + background + cards + captions + font
const PRESETS = [
  ["Clean light", { frame_theme: "float", bg_style: "gradient", card_style: "minimal", shadow: "light",
                    radius: 14, font: "inter", cap_bg: "box", cap_bg_opacity: 0.45, cap_color: "#FFFFFF" }],
  ["Bold dark", { frame_theme: "float", bg_style: "mesh", card_style: "radial", shadow: "heavy",
                  radius: 24, font: "space-grotesk", cap_bg: "box", cap_bg_opacity: 0.7, cap_color: "#FFFFFF" }],
  ["Editorial", { frame_theme: "split", bg_style: "gradient", card_style: "accent", shadow: "medium",
                  radius: 10, font: "playfair-display", cap_bg: "none", cap_color: "#FFFFFF", cap_scale: 1.1 }],
  ["Dev tool", { frame_theme: "browser", bg_style: "noise", card_style: "gradient", shadow: "medium",
                 radius: 12, font: "jetbrains-mono", cap_bg: "box", cap_bg_opacity: 0.6 }],
];

const FONTS = [
  ["", "System default"], ["inter", "Inter"], ["space-grotesk", "Space Grotesk"],
  ["playfair-display", "Playfair Display"], ["jetbrains-mono", "JetBrains Mono"],
];

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

export function StylePanel({ pid, cfg, setCfg, setStatus }) {
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
      setStatus?.("Brand applied — colors, logo, cards, voice & music. Re-render to apply.");
    } catch { setStatus?.("Couldn't apply that brand."); }
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

  // one intro/outro card editor: template (none = skip), eyebrow, title,
  // subtitle, CTA + URL, and seconds.
  const cardEditor = (label, prefix, titleKey, subKey, durKey) => {
    const tpl = cfg[`${prefix}_template`] || (prefix === "outro" ? "cta" : "centered");
    const on = tpl !== "none";
    return (
      <>
        <div className="row gap">
          <span className="eyebrow" style={{ flex: 1 }}>{label} card</span>
          <label className="chk"><input type="checkbox" checked={on}
                 onChange={(e) => u({ [`${prefix}_template`]: e.target.checked ? (prefix === "outro" ? "cta" : "centered") : "none" })} /> Show</label>
        </div>
        {on && <>
          {SEL("Layout", tpl, (v) => u({ [`${prefix}_template`]: v }), [
            ["centered", "Centered"], ["left", "Left aligned"], ["hero", "Hero (big title)"],
            ["cta", "Call to action"], ["wordmark", "Wordmark (logo)"]])}
          <label className="lab col">Eyebrow <span className="dim">(small line above)</span>
            <input value={cfg[`${prefix}_eyebrow`] || ""} placeholder="e.g. NEW"
                   onChange={(e) => u({ [`${prefix}_eyebrow`]: e.target.value })} /></label>
          <label className="lab col">Title
            <input value={cfg[titleKey] || ""} onChange={(e) => u({ [titleKey]: e.target.value })} /></label>
          <label className="lab col">Subtitle
            <input value={cfg[subKey] || ""} onChange={(e) => u({ [subKey]: e.target.value })} /></label>
          {(tpl === "cta" || tpl === "centered" || tpl === "left") && (
            <div className="row gap">
              <label className="lab col">Button
                <input value={cfg[`${prefix}_cta`] || ""} placeholder="Try it free"
                       onChange={(e) => u({ [`${prefix}_cta`]: e.target.value })} /></label>
              <label className="lab col">URL / handle
                <input value={cfg[`${prefix}_url`] || ""} placeholder="yoursite.com"
                       onChange={(e) => u({ [`${prefix}_url`]: e.target.value })} /></label>
            </div>
          )}
          <label className="lab">Seconds <input className="num" type="number" step="0.5" min="0.5" max="10"
                 value={cfg[durKey] ?? 2.5} onChange={(e) => u({ [durKey]: +e.target.value })} /></label>
        </>}
      </>
    );
  };

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
      <div className="row gap wrap">
        {PRESETS.map(([name, patch]) => (
          <button key={name} className="btn sm ghost" title="Apply this preset look"
                  onClick={() => { u(patch); setStatus?.(`Applied “${name}” look.`); }}>
            {name}
          </button>
        ))}
      </div>

      <hr className="sep" />
      {cardEditor("Intro", "intro", "title", "subtitle", "intro_dur")}
      <hr className="sep" />
      {cardEditor("Outro", "outro", "outro_title", "outro_subtitle", "outro_dur")}

      <hr className="sep" />
      <span className="eyebrow">Card background</span>
      {SEL("Style", cfg.card_style || "gradient", (v) => u({ card_style: v }), [
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
      <span className="eyebrow">Typography</span>
      <div className="row gap">
        {SEL("Font", cfg.font || "", (v) => u({ font: v || null }), FONTS)}
        {SEL("Card text align", cfg.card_align || "", (v) => u({ card_align: v || null }), [
          ["", "Auto"], ["left", "Left"], ["center", "Center"]])}
      </div>
      <label className="lab">Card text size <b>{Math.round((cfg.card_scale ?? 1) * 100)}%</b></label>
      <input type="range" min="70" max="140" value={Math.round((cfg.card_scale ?? 1) * 100)}
             onChange={(e) => u({ card_scale: +e.target.value / 100 })} />
      <div className="row gap">
        <label className="lab">Title <input type="color" value={cfg.card_title_color || "#ffffff"}
               onChange={(e) => u({ card_title_color: e.target.value })} /></label>
        <label className="lab">Subtitle <input type="color" value={cfg.card_sub_color || "#96c8ff"}
               onChange={(e) => u({ card_sub_color: e.target.value })} /></label>
        {(cfg.card_title_color || cfg.card_sub_color) && (
          <button className="btn sm ghost" onClick={() => u({ card_title_color: null, card_sub_color: null })}>
            Auto colors
          </button>
        )}
      </div>

      <hr className="sep" />
      <span className="eyebrow">Captions</span>
      <div className="row gap">
        {SEL("Position", cfg.cap_pos || "bottom", (v) => u({ cap_pos: v }), [
          ["bottom", "Bottom"], ["top", "Top"]])}
        {SEL("Background", cfg.cap_bg || "box", (v) => u({ cap_bg: v }), [
          ["box", "Dark box"], ["none", "None"]])}
        <label className="lab">Text <input type="color" value={cfg.cap_color || "#ffffff"}
               onChange={(e) => u({ cap_color: e.target.value })} /></label>
      </div>
      <label className="lab">Caption size <b>{Math.round((cfg.cap_scale ?? 1) * 100)}%</b></label>
      <input type="range" min="70" max="160" value={Math.round((cfg.cap_scale ?? 1) * 100)}
             onChange={(e) => u({ cap_scale: +e.target.value / 100 })} />
      {(cfg.cap_bg || "box") === "box" && (
        <>
          <label className="lab">Box opacity <b>{Math.round((cfg.cap_bg_opacity ?? 0.58) * 100)}%</b></label>
          <input type="range" min="10" max="100" value={Math.round((cfg.cap_bg_opacity ?? 0.58) * 100)}
                 onChange={(e) => u({ cap_bg_opacity: +e.target.value / 100 })} />
        </>
      )}

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
             onChange={(e) => { u({ captions: e.target.checked });
               setStatus?.(e.target.checked ? "Captions on. Re-render to apply." : "Captions off. Re-render to apply."); }} /> Burn captions</label>
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
