"""Database connection pool + the app's own tables (users, chats, messages).
The `chunks` table is created by ingestion/load_vectors.py."""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .settings import DATABASE_URL

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'Student',
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          TEXT PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_sessions_user_idx ON chat_sessions (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,                 -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    citations   JSONB NOT NULL DEFAULT '[]',
    confidence  JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_session_idx ON messages (session_id, id);
"""

pool: ConnectionPool | None = None


def open_pool() -> None:
    global pool
    pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=True,
                          kwargs={"autocommit": True, "row_factory": dict_row})
    pool.wait(timeout=15)
    with pool.connection() as conn:
        conn.execute(APP_SCHEMA)


def close_pool() -> None:
    if pool:
        pool.close()


@contextmanager
def get_conn():
    if pool is None:
        open_pool()
    with pool.connection() as conn:
        yield conn