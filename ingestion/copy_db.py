"""
Copy the knowledge base (chunks + embeddings) from your local database to another
database, e.g. Azure. No Gemini calls - the existing embeddings are copied as-is.

Run:  python ingestion/copy_db.py "postgresql://USER:PASSWORD@HOST:5432/mpstme_one?sslmode=require"
"""
import sys
from pathlib import Path

import psycopg

from config import DATABASE_URL

COLUMNS = ["chunk_id", "source_type", "source", "url", "page", "section", "method",
           "text", "content_hash", "embed_model", "embedding"]
BATCH = 100


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python ingestion/copy_db.py "<target database URL>"')
    target_url = sys.argv[1]

    with psycopg.connect(DATABASE_URL) as src:
        rows = src.execute(
            f"SELECT {', '.join(COLUMNS[:-1])}, embedding::text FROM chunks ORDER BY chunk_id"
        ).fetchall()
    print(f"Read {len(rows)} chunks from the local database")
    if not rows:
        raise SystemExit("Local database is empty - run ingestion/load_vectors.py first.")

    placeholders = ", ".join(["%s"] * (len(COLUMNS) - 1)) + ", %s::vector"
    insert = (f"INSERT INTO chunks ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
              f"ON CONFLICT (chunk_id) DO NOTHING")

    with psycopg.connect(target_url, autocommit=True) as dst:
        print("Connected to target database")
        with dst.transaction():
            dst.execute((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
            dst.execute("TRUNCATE chunks")
        for i in range(0, len(rows), BATCH):
            with dst.transaction(), dst.cursor() as cur:
                cur.executemany(insert, rows[i:i + BATCH])
            print(f"  copied {min(i + BATCH, len(rows))}/{len(rows)}")
        total = dst.execute("SELECT count(*) FROM chunks").fetchone()[0]

    print(f"\nTarget database now holds {total} chunks.")


if __name__ == "__main__":
    main()