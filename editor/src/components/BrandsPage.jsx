import React, { useEffect, useRef, useState } from "react";
import { api, post } from "../api.js";

const CARD_STYLES = [["gradient", "Gradient"], ["diagonal", "Diagonal"], ["radial", "Radial glow"],
  ["accent", "Accent bar"], ["minimal", "Minimal light"]];
const FONTS = [["", "System default"], ["inter", "Inter"], ["space-grotesk", "Space Grotesk"],
  ["playfair-display", "Playfair Display"], ["jetbrains-mono", "JetBrains Mono"]];
const FONT_CSS = { inter: "'Inter'", "space-grotesk": "'Space Grotesk'",
  "playfair-display": "'Playfair Display'", "jetbrains-mono": "'JetBrains Mono'" };

// live intro-card preview — mirrors the render's card()/CardPreview
function BrandCard({ c }) {
  const style = c.card_style || "gradient";
  const top = c.card_top || c.brand_top || "#005DBC", bot = c.card_bottom || c.brand_bottom || "#081428";
  const left = c.card_align ? c.card_align === "left" : style === "accent" || style === "minimal";
  const dark = style === "minimal";
  const fam = FONT_CSS[c.font] ? `${FONT_CSS[c.font]}, sans-serif` : "-apple-system, sans-serif";
  const bg = style === "diagonal" ? `linear-gradient(135deg, ${top}, ${bot})`
    : style === "radial" ? `radial-gradient(75% 90% at 50% 42%, ${top}, ${bot})`
    : style === "accent" ? bot : style === "minimal" ? "#f6f8fb" : `linear-gradient(${top}, ${bot})`;
  const logo = c.logo ? `/assets/${String(c.logo).split("/").pop()}` : null;
  const logoInProj = c.logo && String(c.logo).includes("/brands/");
  const logoUrl = logoInProj ? `/api/brands/${c._bid}/logo?v=${c._v || 0}` : logo;
  return (
    <div style={{ position: "relative", aspectRatio: "16/9", borderRadius: 10, overflow: "hidden",
                  background: bg, boxShadow: "0 10px 30px rgba(0,0,0,.4)" }}>
      {style === "accent" && <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: "1.2%", background: top }} />}
      {logoUrl && <img src={logoUrl} alt="" style={left
        ? { position: "absolute", left: "5.5%", top: "14%", maxWidth: "22%", maxHeight: "16%", objectFit: "contain" }
        : { position: "absolute", left: "50%", top: "22%", transform: "translateX(-50%)", maxWidth: "22%", maxHeight: "16%", objectFit: "contain" }} />}
      <div style={{ position: "absolute", top: "46%", left: left ? "5.5%" : 0, right: left ? "5%" : 0,
                    textAlign: left ? "left" : "center", color: c.card_title_color || (dark ? "#121a28" : "#fff"),
                    font: `700 ${(c.card_scale || 1) * 34}px ${fam}` }}>{c.title || "Company"}</div>
      <div style={{ position: "absolute", top: "62%", left: left ? "5.5%" : 0, right: left ? "5%" : 0,
                    textAlign: left ? "left" : "center", color: c.card_sub_color || (dark ? top : "#bcd6ff"),
                    font: `400 ${(c.card_scale || 1) * 15}px ${fam}` }}>{c.subtitle || "Product Demo"}</div>
    </div>
  );
}

