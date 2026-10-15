"""
The RAG pipeline. Every question flows through these nodes:

    route ──► (small talk) ─────────────────────────────► answer directly
      │
      └──► retrieve ──► generate ──► cite ──► score ──► answer + citations + confidence

Test it from the terminal (run inside the backend folder):
    python -m app.rag "What is ATKT?"
    python -m app.rag            (interactive chat, keeps conversation history)
"""
import re
import sys
from dataclasses import dataclass, field

from . import gemini
from .db import close_pool, get_conn
from .settings import (CONF_HIGH, CONF_MEDIUM, CONTEXT_CHUNKS, HISTORY_TURNS,
                       MAX_PER_PAGE, RETRIEVE_CANDIDATES)

NO_ANSWER = "NO_ANSWER"

SYSTEM_PROMPT = f"""You are MPSTME ONE, the campus assistant for students of NMIMS Mukesh Patel
School of Technology Management & Engineering (MPSTME), Mumbai.

Rules:
1. Answer ONLY using the numbered sources provided. Never use outside knowledge.
2. After every fact, cite its source number in square brackets, e.g. [1] or [2][3].
3. If the sources do not contain the answer, reply with exactly: {NO_ANSWER}
4. Be concise and student-friendly: a direct answer first, then key details.
   Use bullet points for lists of rules or conditions, and a Markdown table if comparing values.
5. Quote numbers, percentages, deadlines and fees exactly as written in the sources.
6. If sources disagree, say so and cite both."""

NOT_FOUND_REPLY = ("I couldn't find this in the MPSTME documents I have access to. "
                   "Try rephrasing your question, or check with the MPSTME office.")

SMALL_TALK = [
    (re.compile(r"^\s*(hi+|hello+|hey+|hii+|good (morning|afternoon|evening)|namaste)\b[\s!.]*$", re.I),
     "Hi! I'm MPSTME ONE. Ask me about attendance, exams, ATKT, fees, placements, "
     "internships or anything else in the Student Resource Book."),
    (re.compile(r"^\s*(thanks|thank you|thx|ty|ok(ay)?|cool|great)\b[\s!.]*$", re.I),
     "You're welcome! Ask me anything else about MPSTME."),
    (re.compile(r"^\s*((who|what) are you|what can you do)\??\s*$", re.I),
     "I'm MPSTME ONE, an assistant that answers questions from the MPSTME Student Resource Book "
     "and the college website, with sources for every answer."),
]

HYBRID_SQL = """
WITH q AS (
    SELECT %(qvec)s::vector AS v,
           NULLIF(replace(plainto_tsquery('english', %(qtext)s)::text, '&', '|'), '')::tsquery AS t
),
vec AS (
    SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> q.v) AS r
    FROM chunks, q ORDER BY embedding <=> q.v LIMIT 20
),
kw AS (
    SELECT chunk_id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q.t) DESC) AS r
    FROM chunks, q WHERE q.t IS NOT NULL AND tsv @@ q.t
    ORDER BY ts_rank_cd(tsv, q.t) DESC LIMIT 20
)
SELECT c.chunk_id, c.source, c.source_type, c.page, c.url, c.section, c.text,
       1 - (c.embedding <=> q.v) AS similarity,
       COALESCE(1.0 / (60 + vec.r), 0) + COALESCE(1.0 / (60 + kw.r), 0) AS rrf
FROM chunks c
CROSS JOIN q
LEFT JOIN vec ON vec.chunk_id = c.chunk_id
LEFT JOIN kw  ON kw.chunk_id  = c.chunk_id
WHERE vec.chunk_id IS NOT NULL OR kw.chunk_id IS NOT NULL
ORDER BY rrf DESC
LIMIT %(k)s
"""


@dataclass
class RagState:
    question: str
    history: list[dict] = field(default_factory=list)   # [{"role": "user"|"assistant", "content": str}]
    route: str = "rag"
    search_query: str = ""
    chunks: list[dict] = field(default_factory=list)
    raw_answer: str = ""
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    confidence: dict = field(default_factory=dict)


# ---------------------------------------------------------------- nodes

def route_node(s: RagState) -> RagState:
    for pattern, reply in SMALL_TALK:
        if pattern.search(s.question):
            s.route, s.answer = "small_talk", reply
            s.confidence = {"level": "none", "score": None, "label": "General reply"}
            return s
    return s


