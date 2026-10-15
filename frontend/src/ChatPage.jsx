import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Answer from "./Answer.jsx";
import { Icon, Wordmark } from "./ui.jsx";

const STARTERS = [
  "What attendance do I need to sit for end-semester exams?",
  "What is ATKT and when can I be promoted?",
  "What was the highest package in the 2026 placements?",
  "How do I apply for re-evaluation of my answer book?",
];

const SpeechRecognition =
  typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

export default function ChatPage({ user, onSignOut }) {
  const [sessions, setSessions] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loadingChat, setLoadingChat] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [listening, setListening] = useState(false);
  const [kbSize, setKbSize] = useState(null);

  const scrollRef = useRef(null);
  const inputRef = useRef(null);
  const recognitionRef = useRef(null);

  useEffect(() => {
    refreshSessions();
    api("/api/health").then((h) => setKbSize(h.chunks)).catch(() => {});
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }, [input]);

  async function refreshSessions() {
    try {
      setSessions(await api("/api/sessions"));
    } catch { /* shown elsewhere */ }
  }

  function newChat() {
    setActiveId(null);
    setMessages([]);
    setSidebarOpen(false);
    inputRef.current?.focus();
  }

  async function openChat(id) {
    if (id === activeId) return setSidebarOpen(false);
    setActiveId(id);
    setMessages([]);
    setLoadingChat(true);
    setSidebarOpen(false);
    try {
      const data = await api(`/api/sessions/${id}`);
      setMessages(data.messages.map((m) => ({ ...m, citations: m.citations || [] })));
    } catch (err) {
      setMessages([{ role: "error", content: err.message }]);
    } finally {
      setLoadingChat(false);
    }
  }

  async function deleteChat(id) {
    try {
      await api(`/api/sessions/${id}`, { method: "DELETE" });
      setSessions((list) => list.filter((s) => s.id !== id));
      if (id === activeId) newChat();
    } catch { /* ignore */ }
    setConfirmDelete(null);
  }

  async function send(text) {
    const question = (text ?? input).trim();
    if (!question || sending) return;
    setInput("");
    setMessages((m) => [...m.filter((x) => x.role !== "error"), { role: "user", content: question }]);
    setSending(true);
    try {
      const res = await api("/api/chat", {
        method: "POST",
        body: { message: question, session_id: activeId },
      });
      setMessages((m) => [
        ...m,
        { role: "assistant", content: res.answer, citations: res.citations, confidence: res.confidence },
      ]);
      if (!activeId) {
        setActiveId(res.session_id);
        refreshSessions();
      } else {
        setSessions((list) => [list.find((s) => s.id === activeId), ...list.filter((s) => s.id !== activeId)].filter(Boolean));
      }
    } catch (err) {
      setMessages((m) => [...m, { role: "error", content: err.message, retry: question }]);
    } finally {
      setSending(false);
    }
  }

  function retry(question) {
    setMessages((m) => {
      const copy = m.filter((x) => x.role !== "error");
      if (copy.at(-1)?.role === "user" && copy.at(-1).content === question) copy.pop();
      return copy;
    });
    send(question);
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function toggleVoice() {
    if (!SpeechRecognition) return;
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    const rec = new SpeechRecognition();
    rec.lang = "en-IN";
    rec.interimResults = true;
    rec.onresult = (e) => setInput(Array.from(e.results).map((r) => r[0].transcript).join(""));
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recognitionRef.current = rec;
    setListening(true);
    rec.start();
  }

  const activeTitle = sessions.find((s) => s.id === activeId)?.title;
  const empty = messages.length === 0 && !loadingChat;

  return (
    <div className={`app ${sidebarOpen ? "app--drawer" : ""}`}>
      <aside className="sidebar" aria-label="Your chats">
        <div className="sidebar__top">
          <Wordmark />
          <button className="icon-btn sidebar__close" onClick={() => setSidebarOpen(false)} aria-label="Close menu">
            <Icon name="close" />
          </button>
        </div>

        <button className="btn btn--primary btn--block" onClick={newChat}>
          <Icon name="plus" size={16} /> New chat
        </button>

        <nav className="history">
          {sessions.length === 0 && <p className="history__empty">Your chats will appear here.</p>}
          {sessions.map((s) => (
            <div key={s.id} className={`history__item ${s.id === activeId ? "is-active" : ""}`}>
              {confirmDelete === s.id ? (
                <div className="history__confirm">
                  <span>Delete this chat?</span>
                  <button className="link link--danger" onClick={() => deleteChat(s.id)}>Delete</button>
                  <button className="link" onClick={() => setConfirmDelete(null)}>Keep</button>
                </div>
              ) : (
                <>
                  <button className="history__open" onClick={() => openChat(s.id)} title={s.title}>
                    {s.title}
                  </button>
                  <button className="icon-btn history__delete" onClick={() => setConfirmDelete(s.id)} aria-label="Delete chat">
                    <Icon name="trash" size={15} />
                  </button>
                </>
              )}
            </div>
          ))}
        </nav>

        <div className="profile">
          <span className="profile__avatar" aria-hidden="true">{user.name.trim()[0]?.toUpperCase()}</span>
          <span className="profile__text">
            <strong>{user.name}</strong>
            <small>{user.role}</small>
          </span>
          <button className="icon-btn" onClick={onSignOut} aria-label="Sign out" title="Sign out">
            <Icon name="out" />
          </button>
        </div>
      </aside>
      <div className="scrim" onClick={() => setSidebarOpen(false)} />

      <main className="chat">
        <header className="chat__bar">
          <button className="icon-btn chat__menu" onClick={() => setSidebarOpen(true)} aria-label="Open menu">
            <Icon name="menu" />
          </button>
          <h1 className="chat__title">{activeTitle || "New chat"}</h1>
          {kbSize !== null && <span className="kb">Searching {kbSize} passages</span>}
        </header>

        <div className="chat__scroll" ref={scrollRef}>
          <div className="thread">
            {empty && (
              <div className="welcome">
                <h2>Hi {user.name.split(" ")[0]}, what do you need to know?</h2>
                <p>Answers come from the MPSTME Student Resource Book and the college website.</p>
                <div className="starters">
                  {STARTERS.map((q) => (
                    <button key={q} className="starter" onClick={() => send(q)}>{q}</button>
                  ))}
                </div>
              </div>
            )}

            {loadingChat && <p className="thinking">Loading chat…</p>}

            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="msg msg--user"><p>{m.content}</p></div>
              ) : m.role === "assistant" ? (
                <div key={i} className="msg msg--bot">
                  <Answer content={m.content} citations={m.citations} confidence={m.confidence} />
                </div>
              ) : (
                <div key={i} className="msg msg--error" role="alert">
                  <p>{m.content}</p>
                  {m.retry && <button className="btn btn--ghost" onClick={() => retry(m.retry)}>Try again</button>}
                </div>
              )
            )}

            {sending && (
              <div className="msg msg--bot">
                <p className="thinking">
                  Reading the Student Resource Book<span className="dots"><i /><i /><i /></span>
                </p>
              </div>
            )}
          </div>
        </div>

        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <div className="composer__box">
            <textarea
              ref={inputRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask about attendance, exams, fees, placements…"
              aria-label="Your question"
              maxLength={1000}
            />
            {SpeechRecognition && (
              <button
                type="button"
                className={`icon-btn ${listening ? "is-listening" : ""}`}
                onClick={toggleVoice}
                aria-label={listening ? "Stop voice input" : "Ask with your voice"}
              >
                <Icon name="mic" />
              </button>
            )}
            <button className="send" disabled={!input.trim() || sending} aria-label="Send">
              <Icon name="send" />
            </button>
          </div>
          <p className="composer__note">MPSTME ONE can make mistakes. Check important details with the source page.</p>
        </form>
      </main>
    </div>
  );
}