import { useState } from "react";
import { api } from "./api.js";
import { Wordmark } from "./ui.jsx";

export default function AuthPage({ onAuthed }) {
  const [mode, setMode] = useState("signin");
  const [form, setForm] = useState({ name: "", email: "", password: "", role: "Student" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const signingUp = mode === "signup";
  const update = (field) => (e) => setForm({ ...form, [field]: e.target.value });

  function switchMode(next) {
    setMode(next);
    setError("");
  }

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (signingUp && form.name.trim().length < 2) return setError("Enter your full name.");
    if (signingUp && form.password.length < 8) return setError("Use a password of at least 8 characters.");

    setBusy(true);
    try {
      const data = signingUp
        ? await api("/api/auth/signup", { method: "POST", body: form })
        : await api("/api/auth/login", {
            method: "POST",
            body: { email: form.email, password: form.password },
          });
      onAuthed(data.token, data.user);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <section className="auth__intro" aria-label="About MPSTME ONE">
        <Wordmark size="lg" />
        <h1 className="auth__headline">
          Your Student Resource Book, answered in seconds.
        </h1>
        <p className="auth__lede">
          Ask about attendance, exams, ATKT, fees or placements. Every answer points to the exact
          page it came from.
        </p>

        <figure className="specimen" aria-label="Example answer">
          <p className="specimen__q">What attendance do I need to sit for end-semester exams?</p>
          <div className="specimen__a">
            <p>
              You need at least <strong>80% attendance in each subject</strong>
              <sup className="cite cite--static">1</sup>. Between 70% and 80%, the Dean may grant
              up to 10% exemption after a hearing<sup className="cite cite--static">2</sup>.
            </p>
            <div className="tabs">
              <span className="tab">SRB p.8</span>
              <span className="tab">SRB p.8</span>
            </div>
          </div>
        </figure>
      </section>

      <section className="auth__panel">
        <form className="auth__form" onSubmit={submit} noValidate>
          <h2 className="auth__title">{signingUp ? "Create your account" : "Sign in"}</h2>

          {signingUp && (
            <label className="field">
              <span>Full name</span>
              <input value={form.name} onChange={update("name")} autoComplete="name" required />
            </label>
          )}
          <label className="field">
            <span>Email</span>
            <input type="email" value={form.email} onChange={update("email")} autoComplete="email" required />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              value={form.password}
              onChange={update("password")}
              autoComplete={signingUp ? "new-password" : "current-password"}
              required
            />
          </label>
          {signingUp && (
            <label className="field">
              <span>I am a</span>
              <select value={form.role} onChange={update("role")}>
                <option>Student</option>
                <option>Faculty</option>
                <option>Staff</option>
              </select>
            </label>
          )}

          {error && <p className="form-error" role="alert">{error}</p>}

          <button className="btn btn--primary btn--block" disabled={busy}>
            {busy ? "Please wait…" : signingUp ? "Create account" : "Sign in"}
          </button>

          <p className="auth__switch">
            {signingUp ? "Already have an account?" : "New to MPSTME ONE?"}{" "}
            <button type="button" className="link" onClick={() => switchMode(signingUp ? "signin" : "signup")}>
              {signingUp ? "Sign in" : "Create an account"}
            </button>
          </p>
        </form>
      </section>
    </div>
  );
}