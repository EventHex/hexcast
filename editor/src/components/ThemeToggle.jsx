import React, { useState } from "react";

// Light/dark toggle. Persists to localStorage and stamps data-theme on <html>,
// which overrides the prefers-color-scheme default in styles.css. main.jsx
// applies the stored choice before first paint so there's no flash.
export function ThemeToggle() {
  const [dark, setDark] = useState(() => {
    const t = localStorage.getItem("remaster_theme");
    return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  });
  const flip = () => {
    const d = !dark;
    document.documentElement.setAttribute("data-theme", d ? "dark" : "light");
    localStorage.setItem("remaster_theme", d ? "dark" : "light");
    setDark(d);
  };
  return (
    <button className="themetoggle" title="Toggle light / dark theme" aria-label="Toggle light or dark theme" onClick={flip}>
      {dark ? "☀" : "☾"}
    </button>
  );
}
