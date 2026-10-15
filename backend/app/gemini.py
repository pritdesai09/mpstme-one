"""Thin wrapper around the Gemini API: query embeddings + answer generation."""
import time

from google import genai
from google.genai import types

from .settings import CHAT_MODEL, EMBED_DIM, EMBED_MODEL, GEMINI_API_KEY


class GeminiUnavailable(Exception):
    """Quota used up or Gemini overloaded - shown to the user as a friendly error."""


_client = None


def client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY missing in .env")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _with_retries(fn, attempts: int = 3):
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                raise GeminiUnavailable("Gemini quota reached. Please try again later.") from e
            if attempt == attempts - 1:
                raise GeminiUnavailable("Gemini is busy right now. Please try again in a minute.") from e
            time.sleep(2 * (attempt + 1))      # 503 / timeout: short wait, then retry


def embed_query(question: str) -> list[float]:
    text = question
    if "embedding-2" in EMBED_MODEL:
        text = f"task: search result | query: {question}"
    cfg = {"output_dimensionality": EMBED_DIM}
    if "embedding-2" not in EMBED_MODEL:
        cfg["task_type"] = "RETRIEVAL_QUERY"

    def call():
        resp = client().models.embed_content(
            model=EMBED_MODEL,
            contents=[types.Content(parts=[types.Part.from_text(text=text)])],
            config=types.EmbedContentConfig(**cfg),
        )
        return list(resp.embeddings[0].values)

    return _with_retries(call)


def generate(system: str, prompt: str, temperature: float = 0.2, json_schema: dict | None = None) -> str:
    cfg = dict(system_instruction=system, temperature=temperature)
    if json_schema:
        cfg.update(response_mime_type="application/json", response_schema=json_schema)

    def call():
        resp = client().models.generate_content(
            model=CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(**cfg),
        )
        return (resp.text or "").strip()

    return _with_retries(call)


def to_pgvector(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"