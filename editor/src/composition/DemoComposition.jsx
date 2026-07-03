import React from "react";
import { AbsoluteFill, Audio, Sequence, Video, useCurrentFrame, useVideoConfig } from "remotion";
import { CardPreview } from "./CardPreview.jsx";

// Truthful preview of the whole pipeline:
//  - live intro/outro cards (build_revoice card(): style + card colors + durations)
//  - fx pass (timeline_fx.py): zoom, captions, elements — inside the window
//  - frame pass (polish_export.py): theme plate, background, shadow, logo
//  - audio: music bed at render gain + user-placed SFX
// Baked cards inside base.mp4 are trimmed out and replaced by the live ones, so
// title/subtitle/seconds/card-style/card-color edits appear instantly.

export function timing(script, cfg, srcDur) {
  const segs = (script.segments || []).filter((s) => s.rstart != null);
  const introDur = Math.max(0, +(cfg.intro_dur ?? 2.5) || 0);
  const outroDur = Math.max(0, +(cfg.outro_dur ?? 2.5) || 0);
  const bakedIntro = segs.length ? segs[0].rstart : 0;
  // duration-less sources (webm without a header probes as 0): fall back to
  // the script's own raw end times so the preview isn't cut to nothing
  const rawEnd = Math.max(0, ...(script.segments || []).map((s) => +s.end || 0));
  const safeDur = Math.max(srcDur || 0, rawEnd) || 1;
  const lastEnd = segs.length ? Math.max(...segs.map((s) => s.rstart + (s.rdur || 0))) : safeDur;
  const contentDur = Math.max(0.1, lastEnd - bakedIntro);
  return {
    introDur, outroDur, contentDur, bakedIntro, lastEnd,
    shift: introDur - bakedIntro,
    total: introDur + contentDur + outroDur,
  };
}

function zoomAt(t, zooms) {
  for (const z of zooms || []) {
    if (t >= z.start && t <= z.end) {
      const speed = z.speed || 3;
      const ramp = Math.max(0.15, 1.05 - speed * 0.18);
      const p = Math.max(0, Math.min(1, Math.min((t - z.start) / ramp, (z.end - t) / ramp)));
      return { s: 1 + ((z.scale || 1.5) - 1) * p, cx: z.cx ?? 0.5, cy: z.cy ?? 0.5 };
    }
  }
  return null;
}

function captionAt(t, segments) {
  for (const sg of segments || []) {
    if (sg.rstart == null) continue;
    if (sg.en && t >= sg.rstart && t < sg.rstart + (sg.rdur || 0)) return sg.en;
  }
  return null;
}

const ELT_STYLE = {
  box: { border: "3px solid #34d8c9" },
  redact: { background: "#0a0f1c" },
  blur: { backdropFilter: "blur(14px)", WebkitBackdropFilter: "blur(14px)" },
  text: {},
  image: {},
};

const SHADOWS = {
  none: "none",
  light: "0 14px 20px rgba(0,0,0,0.27)",
  medium: "0 22px 34px rgba(0,0,0,0.5)",
  heavy: "0 32px 50px rgba(0,0,0,0.72)",
};

function mix(a, b, t) {
  const pa = a.match(/\w\w/g).map((x) => parseInt(x, 16));
  const pb = b.match(/\w\w/g).map((x) => parseInt(x, 16));
  return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(",")})`;
}

function bgStyle(cfg, pid) {
  const top = (cfg.brand_top || "#005DBC").slice(1);
  const bot = (cfg.brand_bottom || "#081428").slice(1);
  if (cfg.background) {
    const base = String(cfg.background).split("/").pop();
    return { background: `url(/media/${pid}/${base}) center / cover no-repeat, #101724` };
  }
  if (cfg.bg_style === "mesh")
    return {
      background:
        `radial-gradient(55% 75% at 16% 18%, #${top}cc, transparent),` +
        `radial-gradient(45% 60% at 88% 10%, ${mix(top, "ffffff", 0.35)} 0%, transparent 70%),` +
        `radial-gradient(65% 80% at 55% 95%, ${mix(top, bot, 0.4)} 0%, transparent 70%),` +
        mix(bot, "000000", 0.25),
    };
  return { background: `linear-gradient(#${top}, #${bot})` };
}

