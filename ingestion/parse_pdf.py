"""
Step 2 - Extract text from every PDF in data/raw/pdfs (including data/raw/pdfs/web).

Pages with a real text layer are read directly (free).
Scanned pages are rendered to images and sent to Gemini for OCR, several pages per
request (to save quota). Every OCR'd page is cached in data/processed/ocr_cache, so
re-running only processes pages that are still missing.

If the daily Gemini quota runs out, the script stops OCR cleanly, saves everything it
has, and tells you to run it again later - it resumes where it stopped.

Run:  python ingestion/parse_pdf.py            (text + OCR)
      python ingestion/parse_pdf.py --no-ocr   (text only, to preview quickly)
"""
import argparse
import io
import json
import re
import time

import pypdfium2 as pdfium

from config import GEMINI_API_KEY, OCR_CACHE, OCR_MODEL, PDF_PAGES, RAW_PDFS, should_skip_pdf

MIN_TEXT_CHARS = 100        # fewer characters than this => treat page as scanned
OCR_DPI_SCALE = 2.0         # ~144 dpi: enough for printed text, keeps uploads small
PAGES_PER_REQUEST = 5       # 5 pages per Gemini call => ~5x fewer requests
DELAY_BETWEEN_CALLS = 8     # seconds; stays under free-tier requests-per-minute

OCR_PROMPT = """You are an OCR engine. The images above are pages of a university student
handbook. Each image is preceded by its label "PAGE <number>".
Transcribe ALL text on every page exactly as written, as GitHub-flavoured Markdown:
- Use '#' headings for section titles and keep section numbers (e.g. '4.1').
- Reproduce every table as a Markdown table with the same rows and columns.
- Keep bullet points as '- ' lists.
- If a page is blank or only a logo/photo, its text is: [NO TEXT]
If there are several pages, return a JSON list with one object per page:
{"page": <number from its label>, "markdown": "<that page's text>"}.
If there is only one page, return just its Markdown text.
No commentary."""


class QuotaExhausted(Exception):
    pass


def clean_text(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = text.strip()
    # drop the printed page number (it can come out first or last)
    lines = [ln.rstrip() for ln in text.split("\n")]
    if lines and re.fullmatch(r"\s*\d{1,3}\s*", lines[-1]):
        lines = lines[:-1]
    if lines and re.fullmatch(r"\s*\d{1,3}\s*", lines[0]):
        lines = lines[1:]
    # re-join lines that were wrapped mid-sentence ("...relaxation with\nrespect to...")
    joined = []
    for ln in lines:
        if (joined and joined[-1] and ln[:1].islower()
                and not joined[-1].endswith((".", ":", ";", "|"))):
            joined[-1] += " " + ln.strip()
        else:
            joined.append(ln)
    return "\n".join(joined).strip()


_client = None


def gemini_client():
    global _client
    if _client is None:
        from google import genai
        if not GEMINI_API_KEY:
            raise SystemExit("GEMINI_API_KEY missing in .env")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def cache_path(pdf_path, page_no):
    return OCR_CACHE / f"{pdf_path.stem}_p{page_no:03d}.md"


def page_png(pdf, page_index) -> bytes:
    image = pdf[page_index].render(scale=OCR_DPI_SCALE).to_pil()
    buf = io.BytesIO()
    image.convert("L").save(buf, format="PNG", optimize=True)   # greyscale = smaller upload
    return buf.getvalue()


OCR_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "page": {"type": "INTEGER"},
            "markdown": {"type": "STRING"},
        },
        "required": ["page", "markdown"],
    },
}


def call_gemini(contents, as_json: bool) -> str:
    """Call Gemini with retries. Raises QuotaExhausted if the quota is used up."""
    from google.genai import types

    config = types.GenerateContentConfig(temperature=0, max_output_tokens=32768)
    if as_json:
        config = types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=32768,
            response_mime_type="application/json",
            response_schema=OCR_SCHEMA,
        )

    quota_hits = 0
    for attempt in range(6):
        try:
            resp = gemini_client().models.generate_content(
                model=OCR_MODEL, contents=contents, config=config
            )
            return resp.text or ""
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                quota_hits += 1
                if quota_hits >= 2:            # still exhausted after a 65s wait => daily quota
                    raise QuotaExhausted(msg[:200])
                print("      rate limit hit - waiting 65s")
                time.sleep(65)
            else:                              # 503 high demand, timeouts, etc.
                wait = 20 * (attempt + 1)
                print(f"      {e.__class__.__name__}: {msg[:80]}... retrying in {wait}s")
                time.sleep(wait)
    raise RuntimeError("Gemini kept failing; try again later")


