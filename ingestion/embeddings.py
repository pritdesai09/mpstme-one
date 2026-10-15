"""
Gemini embeddings helper - used by load_vectors.py now, and by the chatbot backend later.

gemini-embedding-2 has no task_type setting; instead the task is written into the text:
  documents -> "title: <title> | text: <content>"
  queries   -> "task: search result | query: <question>"
"""
import time

from config import EMBED_DIM, EMBED_MODEL, GEMINI_API_KEY


class QuotaExhausted(Exception):
    pass


_client = None


def client():
    global _client
    if _client is None:
        from google import genai
        if not GEMINI_API_KEY:
            raise SystemExit("GEMINI_API_KEY missing in .env")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _uses_prefixes() -> bool:
    return "embedding-2" in EMBED_MODEL


def format_document(title: str, text: str) -> str:
    if _uses_prefixes():
        return f"title: {title or 'none'} | text: {text}"
    return text


def format_query(question: str) -> str:
    if _uses_prefixes():
        return f"task: search result | query: {question}"
    return question


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    from google.genai import types

    cfg = {"output_dimensionality": EMBED_DIM}
    if not _uses_prefixes():                     # older models use task_type instead
        cfg["task_type"] = task_type
    quota_hits = 0
    for attempt in range(6):
        try:
            resp = client().models.embed_content(
                model=EMBED_MODEL,
                # one Content per text => one embedding per text
                # (a plain list of strings gets merged into a single embedding)
                contents=[types.Content(parts=[types.Part.from_text(text=t)]) for t in texts],
                config=types.EmbedContentConfig(**cfg),
            )
            vectors = [e.values for e in resp.embeddings]
            if len(vectors) != len(texts):
                raise RuntimeError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
            return [_normalize(v) for v in vectors]
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                quota_hits += 1
                if quota_hits >= 3:
                    raise QuotaExhausted(msg[:200])
                print("    rate limit hit - waiting 65s")
                time.sleep(65)
            elif isinstance(e, RuntimeError):
                raise
            else:
                wait = 15 * (attempt + 1)
                print(f"    {e.__class__.__name__}: {msg[:80]}... retrying in {wait}s")
                time.sleep(wait)
    raise RuntimeError("Gemini embeddings kept failing; try again later")


def _normalize(v: list[float]) -> list[float]:
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v] if norm else v


def embed_documents(items: list[tuple[str, str]]) -> list[list[float]]:
    """items = [(title, text), ...]"""
    return _embed([format_document(t, x) for t, x in items], "RETRIEVAL_DOCUMENT")


def embed_queries(questions: list[str]) -> list[list[float]]:
    return _embed([format_query(q) for q in questions], "RETRIEVAL_QUERY")


def to_pgvector(v: list[float]) -> str:
    """Python list -> pgvector text format '[0.1,0.2,...]'"""
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"