export function BrandsPage() {
  const [brands, setBrands] = useState([]);
  const [bid, setBid] = useState(null);
  const [c, setC] = useState(null);       // editing brand config
  const [name, setName] = useState("");
  const [dirty, setDirty] = useState(false);
  const [ver, setVer] = useState(0);
  const logoRef = useRef(null);

  const list = () => api("/api/brands").then((r) => setBrands(r.brands || [])).catch(() => {});
  useEffect(() => { list(); }, []);

  const openBrand = async (id) => {
    const b = await api(`/api/brands/${id}`);
    setBid(id); setName(b.name || id); setC({ ...(b.config || {}), _bid: id, _v: ver }); setDirty(false);
  };
  const create = async () => {
    const { id } = await post("/api/brands", { name: "New brand" });
    await list(); openBrand(id);
  };
  const u = (patch) => { setC({ ...c, ...patch }); setDirty(true); };
  const save = async () => {
    await fetch(`/api/brands/${bid}`, { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config: c }) });
    await list(); setDirty(false);
  };
  const del = async () => {
    if (!window.confirm(`Delete brand "${name}"?`)) return;
    await fetch(`/api/brands/${bid}`, { method: "DELETE" });
    setBid(null); setC(null); list();
  };
  const uploadLogo = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    const r = await fetch(`/api/brands/${bid}/logo`, { method: "POST", body: fd }).then((x) => x.json());
    const v = ver + 1; setVer(v);
    setC((cc) => ({ ...cc, logo: r.logo, _v: v })); setDirty(true);
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Brands</h1>
        <span className="hint">Set colors, logo, cards, voice and music once — reuse on every video.</span>
        <span className="grow" />
        <button className="btn sm" onClick={create}>＋ New brand</button>
      </div>
      <div className="brands-split">
        <div className="brands-list">
          {brands.length === 0 && <p className="hint">No brands yet.</p>}
          {brands.map((b) => (
            <button key={b.id} className={`brand-row ${bid === b.id ? "on" : ""}`} onClick={() => openBrand(b.id)}>
              <span className="swatch" style={{ background: `linear-gradient(${b.brand_top || "#005DBC"}, ${b.brand_bottom || "#081428"})` }} />
              <span className="grow" style={{ textAlign: "left" }}>{b.name}</span>
              {b.has_logo && <span className="hint">logo</span>}
            </button>
          ))}
        </div>

        <div className="brands-edit">
          {!c ? <p className="hint">Select a brand, or create one.</p> : <>
            <BrandCard c={c} />
            <div className="row gap" style={{ marginTop: 12 }}>
              <input value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }} placeholder="Brand name" />
              <span className="grow" />
              {dirty && <button className="btn sm" onClick={save}>Save</button>}
              <button className="btn sm danger" onClick={del}>Delete</button>
            </div>
            <hr className="sep" />
            <div className="grid2">
              <label className="lab col">Card title <input value={c.title || ""} onChange={(e) => u({ title: e.target.value })} /></label>
              <label className="lab col">Card subtitle <input value={c.subtitle || ""} onChange={(e) => u({ subtitle: e.target.value })} /></label>
              <label className="lab col">Outro title <input value={c.outro_title || ""} onChange={(e) => u({ outro_title: e.target.value })} /></label>
              <label className="lab col">Outro subtitle <input value={c.outro_subtitle || ""} onChange={(e) => u({ outro_subtitle: e.target.value })} /></label>
            </div>
            <div className="row gap">
              <label className="lab">Brand top <input type="color" value={c.brand_top || "#005DBC"} onChange={(e) => u({ brand_top: e.target.value })} /></label>
              <label className="lab">bottom <input type="color" value={c.brand_bottom || "#081428"} onChange={(e) => u({ brand_bottom: e.target.value })} /></label>
              <span className="grow" />
              <input ref={logoRef} type="file" accept="image/png,image/*" hidden onChange={uploadLogo} />
              <button className="btn sm ghost" onClick={() => logoRef.current.click()}>Upload logo</button>
            </div>
            <div className="row gap">
              <label className="lab col">Card style
                <select value={c.card_style || "gradient"} onChange={(e) => u({ card_style: e.target.value })}>
                  {CARD_STYLES.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                </select></label>
              <label className="lab col">Font
                <select value={c.font || ""} onChange={(e) => u({ font: e.target.value || null })}>
                  {FONTS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                </select></label>
            </div>
          </>}
        </div>
      </div>
    </div>
  );
}
