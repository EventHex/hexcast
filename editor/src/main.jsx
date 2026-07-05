import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { AuthPage } from "./components/AuthPage.jsx";
import "./styles.css";

// Auth gate: nothing renders until we know who (if anyone) is signed in.
function Root() {
  const [me, setMe] = useState(undefined);   // undefined=checking, null=logged out, {}=user
  const [google, setGoogle] = useState(false);
  useEffect(() => {
    fetch("/api/auth/me").then((r) => r.json())
      .then((d) => { setMe(d.user); setGoogle(!!d.google); })
      .catch(() => setMe(null));
  }, []);
  if (me === undefined) return <div className="boot">Loading…</div>;
  if (!me) return <AuthPage google={google} onAuthed={setMe} />;
  return <App user={me} onLogout={() => setMe(null)} />;
}

createRoot(document.getElementById("root")).render(<Root />);
