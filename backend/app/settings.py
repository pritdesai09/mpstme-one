"""All configuration in one place. Values come from the .env file (locally) or
from environment variables (on Azure)."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]           # .../mpstme-one/backend
PROJECT_DIR = BACKEND_DIR.parent                            # .../mpstme-one
load_dotenv(PROJECT_DIR / ".env", override=True)

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-3.5-flash")
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-2")
EMBED_DIM = 768

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mpstme:mpstme_dev@localhost:5434/mpstme_one")

# --- Auth ---
JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_hex(32)   # set a fixed one in .env!
JWT_HOURS = 24 * 7
# e.g. "nmims.edu,nmims.in" to only allow college emails. Empty = anyone can sign up.
ALLOWED_EMAIL_DOMAINS = [d.strip().lower() for d in os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(",") if d.strip()]

# --- RAG tuning ---
RETRIEVE_CANDIDATES = 10      # chunks fetched from the DB
CONTEXT_CHUNKS = 5            # chunks actually shown to Gemini
MAX_PER_PAGE = 2              # avoid 3 chunks from the same page crowding out other sources
HISTORY_TURNS = 3             # previous Q&A pairs sent to Gemini for follow-up questions

# Confidence thresholds on the best cited chunk's similarity.
# Phase 5 (scripts/eval_rag.py) tells you what to set these to.
CONF_HIGH = float(os.getenv("CONF_HIGH", "0.72"))
CONF_MEDIUM = float(os.getenv("CONF_MEDIUM", "0.62"))

# --- Limits ---
RATE_LIMIT_PER_MINUTE = 12    # questions per user per minute (protects your Gemini quota)
MAX_QUESTION_CHARS = 1000

# Frontends allowed to call the API (Phase 6 React dev server runs on 5173)
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
EVAL_DIR = PROJECT_DIR / "data" / "eval"