def retrieve_node(s: RagState) -> RagState:
    # Short follow-ups ("what about MBA Tech?") are searched together with the previous question
    s.search_query = s.question
    prev_user = [m["content"] for m in s.history if m["role"] == "user"]
    if prev_user and len(s.question.split()) <= 8:
        s.search_query = f"{prev_user[-1]} {s.question}"

    qvec = gemini.to_pgvector(gemini.embed_query(s.search_query))
    with get_conn() as conn:
        rows = conn.execute(HYBRID_SQL, {"qvec": qvec, "qtext": s.search_query,
                                         "k": RETRIEVE_CANDIDATES}).fetchall()

    # keep at most MAX_PER_PAGE chunks from the same page, then the best CONTEXT_CHUNKS
    per_page, picked = {}, []
    for r in rows:
        key = (r["source"], r["page"] or r["url"])
        if per_page.get(key, 0) >= MAX_PER_PAGE:
            continue
        per_page[key] = per_page.get(key, 0) + 1
        picked.append(r)
        if len(picked) == CONTEXT_CHUNKS:
            break
    s.chunks = picked
    return s


def _source_label(c: dict) -> str:
    if c["page"]:
        return f"{c['source']}, page {c['page']}"
    return f"{c['source']} ({c['url']})"


def generate_node(s: RagState) -> RagState:
    if not s.chunks:
        s.raw_answer = NO_ANSWER
        return s
    sources = "\n\n".join(
        f"[{i}] {_source_label(c)}" + (f" - section: {c['section']}" if c["section"] else "") + f"\n{c['text']}"
        for i, c in enumerate(s.chunks, 1)
    )
    recent = s.history[-HISTORY_TURNS * 2:]
    convo = "\n".join(f"{m['role'].capitalize()}: {m['content'][:600]}" for m in recent)
    prompt = (
        (f"Conversation so far:\n{convo}\n\n" if convo else "")
        + f"Sources:\n{sources}\n\n"
        + f"Student's question: {s.question}"
    )
    s.raw_answer = gemini.generate(SYSTEM_PROMPT, prompt)
    return s


def cite_node(s: RagState) -> RagState:
    if NO_ANSWER in s.raw_answer[:40] or not s.raw_answer:
        s.answer, s.citations = NOT_FOUND_REPLY, []
        return s
    s.answer = s.raw_answer
    used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", s.answer) if 1 <= int(n) <= len(s.chunks)})
    for n in used:
        c = s.chunks[n - 1]
        s.citations.append({
            "n": n,
            "source": c["source"],
            "page": c["page"],
            "url": c["url"],
            "section": c["section"],
            "similarity": round(float(c["similarity"]), 3),
            "snippet": re.sub(r"\s+", " ", re.sub(r"[#*`|]+|-{3,}", " ", c["text"])).strip()[:280],
        })
    return s


def score_node(s: RagState) -> RagState:
    if not s.citations:
        s.confidence = {"level": "low", "score": 0.0, "label": "Not found in sources"}
        return s
    best = max(c["similarity"] for c in s.citations)
    if best >= CONF_HIGH:
        level, label = "high", "High confidence"
    elif best >= CONF_MEDIUM:
        level, label = "medium", "Medium confidence"
    else:
        level, label = "low", "Low confidence - please verify"
    s.confidence = {"level": level, "score": round(best, 3), "label": label}
    return s


# ---------------------------------------------------------------- graph

def run(question: str, history: list[dict] | None = None) -> RagState:
    s = route_node(RagState(question=question.strip(), history=history or []))
    if s.route == "small_talk":
        return s
    for node in (retrieve_node, generate_node, cite_node, score_node):
        s = node(s)
    return s


def _print(s: RagState) -> None:
    print(f"\n{s.answer}\n")
    print(f"[{s.confidence.get('label')}"
          + (f", score {s.confidence['score']}" if s.confidence.get("score") else "") + "]")
    for c in s.citations:
        where = f"p.{c['page']}" if c["page"] else c["url"]
        print(f"  [{c['n']}] {c['source']} {where}  (similarity {c['similarity']})")


def _cli() -> None:
    if len(sys.argv) > 1:
        _print(run(" ".join(sys.argv[1:])))
    else:
        print("MPSTME ONE - type a question (or 'exit')")
        history: list[dict] = []
        while True:
            try:
                q = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("exit", "quit"):
                break
            if not q:
                continue
            result = run(q, history)
            _print(result)
            history += [{"role": "user", "content": q}, {"role": "assistant", "content": result.answer}]


if __name__ == "__main__":
    try:
        _cli()
    finally:
        close_pool()