"""
Step 1 - Scrape the college website.

Reads data/sources/urls.txt, downloads each page, strips menus/footers/popups,
and saves clean text to data/processed/web_pages.jsonl.
Any PDF linked from those pages on the same site (placement reports, brochures)
is downloaded into data/raw/pdfs/web/ so parse_pdf.py can index it too.

Run:  python ingestion/scrape_web.py
"""
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from config import RAW_HTML, RAW_PDFS, URLS_FILE, WEB_PAGES, should_skip_pdf

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MPSTME-ONE-student-project/1.0"
}
DELAY_SECONDS = 1.5          # be polite to the college server
ALLOWED_DOMAINS = ("nmims.edu",)

# Page parts that are never useful content
NOISE_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "header", "footer", "nav", "aside"]
NOISE_PATTERN = re.compile(
    r"(menu|navbar|nav-|footer|header|breadcrumb|cookie|popup|modal|social|share|sidebar|banner|newsletter|enquir)",
    re.I,
)


def read_urls() -> list[str]:
    urls = []
    for line in URLS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


_robots: dict[str, RobotFileParser] = {}


def allowed_by_robots(url: str) -> bool:
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    if base not in _robots:
        rp = RobotFileParser()
        try:
            resp = requests.get(base + "/robots.txt", headers=HEADERS, timeout=15)
            rp.parse(resp.text.splitlines() if resp.ok else [])
        except requests.RequestException:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(HEADERS["User-Agent"], url)


def table_to_text(table) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, clean text) with headings kept as markdown '#'."""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.get_text(strip=True) if soup.title else "").split("|")[0].strip()

    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed:  # already removed together with its parent
            continue
        attrs = " ".join(tag.get("class", [])) + " " + (tag.get("id") or "")
        if NOISE_PATTERN.search(attrs) and tag.name not in ("body", "main", "html"):
            tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.body or soup
    lines = []
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "table"]):
        if el.find_parent("table") and el.name != "table":
            continue  # table text is handled by table_to_text
        if el.name == "table":
            text = table_to_text(el)
        else:
            text = el.get_text(" ", strip=True)
        if not text or len(text) < 3:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            text = "#" * int(el.name[1]) + " " + text
        elif el.name == "li":
            text = "- " + text
        lines.append(text)

    # drop exact duplicate lines (menus repeated in the body, etc.)
    seen, out = set(), []
    for line in lines:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return title, "\n".join(out)


def find_pdf_links(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        host = urlparse(href).netloc
        if href.lower().split("?")[0].endswith(".pdf") and host.endswith(ALLOWED_DOMAINS):
            links.add(href)
    return sorted(links)


def download_pdf(url: str) -> None:
    out_dir = RAW_PDFS / "web"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", urlparse(url).path.split("/")[-1]) or "file.pdf"
    if should_skip_pdf(name):
        print(f"    - skipping {name} (on skip list)")
        return
    target = out_dir / name
    if target.exists():
        print(f"    = already have {name}")
        return
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        target.write_bytes(resp.content)
        # remember where it came from, so citations can link back to it
        (out_dir / (name + ".url")).write_text(url, encoding="utf-8")
        print(f"    + downloaded {name} ({len(resp.content) // 1024} KB)")
    except requests.RequestException as e:
        print(f"    ! could not download {url}: {e}")


def main() -> None:
    urls = read_urls()
    print(f"Scraping {len(urls)} URLs...\n")
    pages, pdf_links = [], set()

    for url in urls:
        if not allowed_by_robots(url):
            print(f"[skip] robots.txt disallows {url}")
            continue
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[fail] {url}: {e}")
            continue

        html = resp.text
        page_id = hashlib.md5(url.encode()).hexdigest()[:10]
        (RAW_HTML / f"{page_id}.html").write_text(html, encoding="utf-8")

        title, text = html_to_text(html)
        words = len(text.split())
        flag = "  <-- very little text, check this page" if words < 80 else ""
        print(f"[ok]   {url}\n       title: {title!r} | {words} words{flag}")

        pages.append({
            "id": page_id,
            "url": url,
            "title": title,
            "text": text,
            "crawled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        pdf_links.update(find_pdf_links(html, url))
        time.sleep(DELAY_SECONDS)

    with WEB_PAGES.open("w", encoding="utf-8") as f:
        for p in pages:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"\nSaved {len(pages)} pages -> {WEB_PAGES}")

    if pdf_links:
        print(f"\nFound {len(pdf_links)} linked PDFs on these pages:")
        for link in pdf_links:
            download_pdf(link)
            time.sleep(DELAY_SECONDS)


if __name__ == "__main__":
    main()