function windowRect(cfg, theme, CW, CH, ar) {
  const pad = cfg.padding != null && cfg.padding !== "" ? Math.max(2, Math.min(24, +cfg.padding)) / 100 : 0.16;
  if (theme === "split") {
    const marg = CW * 0.045;
    let WW = CW * 0.6, WH = WW / ar;
    if (WH > CH - 2 * marg) { WH = CH - 2 * marg; WW = WH * ar; }
    return { x: CW - WW - marg, y: (CH - WH) / 2, w: WW, h: WH };
  }
  const availW = CW * (1 - pad), availH = CH * (1 - pad);
  let WW, WH;
  if (availW / ar <= availH) { WW = availW; WH = WW / ar; }
  else { WH = availH; WW = WH * ar; }
  let y = (CH - WH) / 2;
  if (theme === "browser") y += Math.max(34, WH * 0.055) / 2;
  return { x: (CW - WW) / 2, y, w: WW, h: WH };
}

function logoSrc(cfg, pid) {
  if (!cfg.logo) return null;
  const base = String(cfg.logo).split("/").pop();
  return String(cfg.logo).includes("webstudio/assets") ? `/assets/${base}` : `/media/${pid}/${base}`;
}

export const DemoComposition = ({ videoSrc, script, cfg, musicSrc, pid, srcAr, srcDur }) => {
  const frame = useCurrentFrame();
  const { fps, width: CW, height: CH } = useVideoConfig();
  const T = timing(script, cfg, srcDur);
  const t = frame / fps;
  const tB = t - T.shift; // live time -> baked (script.json) time
  const z = zoomAt(tB, script.zooms);
  const cap = cfg.captions !== false ? captionAt(tB, script.segments) : null;
  const theme = cfg.frame_theme || "float";
  const framed = theme !== "full";
  const ar = srcAr || 16 / 10;
  const rect = framed ? windowRect(cfg, theme, CW, CH, ar) : null;
  const radius = framed ? +(cfg.radius ?? 24) : 0;
  const bar = theme === "browser" ? Math.max(34, rect.h * 0.055) : 0;
  const logo = logoSrc(cfg, pid);
  const winH = framed ? rect.h : CH;
  const capSize = Math.max(16, Math.round(winH * 0.033));
  const introF = Math.round(T.introDur * fps);
  const contentF = Math.max(1, Math.round(T.contentDur * fps));
  const outroF = Math.round(T.outroDur * fps);
  const cardTop = cfg.card_top || cfg.brand_top || "#005DBC";
  const cardBot = cfg.card_bottom || cfg.brand_bottom || "#081428";

  const windowContent = (
    <>
      {introF > 0 && (
        <Sequence from={0} durationInFrames={introF}>
          <CardPreview style={cfg.card_style} top={cardTop} bot={cardBot}
                       title={cfg.title} subtitle={cfg.subtitle} logo={logo} height={winH} />
        </Sequence>
      )}
      <Sequence from={introF} durationInFrames={contentF}>
        <AbsoluteFill
          style={z ? { transform: `scale(${z.s})`, transformOrigin: `${z.cx * 100}% ${z.cy * 100}%` } : undefined}
        >
          <Video src={videoSrc} style={{ width: "100%", height: "100%" }}
                 startFrom={Math.round(T.bakedIntro * fps)} pauseWhenBuffering />
        </AbsoluteFill>
        {(script.elements || []).map((el, i) => {
          if (el.start != null && el.end != null && el.end > el.start && (tB < el.start || tB > el.end)) return null;
          return (
            <div key={i}
              style={{
                position: "absolute", left: `${el.x * 100}%`, top: `${el.y * 100}%`,
                width: `${el.w * 100}%`, height: `${el.h * 100}%`, borderRadius: 4,
                display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden",
                ...(ELT_STYLE[el.type] || ELT_STYLE.box),
              }}>
              {el.type === "text" && (
                <span style={{ color: "#fff", background: "rgba(0,0,0,.55)", padding: "0.3em 0.6em",
                               borderRadius: 6, font: `600 ${capSize}px -apple-system, sans-serif`, textAlign: "center" }}>
                  {el.text || ""}
                </span>
              )}
              {el.type === "image" && el.src && (
                <img src={`/media/${pid}/${String(el.src).split("/").slice(-2).join("/")}`}
                     style={{ width: "100%", height: "100%", objectFit: "contain" }} />
              )}
            </div>
          );
        })}
        {cap && (
          <div style={{ position: "absolute", left: 0, right: 0, bottom: "5.5%",
                        display: "flex", justifyContent: "center", pointerEvents: "none" }}>
            <span style={{ maxWidth: "82%", background: "rgba(0,0,0,.5)", color: "#fff",
                           padding: `${capSize * 0.4}px ${capSize * 0.6}px`,
                           font: `500 ${capSize}px -apple-system, 'Arial', sans-serif`,
                           lineHeight: 1.35, borderRadius: 4, textAlign: "center" }}>
              {cap}
            </span>
          </div>
        )}
      </Sequence>
      {outroF > 0 && (
        <Sequence from={introF + contentF} durationInFrames={outroF}>
          <CardPreview style={cfg.card_style} top={cardTop} bot={cardBot}
                       title={cfg.outro_title} subtitle={cfg.outro_subtitle} logo={logo} height={winH} />
        </Sequence>
      )}
    </>
  );

  const audioLayers = (
    <>
      {musicSrc && <Audio src={musicSrc} loop volume={Math.pow(10, (cfg.music_gain ?? -14) / 20)} />}
      {(script.sounds || []).map((s, i) => {
        if (s.start == null || !s.sfx) return null;
        const from = Math.round((s.start + T.shift) * fps);
        if (from < 0) return null;
        return (
          <Sequence key={i} from={from} durationInFrames={Math.round(2.5 * fps)}>
            <Audio src={`/assets/sfx/${s.sfx}.wav`}
                   volume={Math.pow(10, Math.max(-24, Math.min(6, +(s.gain || 0))) / 20)} />
          </Sequence>
        );
      })}
    </>
  );

  if (!framed) {
    return (
      <AbsoluteFill style={{ background: "#000" }}>
        <AbsoluteFill style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ position: "relative", aspectRatio: `${ar}`, width: ar >= CW / CH ? "100%" : "auto",
                        height: ar >= CW / CH ? "auto" : "100%", overflow: "hidden" }}>
            {windowContent}
          </div>
        </AbsoluteFill>
        {logo && (
          <img src={logo} style={{ position: "absolute", top: "2.2%", right: "2.2%", width: "9%", opacity: 0.85 }} />
        )}
        {audioLayers}
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={bgStyle(cfg, pid)}>
      {theme === "split" && (
        <div style={{ position: "absolute", left: "4.5%", top: 0, bottom: 0, width: `${(rect.x / CW) * 100 - 9}%`,
                      display: "flex", flexDirection: "column", justifyContent: "center", gap: CH * 0.02 }}>
          {logo && <img src={logo} style={{ width: "62%", maxHeight: CH * 0.12, objectFit: "contain",
                                            objectPosition: "left" }} />}
          <div style={{ color: "#fff", font: `700 ${CH * 0.055}px -apple-system, sans-serif`, lineHeight: 1.2 }}>
            {cfg.title || ""}
          </div>
          <div style={{ width: "26%", height: Math.max(3, CH / 200), background: "#7fb2ff" }} />
          <div style={{ color: "#becde1", font: `400 ${CH * 0.026}px -apple-system, sans-serif` }}>
            {cfg.subtitle || ""}
          </div>
        </div>
      )}
      {theme !== "split" && logo && (
        <img src={logo}
             style={{ position: "absolute", top: "2%", right: "2%",
                      maxWidth: "13%", maxHeight: `${((rect.y - bar) / CH) * 100 - 3.5}%`,
                      minHeight: 12, objectFit: "contain" }} />
      )}
      <div style={{ position: "absolute", left: rect.x, top: rect.y - bar, width: rect.w, height: rect.h + bar,
                    borderRadius: radius, boxShadow: SHADOWS[cfg.shadow || "medium"] || SHADOWS.medium,
                    background: "#fff", overflow: "hidden" }}>
        {theme === "browser" && (
          <div style={{ height: bar, background: "#f1f3f6", borderBottom: "1px solid #e2e6eb",
                        display: "flex", alignItems: "center", padding: `0 ${bar * 0.5}px`, gap: bar * 0.22 }}>
            {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
              <span key={c} style={{ width: bar * 0.34, height: bar * 0.34, borderRadius: "50%", background: c }} />
            ))}
            <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
              <div style={{ width: "44%", height: bar * 0.58, background: "#fff", borderRadius: bar * 0.29,
                            display: "flex", alignItems: "center", justifyContent: "center",
                            color: "#78828f", font: `400 ${bar * 0.34}px -apple-system, sans-serif` }}>
                {cfg.browser_url || ""}
              </div>
            </div>
          </div>
        )}
        <div style={{ position: "absolute", left: 0, right: 0, top: bar, bottom: 0, overflow: "hidden" }}>
          {windowContent}
        </div>
      </div>
      {audioLayers}
    </AbsoluteFill>
  );
};
