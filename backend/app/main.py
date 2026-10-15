"""
MPSTME ONE API.

Run (inside the backend folder):   uvicorn app.main:app --reload
Then open http://localhost:8000/docs to try every endpoint in the browser.
"""
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import db, rag
from .db import get_conn
from .gemini import GeminiUnavailable
from .security import create_token, current_user, hash_password, verify_password
from .settings import (ALLOWED_EMAIL_DOMAINS, CHAT_MODEL, CORS_ORIGINS,
                       MAX_QUESTION_CHARS, RATE_LIMIT_PER_MINUTE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.open_pool()
    yield
    db.close_pool()


app = FastAPI(title="MPSTME ONE API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLES = {"Student", "Faculty", "Staff"}


# ------------------------------------------------------------------ schemas

class SignupIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str
    password: str = Field(min_length=8, max_length=128)
    role: str = "Student"


class LoginIn(BaseModel):
    email: str
    password: str


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    session_id: str | None = None


def _public_user(u: dict) -> dict:
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}


# ------------------------------------------------------------------ health

@app.get("/api/health", tags=["system"])
def health():
    with get_conn() as conn:
        n = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
    return {"status": "ok", "chunks": n, "model": CHAT_MODEL}


# ------------------------------------------------------------------ auth

@app.post("/api/auth/signup", tags=["auth"])
def signup(body: SignupIn):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    if ALLOWED_EMAIL_DOMAINS and email.split("@")[1] not in ALLOWED_EMAIL_DOMAINS:
        raise HTTPException(400, f"Use your college email ({', '.join(ALLOWED_EMAIL_DOMAINS)}).")
    if body.role not in ROLES:
        raise HTTPException(400, f"Role must be one of: {', '.join(sorted(ROLES))}.")
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists. Sign in instead.")
        user = conn.execute(
            "INSERT INTO users (email, name, role, password_hash) VALUES (%s, %s, %s, %s) "
            "RETURNING id, email, name, role",
            (email, body.name.strip(), body.role, hash_password(body.password)),
        ).fetchone()
    return {"token": create_token(user["id"]), "user": _public_user(user)}


@app.post("/api/auth/login", tags=["auth"])
def login(body: LoginIn):
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = %s",
                            (body.email.strip().lower(),)).fetchone()
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Email or password is incorrect.")
    return {"token": create_token(user["id"]), "user": _public_user(user)}


@app.get("/api/auth/me", tags=["auth"])
def me(user: dict = Depends(current_user)):
    return _public_user(user)


# ------------------------------------------------------------------ chat

_recent: dict[int, deque] = defaultdict(deque)


def _rate_limit(user_id: int) -> None:
    now, q = time.time(), _recent[user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(429, "You're asking too fast. Wait a minute and try again.")
    q.append(now)


@app.post("/api/chat", tags=["chat"])
def chat(body: ChatIn, user: dict = Depends(current_user)):
    _rate_limit(user["id"])
    question = body.message.strip()

    with get_conn() as conn:
        if body.session_id:
            owner = conn.execute("SELECT user_id FROM chat_sessions WHERE id = %s",
                                 (body.session_id,)).fetchone()
            if not owner or owner["user_id"] != user["id"]:
                raise HTTPException(404, "Chat not found.")
            session_id = body.session_id
            history = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = %s ORDER BY id",
                (session_id,)).fetchall()
        else:
            session_id, history = None, []

    try:
        result = rag.run(question, history)
    except GeminiUnavailable as e:
        raise HTTPException(503, str(e))

    with get_conn() as conn, conn.transaction():
        if session_id is None:
            session_id = uuid.uuid4().hex
            title = question if len(question) <= 60 else question[:57] + "..."
            conn.execute("INSERT INTO chat_sessions (id, user_id, title) VALUES (%s, %s, %s)",
                         (session_id, user["id"], title))
        conn.execute("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)",
                     (session_id, question))
        conn.execute(
            "INSERT INTO messages (session_id, role, content, citations, confidence) "
            "VALUES (%s, 'assistant', %s, %s, %s)",
            (session_id, result.answer, Jsonb(result.citations), Jsonb(result.confidence)))
        conn.execute("UPDATE chat_sessions SET updated_at = now() WHERE id = %s", (session_id,))

    return {
        "session_id": session_id,
        "answer": result.answer,
        "citations": result.citations,
        "confidence": result.confidence,
        "route": result.route,
    }


@app.get("/api/sessions", tags=["chat"])
def list_sessions(user: dict = Depends(current_user)):
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, title, created_at, updated_at FROM chat_sessions "
            "WHERE user_id = %s ORDER BY updated_at DESC LIMIT 100", (user["id"],)).fetchall()


@app.get("/api/sessions/{session_id}", tags=["chat"])
def get_session(session_id: str, user: dict = Depends(current_user)):
    with get_conn() as conn:
        sess = conn.execute("SELECT id, title, user_id FROM chat_sessions WHERE id = %s",
                            (session_id,)).fetchone()
        if not sess or sess["user_id"] != user["id"]:
            raise HTTPException(404, "Chat not found.")
        msgs = conn.execute(
            "SELECT role, content, citations, confidence, created_at FROM messages "
            "WHERE session_id = %s ORDER BY id", (session_id,)).fetchall()
    return {"id": sess["id"], "title": sess["title"], "messages": msgs}


@app.delete("/api/sessions/{session_id}", tags=["chat"])
def delete_session(session_id: str, user: dict = Depends(current_user)):
    with get_conn() as conn:
        deleted = conn.execute("DELETE FROM chat_sessions WHERE id = %s AND user_id = %s RETURNING id",
                               (session_id, user["id"])).fetchone()
    if not deleted:
        raise HTTPException(404, "Chat not found.")
    return {"deleted": session_id}