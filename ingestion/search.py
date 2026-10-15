"""
Step 6 (Phase 2) - Test retrieval: does the right chunk come back for a question?

  python ingestion/search.py "What is ATKT?"      -> top 5 chunks for one question
  python ingestion/search.py --eval               -> run all questions in data/eval/questions.csv
                                                     and score vector vs hybrid search

Hybrid search = vector (meaning) search + keyword search, merged with
Reciprocal Rank Fusion. Keyword search catches exact terms like "ATKT" or "PPO"
that embeddings sometimes miss. Phase 3 will reuse search() from this file.
"""
import argparse
import csv
import re

import psycopg
from psycopg.rows import dict_row

from config import DATABASE_URL, EVAL_FILE
from embeddings import embed_queries, to_pgvector

CANDIDATES = 20     # how many results each method contributes before fusion
RRF_K = 60          # standard RRF constant

VECTOR_SQL = """
SELECT chunk_id, source, page, url, section, text,
       1 - (embedding <=> %(qvec)s::vector) AS similarity
FROM chunks
ORDER BY embedding <=> %(qvec)s::vector
LIMIT %(k)s
"""

# Keyword part ORs the words of the question together ("minimum | attend | requir")
HYBRID_SQL = """
WITH q AS (
    SELECT %(qvec)s::vector AS v,
           NULLIF(replace(plainto_tsquery('english', %(qtext)s)::text, '&', '|'), '')::tsquery AS t
),
vec AS (
    SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> q.v) AS r
    FROM chunks, q
    ORDER BY embedding <=> q.v
    LIMIT %(cand)s
),
kw AS (
    SELECT chunk_id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q.t) DESC) AS r
    FROM chunks, q
    WHERE q.t IS NOT NULL AND tsv @@ q.t
    ORDER BY ts_rank_cd(tsv, q.t) DESC
    LIMIT %(cand)s
)
SELECT c.chunk_id, c.source, c.page, c.url, c.section, c.text,
       1 - (c.embedding <=> q.v) AS similarity,
       COALESCE(1.0 / (%(rrf)s + vec.r), 0) + COALESCE(1.0 / (%(rrf)s + kw.r), 0) AS rrf_score
FROM chunks c
CROSS JOIN q
LEFT JOIN vec ON vec.chunk_id = c.chunk_id
LEFT JOIN kw  ON kw.chunk_id  = c.chunk_id
WHERE vec.chunk_id IS NOT NULL OR kw.chunk_id IS NOT NULL
ORDER BY rrf_score DESC
LIMIT %(k)s
"""


def search(conn, question: str, k: int = 5, mode: str = "hybrid", qvec=None) -> list[dict]:
    if qvec is None:
        qvec = embed_queries([question])[0]
    params = {"qvec": to_pgvector(qvec), "qtext": question, "k": k,
              "cand": CANDIDATES, "rrf": RRF_K}
    sql = HYBRID_SQL if mode == "hybrid" else VECTOR_SQL
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def where(r: dict) -> str:
    return f"{r['source']} p.{r['page']}" if r["page"] else (r["url"] or r["source"])


def show(question: str, conn) -> None:
    results = search(conn, question)
    print(f"\nQ: {question}\n")
    for i, r in enumerate(results, 1):
        print(f"[{i}] {where(r)}   similarity {r['similarity']:.2f}")
        if r["section"]:
            print(f"    section: {r['section']}")
        snippet = re.sub(r"\s+", " ", r["text"])[:300]
        print(f"    {snippet}...\n")


def expected_page(source: str):
    """'SRB p.8' -> ('SRB', 8). Returns None if the source has no page."""
    m = re.search(r"(\w+)\s+p\.?\s*(\d+)", source)
    return (m.group(1).lower(), int(m.group(2))) if m else None


def is_hit(results, exp) -> bool:
    doc, page = exp
    return any(r["page"] == page and doc in r["source"].lower() for r in results)


def run_eval(conn, k: int = 5) -> None:
    rows = list(csv.DictReader(EVAL_FILE.open(encoding="utf-8")))
    rows = [r for r in rows if expected_page(r["source"])]
    print(f"Evaluating retrieval on {len(rows)} questions (hit = expected page in top {k})\n")

    qvecs = []
    for i in range(0, len(rows), 50):            # embed the questions in batches
        qvecs += embed_queries([r["question"] for r in rows[i:i + 50]])

    score = {"vector": 0, "hybrid": 0}
    misses = []
    for r, qv in zip(rows, qvecs):
        exp = expected_page(r["source"])
        res = {m: search(conn, r["question"], k=k, mode=m, qvec=qv) for m in score}
        marks = []
        for m in score:
            hit = is_hit(res[m], exp)
            score[m] += hit
            marks.append("OK  " if hit else "MISS")
        print(f"  vector {marks[0]} | hybrid {marks[1]} | {r['question'][:70]}")
        if not is_hit(res["hybrid"], exp):
            misses.append((r, res["hybrid"]))

    n = len(rows)
    print(f"\nHit rate @ top {k}:  vector {score['vector']}/{n} ({score['vector'] / n:.0%})"
          f"   hybrid {score['hybrid']}/{n} ({score['hybrid'] / n:.0%})")
    if misses:
        print("\nHybrid misses (expected -> what came back):")
        for r, res in misses:
            got = ", ".join(where(x) for x in res[:3])
            print(f"  - {r['question'][:60]}\n      expected {r['source']} -> got {got}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*")
    parser.add_argument("--eval", action="store_true")
    args = parser.parse_args()

    with psycopg.connect(DATABASE_URL) as conn:
        if args.eval:
            run_eval(conn)
        elif args.question:
            show(" ".join(args.question), conn)
        else:
            parser.print_help()


if __name__ == "__main__":
    main()