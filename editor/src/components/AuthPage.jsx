import React, { useState } from "react";
import logoStack from "../assets/logo-stack.png";

// Sign-in / sign-up gate. Shown until /api/auth/me returns a user. On success
// the session cookie is set by the server and onAuthed(user) swaps in the app.
export function AuthPage({ onAuthed, google }) {
  const [mode, setMode] = useState("login");   // login | signup
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const signup = mode === "signup";

  const submit = async (e) => {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const body = signup ? { email, password, name } : { email, password };
      const r = await fetch(`/api/auth/${signup ? "signup" : "login"}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || "Something went wrong");
      onAuthed(signup ? j : j);
    } catch (e2) { setErr(String(e2.message || e2)); }
    setBusy(false);
  };

  return (
    <div className="authwrap">
      <form className="authcard" onSubmit={submit}>
        <img className="authbrand-logo" src={logoStack} alt="HexCast" />
        <p className="hint" style={{ textAlign: "center", marginTop: -4 }}>
          Your studio for demo & how-to videos.
        </p>
        <div className="subtabs" style={{ justifyContent: "center", margin: "6px 0 14px" }}>
          <button type="button" className={!signup ? "on" : ""} onClick={() => setMode("login")}>Log in</button>
          <button type="button" className={signup ? "on" : ""} onClick={() => setMode("signup")}>Sign up</button>
        </div>

        {signup && (
          <label className="lab col">Name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" autoComplete="name" />
          </label>
        )}
        <label className="lab col">Email
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                 placeholder="you@company.com" autoComplete="email" />
        </label>
        <label className="lab col">Password
          <input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)}
                 placeholder={signup ? "6+ characters" : "Your password"}
                 autoComplete={signup ? "new-password" : "current-password"} />
        </label>

        {err && <p className="autherr">{err}</p>}
        <button className="btn wide" type="submit" disabled={busy}>
          {busy ? "…" : signup ? "Create account" : "Log in"}
        </button>

        {google && (
          <>
            <div className="author"><span>or</span></div>
            <a className="btn sm ghost wide" href="/api/auth/google/login">Continue with Google</a>
          </>
        )}
        <p className="hint" style={{ textAlign: "center", marginTop: 12 }}>
          Bring your own API keys — set them in Settings after you sign in. Free while in beta.
        </p>
      </form>
    </div>
  );
}
