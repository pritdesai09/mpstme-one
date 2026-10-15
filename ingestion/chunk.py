"""
Step 3 - Split web pages and PDF pages into overlapping chunks with metadata.

Input:  data/processed/web_pages.jsonl, data/processed/pdf_pages.jsonl
Output: data/processed/chunks.jsonl   (this is what Phase 2 embeds)

Run:  python ingestion/chunk.py
"""
import hashlib
import json
import re

from config import CHUNK_OVERLAP, CHUNK_SIZE, CHUNKS, PDF_PAGES, WEB_PAGES

HEADING = re.compile(r"^(#{1,4}\s+.+|\d+(\.\d+)*\.?\s+[A-Z][^\n]{2,80})$")


def read_jsonl(path):
    if not path.exists():
        print(f"  (no {path.name} yet - skipping)")
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def split_long(paragraph: str, size: int) -> list[str]:
    """Split one oversized paragraph on sentence boundaries, then hard-cut if needed."""
    sentences = re.split(r"(?<=[.!?;])\s+", paragraph)
    parts, buf = [], ""
    for s in sentences:
        while len(s) > size:                      # a single giant "sentence" (e.g. a table)
            parts.append(s[:size])
            s = s[size:]
        if len(buf) + len(s) + 1 > size and buf:
            parts.append(buf.strip())
            buf = ""
        buf += s + " "
    if buf.strip():
        parts.append(buf.strip())
    return parts


def chunk_text(text: str, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP) -> list[tuple[str, str]]:
    """Return list of (section_heading, chunk_text). Paragraph-aware with overlap."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n|\n(?=#)", text) if b.strip()]
    # if the text has no blank lines (common in PDFs), fall back to single lines
    if len(blocks) <= 1:
        blocks = [b.strip() for b in text.split("\n") if b.strip()]

    pieces = []
    for b in blocks:
        pieces.extend(split_long(b, size) if len(b) > size else [b])

    chunks, current, section, chunk_section = [], [], "", ""
    for p in pieces:
        first_line = p.split("\n")[0].strip()
        if HEADING.match(first_line):
            section = first_line.lstrip("# ").strip()[:100]
        if current and sum(len(x) + 1 for x in current) + len(p) > size:
            chunks.append((chunk_section, "\n".join(current)))
            # carry the tail of the previous chunk forward as overlap
            tail = "\n".join(current)[-overlap:]
            tail = tail[tail.find(" ") + 1:] if " " in tail else tail
            current = [tail] if overlap else []
            chunk_section = section
        if not current:
            chunk_section = section
        current.append(p)
    if current:
        chunks.append((chunk_section, "\n".join(current)))
    return [(s, c.strip()) for s, c in chunks if len(c.strip()) > 40]


def make_id(*parts) -> str:
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12]


def main() -> None:
    out = []

    for page in read_jsonl(WEB_PAGES):
        for i, (section, text) in enumerate(chunk_text(page["text"])):
            out.append({
                "chunk_id": make_id(page["url"], i),
                "source_type": "web",
                "source": page["title"] or page["url"],
                "url": page["url"],
                "page": None,
                "section": section,
                # prefixing the title helps retrieval ("Placements: ...")
                "text": f"{page['title']}\n{text}" if page["title"] else text,
            })

    # PDF: chunk each page separately so every chunk has an exact page number to cite
    for page in read_jsonl(PDF_PAGES):
        for i, (section, text) in enumerate(chunk_text(page["text"])):
            out.append({
                "chunk_id": make_id(page["doc"], page["page"], i),
                "source_type": "pdf",
                "source": page["doc"],
                "url": page.get("url"),
                "page": page["page"],
                "section": section,
                "method": page["method"],
                "text": text,
            })

    with CHUNKS.open("w", encoding="utf-8") as f:
        for c in out:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    sizes = [len(c["text"]) for c in out] or [0]
    web = sum(c["source_type"] == "web" for c in out)
    print(f"Chunks: {len(out)}  (web: {web}, pdf: {len(out) - web})")
    print(f"Size (chars): min {min(sizes)}, avg {sum(sizes) // len(sizes)}, max {max(sizes)}")
    print(f"Saved -> {CHUNKS}")


if __name__ == "__main__":
    main()
