import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { Library } from "./Library.jsx";
import { BrandsPage } from "./BrandsPage.jsx";
import { SettingsPage } from "./SettingsPage.jsx";
import { HelpPage } from "./HelpPage.jsx";
import { Onboarding } from "./Onboarding.jsx";

// Persistent product shell: one top bar (Library · Brands · Settings · Help +
// workspace stats) around every non-editor view. The editor is a separate
// focused mode (App renders it directly when ?project= is present).

const VIEWS = [
  ["library", "Library"],
  ["brands", "Brands"],
  ["settings", "Settings"],
  ["help", "Help"],
];

const fmtGB = (b) => (b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(b / 1e6)) + " MB");

function useView() {
  const [view, setView] = useState(() => new URLSearchParams(location.search).get("view") || "library");
  useEffect(() => {
    const onPop = () => setView(new URLSearchParams(location.search).get("view") || "library");
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = (v) => {
    const u = new URL(location.href);
    if (v === "library") u.searchParams.delete("view"); else u.searchParams.set("view", v);
    history.pushState({}, "", u);
    setView(v);
  };
  return [view, go];
}

export function Shell() {
  const [view, go] = useView();
  const [stats, setStats] = useState(null);   // {count, exported, langs, bytes}
  const [anyKey, setAnyKey] = useState(null);
  const [onboard, setOnboard] = useState(false);

  const loadStats = () => api("/api/projects").then((r) => {
    const ps = r.projects || [];
    // language variants are "<id> (Lang)" — count distinct base projects
    const bases = new Set(ps.map((p) => p.id.replace(/-[a-z]+$/, "")));
    const langs = ps.filter((p) => /\([A-Z]/.test(p.name || "")).length;
    setStats({
      count: bases.size, total: ps.length,
      exported: ps.filter((p) => p.status === "exported").length,
      langs, bytes: ps.reduce((a, p) => a + (p.size || 0), 0),
    });
  }).catch(() => setStats(null));

  useEffect(() => {
    loadStats();
    api("/api/settings").then((v) => setAnyKey(Object.values(v.keys || {}).some((k) => k.set))).catch(() => setAnyKey(false));
  }, [view]);

  useEffect(() => {
    if (anyKey === false && stats && stats.total === 0 && !localStorage.getItem("remaster_onboarded")) setOnboard(true);
  }, [anyKey, stats]);

  return (
    <div className="app">
      <header className="shellbar">
        <span className="brand">Remaster</span>
        <nav className="shellnav">
          {VIEWS.map(([v, label]) => (
            <button key={v} className={view === v ? "on" : ""} onClick={() => go(v)}>
              {label}{v === "settings" && anyKey === false ? " ·" : ""}
            </button>
          ))}
        </nav>
        <span className="grow" />
        {stats && (
          <span className="stats" title="Workspace">
            <b>{stats.count}</b> video{stats.count !== 1 ? "s" : ""}
            <span className="dot" /> <b>{stats.exported}</b> exported
            {stats.langs > 0 && <><span className="dot" /> <b>{stats.langs}</b> lang</>}
            <span className="dot" /> {fmtGB(stats.bytes)}
          </span>
        )}
      </header>

      <div className="shellbody">
        {view === "library" && <Library onChange={loadStats} />}
        {view === "brands" && <BrandsPage />}
        {view === "settings" && <SettingsPage />}
        {view === "help" && <HelpPage />}
      </div>

      {onboard && <Onboarding
        onKeys={() => { localStorage.setItem("remaster_onboarded", "1"); setOnboard(false); go("settings"); }}
        onSkip={() => { localStorage.setItem("remaster_onboarded", "1"); setOnboard(false); }} />}
    </div>
  );
}
