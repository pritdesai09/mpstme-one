"""
Step 5 (Phase 2) - Embed every chunk with Gemini and store it in PostgreSQL + pgvector.

- Creates the table/indexes from schema.sql (safe to re-run)
- Only embeds chunks that are new or changed (compares a content hash)
- Removes chunks from the DB that no longer exist in chunks.jsonl
- Commits after every batch: if the quota runs out, just run it again later

Run:  python ingestion/load_vectors.py
"""
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import psycopg

from config import CHUNKS, DATABASE_URL, EMBED_MODEL
from embeddings import QuotaExhausted, embed_documents, to_pgvector

BATCH_SIZE = 40              # chunks per Gemini request
DELAY_BETWEEN_BATCHES = 3    # seconds

UPSERT_SQL = """
INSERT INTO chunks (chunk_id, source_type, source, url, page, section, method,
                    text, content_hash, embed_model, embedding, updated_at)
VALUES (%(chunk_id)s, %(source_type)s, %(source)s, %(url)s, %(page)s, %(section)s, %(method)s,
        %(text)s, %(content_hash)s, %(embed_model)s, %(embedding)s::vector, now())
ON CONFLICT (chunk_id) DO UPDATE SET
    source_type = EXCLUDED.source_type, source = EXCLUDED.source, url = EXCLUDED.url,
    page = EXCLUDED.page, section = EXCLUDED.section, method = EXCLUDED.method,
    text = EXCLUDED.text, content_hash = EXCLUDED.content_hash,
    embed_model = EXCLUDED.embed_model, embedding = EXCLUDED.embedding, updated_at = now()
"""


def content_hash(c: dict) -> str:
    key = "|".join([EMBED_MODEL, c["source"], c.get("section") or "", c["text"]])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def title_for(c: dict) -> str:
    title = c["source"].removesuffix(".pdf").replace("_", " ").replace("-", " ")
    if c.get("section"):
        title += f" - {c['section']}"
    return title


def main() -> None:
    chunks = [json.loads(l) for l in CHUNKS.open(encoding="utf-8") if l.strip()]
    for c in chunks:
        c["content_hash"] = content_hash(c)
    print(f"{len(chunks)} chunks in {CHUNKS.name}")

    try:
        conn = psycopg.connect(DATABASE_URL, autocommit=True)
    except psycopg.OperationalError as e:
        raise SystemExit(f"Can't connect to the database. Is Docker running?\n"
                         f"Try: docker compose up -d\n\n{e}")

    with conn.transaction():
        conn.execute((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
        existing = dict(conn.execute("SELECT chunk_id, content_hash FROM chunks").fetchall())

        # remove chunks that disappeared from chunks.jsonl
        current_ids = {c["chunk_id"] for c in chunks}
        stale = [cid for cid in existing if cid not in current_ids]
        if stale:
            conn.execute("DELETE FROM chunks WHERE chunk_id = ANY(%s)", (stale,))
            print(f"Removed {len(stale)} old chunks")

    todo = [c for c in chunks if existing.get(c["chunk_id"]) != c["content_hash"]]
    print(f"{len(chunks) - len(todo)} already embedded, {len(todo)} to embed with {EMBED_MODEL}\n")

    done = 0
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        try:
            vectors = embed_documents([(title_for(c), c["text"]) for c in batch])
        except QuotaExhausted:
            print("\n!! Gemini quota used up for now. Progress is saved - run this again later.")
            break
        rows = [{
            "chunk_id": c["chunk_id"], "source_type": c["source_type"], "source": c["source"],
            "url": c.get("url"), "page": c.get("page"), "section": c.get("section"),
            "method": c.get("method", "web"), "text": c["text"],
            "content_hash": c["content_hash"], "embed_model": EMBED_MODEL,
            "embedding": to_pgvector(v),
        } for c, v in zip(batch, vectors)]
        with conn.transaction(), conn.cursor() as cur:   # one commit per batch
            cur.executemany(UPSERT_SQL, rows)
        done += len(batch)
        print(f"  embedded {done}/{len(todo)}")
        time.sleep(DELAY_BETWEEN_BATCHES)

    total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    by_source = conn.execute("SELECT source FROM chunks").fetchall()
    conn.close()

    print(f"\nDatabase now holds {total} / {len(chunks)} chunks")
    for src, n in Counter(r[0] for r in by_source).most_common():
        print(f"  {n:4d}  {src}")
    if total == len(chunks):
        print("\nAll chunks embedded. Next: python ingestion/search.py \"your question\"")


if __name__ == "__main__":
    main()