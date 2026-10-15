import { useEffect, useState } from "react";
import { api, setUnauthorizedHandler, tokenStore } from "./api.js";
import AuthPage from "./AuthPage.jsx";
import ChatPage from "./ChatPage.jsx";

export default function App() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(Boolean(tokenStore.get()));

  useEffect(() => {
    setUnauthorizedHandler(() => {
      tokenStore.clear();
      setUser(null);
    });
    if (!tokenStore.get()) return;
    api("/api/auth/me")
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setChecking(false));
  }, []);

  function handleAuthed(token, user) {
    tokenStore.set(token);
    setUser(user);
  }

  function handleSignOut() {
    tokenStore.clear();
    setUser(null);
  }

  if (checking) return <div className="boot">Opening MPSTME ONE…</div>;
  if (!user) return <AuthPage onAuthed={handleAuthed} />;
  return <ChatPage user={user} onSignOut={handleSignOut} />;
}