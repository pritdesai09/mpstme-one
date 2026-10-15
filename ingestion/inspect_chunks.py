"""
Step 4 - Sanity-check the knowledge base before embedding it.

  python ingestion/inspect_chunks.py               -> overview + coverage check of key topics
  python ingestion/inspect_chunks.py "ATKT"        -> show chunks containing a word/phrase
"""
import json
import sys
from collections import Counter

from config import CHUNKS

# Topics your eval questions depend on. Each must appear in at least one chunk.
KEY_TERMS = [
    "80%", "attendance", "re-examination", "re-valuation", "refund", "library",
    "scholarship", "ATKT", "grace marks", "40%", "CGPA", "placement",
    "Pre Placement Offer", "Virginia Tech", "internship",
]


def main() -> None:
    chunks = [json.loads(l) for l in CHUNKS.open(encoding="utf-8") if l.strip()]

    if len(sys.argv) > 1:
        term = " ".join(sys.argv[1:]).lower()
        hits = [c for c in chunks if term in c["text"].lower()]
        print(f"{len(hits)} chunks contain {term!r}\n")
        for c in hits[:5]:
            where = f"p.{c['page']}" if c["page"] else c["url"]
            print(f"--- {c['source']} {where} [{c.get('method', 'web')}] section: {c['section']!r}")
            print(c["text"][:700], "\n")
        return

    print(f"Total chunks: {len(chunks)}\n")
    print("Chunks per source:")
    for src, n in Counter(c["source"] for c in chunks).most_common():
        print(f"  {n:4d}  {src}")
    methods = Counter(c.get("method", "web") for c in chunks)
    print(f"\nBy extraction method: {dict(methods)}")

    print("\nCoverage of key topics:")
    missing = 0
    for term in KEY_TERMS:
        n = sum(term.lower() in c["text"].lower() for c in chunks)
        mark = "OK " if n else "MISSING"
        missing += not n
        print(f"  [{mark}] {term:<22} {n} chunks")
    print("\nAll key topics found - ready for Phase 2!" if not missing
          else f"\n{missing} topic(s) missing - check OCR output / URLs before Phase 2.")


if __name__ == "__main__":
    main()
