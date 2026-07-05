import React from "react";
import { AbsoluteFill } from "remotion";

// Live intro/outro/scene card — mirrors pipeline/build_revoice.py card():
// background style (gradient|diagonal|radial|accent|minimal) + a layout template
// (centered|left|hero|cta|wordmark) with eyebrow / title / subtitle / CTA / URL,
// laid out as an optically-centered vertical stack.

const TPL = {
  centered: { align: "center", logo: true, tscale: 1.0, sub: true, cta: true, logoBig: false },
  left: { align: "left", logo: true, tscale: 1.0, sub: true, cta: true, logoBig: false },
  hero: { align: "center", logo: false, tscale: 1.45, sub: true, cta: false, logoBig: false },
  cta: { align: "center", logo: true, tscale: 0.92, sub: true, cta: true, logoBig: false },
  wordmark: { align: "center", logo: true, tscale: 1.1, sub: false, cta: false, logoBig: true },
};

function mixHex(a, b, t) {
  const pa = a.replace("#", "").match(/\w\w/g).map((x) => parseInt(x, 16));
  const pb = b.replace("#", "").match(/\w\w/g).map((x) => parseInt(x, 16));
  return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(",")})`;
}

export function CardPreview({ style = "gradient", template = "centered", top, bot, title, subtitle,
                              eyebrow, cta, url, logo, height, align, titleColor, subColor, scale = 1, fontFamily }) {
  const H = height || 1080;
  const tpl = TPL[template] || TPL.centered;
  const al = align || tpl.align;
  const left = al === "left";
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
  const showLogo = tpl.logo && logo;
  const showTitle = title && !(tpl.logoBig && logo);
  const gap = H * 0.024;

  return (
    <AbsoluteFill style={bg}>
      {style === "accent" && (
        <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: "1.2%", background: top }} />
      )}
      <div style={{
        position: "absolute", inset: 0, display: "flex", flexDirection: "column",
        alignItems: left ? "flex-start" : "center", justifyContent: "center",
        textAlign: left ? "left" : "center", gap, padding: left ? "0 6%" : "0 8%",
      }}>
        {showLogo && (
          <img src={logo} alt="" style={{ maxWidth: "50%", maxHeight: `${(tpl.logoBig ? 0.30 : 0.14) * 100}%`, objectFit: "contain" }} />
        )}
        {eyebrow && (
          <div style={{ color: subCol, font: `700 ${H * 0.024}px ${fam}`, letterSpacing: "0.14em", textTransform: "uppercase" }}>{eyebrow}</div>
        )}
        {showTitle && (
          <div style={{ color: titleCol, font: `700 ${H * 0.085 * tpl.tscale * scale}px ${fam}`, lineHeight: 1.05 }}>{title}</div>
        )}
        {subtitle && tpl.sub && (
          <div style={{ color: subCol, font: `400 ${H * 0.036 * scale}px ${fam}` }}>{subtitle}</div>
        )}
        {cta && tpl.cta && (
          <div style={{ background: titleCol, color: dark ? "#fff" : bot, borderRadius: 999,
                        padding: `${H * 0.016}px ${H * 0.032}px`, font: `700 ${H * 0.03}px ${fam}` }}>{cta}</div>
        )}
        {url && (
          <div style={{ color: subCol, font: `400 ${H * 0.026}px ${fam}` }}>{url}</div>
        )}
      </div>
    </AbsoluteFill>
  );
}
