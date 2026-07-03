import React from "react";
import { AbsoluteFill } from "remotion";

// Live intro/outro/scene card — mirrors pipeline/build_revoice.py card():
// gradient | diagonal | radial | accent | minimal, brand-colorable, logo-aware,
// with style-v2 overrides (align, colors, scale, bundled font).

function mixHex(a, b, t) {
  const pa = a.replace("#", "").match(/\w\w/g).map((x) => parseInt(x, 16));
  const pb = b.replace("#", "").match(/\w\w/g).map((x) => parseInt(x, 16));
  return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(",")})`;
}

export function CardPreview({ style = "gradient", top, bot, title, subtitle, logo, height,
                              align, titleColor, subColor, scale = 1, fontFamily }) {
  const H = height || 1080;
  const left = align ? align === "left" : style === "accent" || style === "minimal";
  const dark = style === "minimal";
  const fam = fontFamily || "-apple-system, Helvetica, sans-serif";
  const bg =
    style === "diagonal" ? { background: `linear-gradient(135deg, ${top}, ${bot})` }
    : style === "radial" ? { background: `radial-gradient(75% 90% at 50% 42%, ${top}, ${bot})` }
    : style === "accent" ? { background: bot }
    : style === "minimal" ? { background: "#f6f8fb" }
    : { background: `linear-gradient(${top}, ${bot})` };

  const titleCol = titleColor || (dark ? "#121a28" : "#fff");
  const subCol = subColor || (dark ? top : mixHex(top, "#ffffff", 0.6));

  return (
    <AbsoluteFill style={bg}>
      {style === "accent" && (
        <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: "1.2%", background: top }} />
      )}
      {(style === "accent" || style === "minimal") && (
        <div style={{ position: "absolute", left: "5.5%", top: "40%", width: "6%",
                      height: Math.max(3, H / 180), background: top }} />
      )}
      {logo && (
        <img src={logo}
             style={left
               ? { position: "absolute", left: "5.5%", top: "14%", maxWidth: "22%", maxHeight: "16%", objectFit: "contain" }
               : { position: "absolute", left: "50%", top: "24%", transform: "translateX(-50%)",
                   maxWidth: "22%", maxHeight: "16%", objectFit: "contain" }} />
      )}
      <div style={{ position: "absolute", top: "46%", left: left ? "5.5%" : 0, right: left ? "5%" : 0,
                    textAlign: left ? "left" : "center", color: titleCol,
                    font: `700 ${H * 0.09 * scale}px ${fam}`, lineHeight: 1.05 }}>
        {title || ""}
      </div>
      <div style={{ position: "absolute", top: "60%", left: left ? "5.5%" : 0, right: left ? "5%" : 0,
                    textAlign: left ? "left" : "center", color: subCol,
                    font: `400 ${H * 0.038 * scale}px ${fam}` }}>
        {subtitle || ""}
      </div>
    </AbsoluteFill>
  );
}
