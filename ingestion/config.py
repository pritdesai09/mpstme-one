"""Shared paths and settings for the ingestion scripts."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]          # C:\Users\Prit\mpstme-one
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
URLS_FILE = DATA / "sources" / "urls.txt"
RAW_PDFS = DATA / "raw" / "pdfs"                    # SRB + PDFs downloaded from the website
RAW_HTML = DATA / "raw" / "html"                    # raw page copies (for debugging)
PROCESSED = DATA / "processed"
OCR_CACHE = PROCESSED / "ocr_cache"                 # so we never pay to OCR the same page twice

WEB_PAGES = PROCESSED / "web_pages.jsonl"
PDF_PAGES = PROCESSED / "pdf_pages.jsonl"
CHUNKS = PROCESSED / "chunks.jsonl"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OCR_MODEL = os.getenv("GEMINI_OCR_MODEL") or os.getenv("GEMINI_CHAT_MODEL", "gemini-3.5-flash")

# Chunking (characters, roughly 4 chars = 1 token)
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200

# PDFs linked on the website that are not useful for students' questions.
# Any PDF whose filename contains one of these (case-insensitive) is not downloaded or indexed.
SKIP_PDF_PATTERNS = [
    "feedback-forms",          # scanned filled-in feedback forms
    "naac_certificate",        # one-page certificate image
    "naac_quality_profile",
    "ssr.pdf",                 # NAAC Self Study Report: 116 pages of accreditation data
    "all-courses-brochure",    # covers every NMIMS school, would mix other schools' info in
]


def should_skip_pdf(filename: str) -> bool:
    name = filename.lower()
    return any(p in name for p in SKIP_PDF_PATTERNS)


for d in (RAW_PDFS, RAW_HTML, PROCESSED, OCR_CACHE):
    d.mkdir(parents=True, exist_ok=True)