def parse_batch_response(text: str, page_numbers) -> dict[int, str]:
    """Turn Gemini's reply into {page_number: markdown}. JSON first, page markers as fallback."""
    found = {}
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            data = data.get("pages", [data])
        for item in data:
            n = int(item.get("page", -1))
            if n in page_numbers:
                found[n] = str(item.get("markdown", "")).strip()
        if found:
            return found
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        pass
    # fallback: lines like "=== PAGE 63 ===", "## Page 63", "**PAGE 63**"
    parts = re.split(r"^[\W_]*page\s+(\d+)[\W_]*$", text, flags=re.M | re.I)
    for i in range(1, len(parts) - 1, 2):
        n = int(parts[i])
        if n in page_numbers:
            found[n] = parts[i + 1].strip()
    if not found and len(page_numbers) == 1 and text.strip():
        found[page_numbers[0]] = text.strip()     # single page: the whole reply is the page
    return found


def ocr_pages(pdf, pdf_path, page_numbers) -> list[int]:
    """One Gemini request for these pages. Caches each page found; returns pages still missing."""
    from google.genai import types

    contents = []
    for n in page_numbers:
        contents.append(f"PAGE {n}")
        contents.append(types.Part.from_bytes(data=page_png(pdf, n - 1), mime_type="image/png"))
    contents.append(OCR_PROMPT)

    reply = call_gemini(contents, as_json=len(page_numbers) > 1)
    found = parse_batch_response(reply, page_numbers)
    for n, md in found.items():
        cache_path(pdf_path, n).write_text(md or "[NO TEXT]", encoding="utf-8")

    missing = [n for n in page_numbers if n not in found]
    if missing:
        debug = OCR_CACHE / f"_debug_{pdf_path.stem}_p{page_numbers[0]:03d}.txt"
        debug.write_text(reply, encoding="utf-8")
        print(f"      (pages {missing} not found in reply - raw reply saved to {debug.name})")
    return missing


def ocr_batch(pdf, pdf_path, page_numbers) -> None:
    missing = ocr_pages(pdf, pdf_path, page_numbers)
    # if the batch reply couldn't be split, do the missing pages one at a time
    for n in missing:
        time.sleep(DELAY_BETWEEN_CALLS)
        print(f"      retrying page {n} on its own ...")
        ocr_pages(pdf, pdf_path, [n])


def source_url_for(pdf_path) -> str | None:
    url_file = pdf_path.with_name(pdf_path.name + ".url")
    return url_file.read_text(encoding="utf-8").strip() if url_file.exists() else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-ocr", action="store_true", help="skip scanned pages")
    args = parser.parse_args()

    pdf_files = [p for p in sorted(RAW_PDFS.rglob("*.pdf")) if not should_skip_pdf(p.name)]
    skipped = [p.name for p in RAW_PDFS.rglob("*.pdf") if should_skip_pdf(p.name)]
    if skipped:
        print(f"Skipping {len(skipped)} PDFs on the skip list: {', '.join(sorted(skipped))}")
    if not pdf_files:
        raise SystemExit(f"No PDFs found in {RAW_PDFS}")

    records, quota_out, still_missing = [], False, 0
    for path in pdf_files:
        pdf = pdfium.PdfDocument(str(path))
        n_pages = len(pdf)
        url = source_url_for(path)

        # 1) read the text layer of every page
        texts = {}
        for i in range(n_pages):
            texts[i + 1] = clean_text(pdf[i].get_textpage().get_text_range())
        scanned = [n for n, t in texts.items() if len(t) < MIN_TEXT_CHARS]
        todo = [n for n in scanned if not cache_path(path, n).exists()]
        print(f"\n{path.name}: {n_pages} pages | {len(scanned)} scanned "
              f"({len(scanned) - len(todo)} already in OCR cache)")

        # 2) OCR the scanned pages that aren't cached yet, in batches
        if todo and not args.no_ocr and not quota_out:
            for b in range(0, len(todo), PAGES_PER_REQUEST):
                batch = todo[b:b + PAGES_PER_REQUEST]
                print(f"  OCR pages {batch} ...")
                try:
                    ocr_batch(pdf, path, batch)
                except QuotaExhausted:
                    quota_out = True
                    print("\n  !! Gemini quota used up for now. Progress is saved.")
                    break
                time.sleep(DELAY_BETWEEN_CALLS)

        # 3) build page records
        stats = {"text": 0, "ocr": 0, "empty": 0}
        for n in range(1, n_pages + 1):
            text, method = texts[n], "text"
            if n in scanned:
                method = "ocr"
                cp = cache_path(path, n)
                if cp.exists():
                    text = clean_text(cp.read_text(encoding="utf-8"))
                else:
                    text = ""
                    if not args.no_ocr:
                        still_missing += 1
            if not text or "[NO TEXT]" in text[:20]:
                stats["empty"] += 1
                continue
            stats[method] += 1
            records.append({"doc": path.name, "page": n, "text": text, "method": method, "url": url})
        pdf.close()
        print(f"  -> text layer: {stats['text']} | OCR: {stats['ocr']} | empty/missing: {stats['empty']}")

    with PDF_PAGES.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nSaved {len(records)} pages -> {PDF_PAGES}")

    if still_missing:
        print(f"\n{still_missing} scanned pages still need OCR. Run this script again later "
              f"(quota resets daily) - it continues from where it stopped.")
    elif not args.no_ocr:
        print("\nAll scanned pages are OCR'd.")


if __name__ == "__main__":
    main()
