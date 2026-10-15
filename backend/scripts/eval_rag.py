"""
Phase 5 - Evaluate the full RAG pipeline (retrieval + Gemini answers).

For every question in data/eval/questions.csv (+ a few out-of-scope questions):
  1. runs the pipeline and saves the answer         -> data/eval/rag_results.jsonl
  2. asks Gemini to grade each answer vs the expected answer (correct / partial / wrong)
  3. prints accuracy, citation accuracy, refusal behaviour and suggested confidence thresholds

Results are cached, so you can stop any time (or run out of quota) and re-run to continue.

Run inside the backend folder:
    python scripts/eval_rag.py              (all questions)
    python scripts/eval_rag.py --limit 10   (only 10 new questions this run)
    python scripts/eval_rag.py --report     (just print the report from saved results)
"""
import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # so "app" can be imported

from app import db, gemini, rag                                  # noqa: E402
from app.gemini import GeminiUnavailable                         # noqa: E402
from app.settings import CONF_HIGH, CONF_MEDIUM, EVAL_DIR        # noqa: E402

QUESTIONS = EVAL_DIR / "questions.csv"
RESULTS = EVAL_DIR / "rag_results.jsonl"
DELAY = 5            # seconds between Gemini calls (free-tier friendly)

# The bot should say it doesn't know for these
OUT_OF_SCOPE = [
    "What is the capital of Australia?",
    "Write me a Python function to reverse a string.",
    "What is the hostel fee at IIT Bombay?",
    "Who won the cricket world cup in 2011?",
]

JUDGE_SYSTEM = """You grade a university chatbot. For each item, compare the chatbot's answer
with the expected answer. Grade:
- "correct": contains the key facts of the expected answer (wording may differ; extra correct detail is fine)
- "partial": some key facts present but something important is missing or vague
- "wrong": key facts missing, contradicted, or the bot said it could not find the answer"""

JUDGE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "verdict": {"type": "STRING", "enum": ["correct", "partial", "wrong"]},
            "reason": {"type": "STRING"},
        },
        "required": ["id", "verdict"],
    },
}


def load_cases() -> list[dict]:
    cases = [{"question": r["question"], "expected": r["expected_answer"],
              "source": r["source"], "in_scope": True}
             for r in csv.DictReader(QUESTIONS.open(encoding="utf-8"))]
    cases += [{"question": q, "expected": "", "source": "", "in_scope": False} for q in OUT_OF_SCOPE]
    return cases


def load_results() -> dict[str, dict]:
    if not RESULTS.exists():
        return {}
    rows = [json.loads(l) for l in RESULTS.open(encoding="utf-8") if l.strip()]
    return {r["question"]: r for r in rows}


def save_results(results: dict[str, dict]) -> None:
    with RESULTS.open("w", encoding="utf-8") as f:
        for r in results.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def expected_page(source: str):
    m = re.search(r"(\w+)\s+p\.?\s*(\d+)", source or "")
    return (m.group(1).lower(), int(m.group(2))) if m else None


def answer_questions(cases, results, limit) -> None:
    todo = [c for c in cases if c["question"] not in results][:limit]
    print(f"Answering {len(todo)} new questions ({len(results)} already saved)...\n")
    for i, c in enumerate(todo, 1):
        try:
            s = rag.run(c["question"])
        except GeminiUnavailable as e:
            print(f"\n!! {e} Progress saved - run again later.")
            break
        exp = expected_page(c["source"])
        cited_pages = [(x["source"].lower(), x["page"]) for x in s.citations]
        results[c["question"]] = {
            **c,
            "answer": s.answer,
            "no_answer": s.answer == rag.NOT_FOUND_REPLY,
            "confidence": s.confidence,
            "citations": [f"{x['source']} p.{x['page']}" if x["page"] else x["url"] for x in s.citations],
            "citation_hit": bool(exp) and any(exp[0] in src and pg == exp[1] for src, pg in cited_pages),
            "verdict": None,
        }
        save_results(results)
        status = "NOT FOUND" if results[c["question"]]["no_answer"] else s.confidence.get("level", "")
        print(f"  [{i}/{len(todo)}] {status:9} | {c['question'][:70]}")
        time.sleep(DELAY)


def judge(results) -> None:
    pending = [r for r in results.values() if r["in_scope"] and r["verdict"] is None]
    for r in results.values():                       # out-of-scope: correct if the bot refused
        if not r["in_scope"] and r["verdict"] is None:
            r["verdict"] = "correct" if r["no_answer"] else "wrong"
    if not pending:
        save_results(results)
        return
    print(f"\nGrading {len(pending)} answers with Gemini...")
    for b in range(0, len(pending), 10):
        batch = pending[b:b + 10]
        items = "\n\n".join(
            f"id: {i}\nquestion: {r['question']}\nexpected answer: {r['expected']}\nchatbot answer: {r['answer']}"
            for i, r in enumerate(batch))
        try:
            reply = gemini.generate(JUDGE_SYSTEM, items, temperature=0, json_schema=JUDGE_SCHEMA)
            grades = {g["id"]: g for g in json.loads(reply)}
        except GeminiUnavailable as e:
            print(f"!! {e} Run again later to finish grading.")
            break
        except (json.JSONDecodeError, KeyError, TypeError):
            print("  (could not read grades for one batch - will retry next run)")
            continue
        for i, r in enumerate(batch):
            if i in grades:
                r["verdict"] = grades[i]["verdict"]
                r["judge_reason"] = grades[i].get("reason", "")
        save_results(results)
        time.sleep(DELAY)


def suggest_threshold(points, target):
    """Lowest similarity s such that answers with score >= s are right at least `target` of the time."""
    best = None
    ok = 0
    for n, (score, right) in enumerate(sorted(points, reverse=True), 1):
        ok += right
        if ok / n >= target:
            best = score
    return best


def report(results) -> None:
    rows = [r for r in results.values() if r.get("verdict")]
    ins = [r for r in rows if r["in_scope"]]
    outs = [r for r in rows if not r["in_scope"]]
    if not ins:
        print("No graded results yet.")
        return

    count = {v: sum(r["verdict"] == v for r in ins) for v in ("correct", "partial", "wrong")}
    n = len(ins)
    print("\n" + "=" * 64)
    print(f"ANSWER QUALITY  ({n} in-scope questions)")
    print(f"  correct {count['correct']} ({count['correct'] / n:.0%})   "
          f"partial {count['partial']} ({count['partial'] / n:.0%})   wrong {count['wrong']} ({count['wrong'] / n:.0%})")
    with_page = [r for r in ins if expected_page(r["source"])]
    if with_page:
        hits = sum(r["citation_hit"] for r in with_page)
        print(f"  cited the expected page: {hits}/{len(with_page)} ({hits / len(with_page):.0%})")
    missed = sum(r["no_answer"] for r in ins)
    print(f"  said 'not found' on an answerable question: {missed}")
    if outs:
        refused = sum(r["verdict"] == "correct" for r in outs)
        print(f"\nOUT-OF-SCOPE  refused correctly: {refused}/{len(outs)}")

    print(f"\nCONFIDENCE vs CORRECTNESS  (current thresholds: high >= {CONF_HIGH}, medium >= {CONF_MEDIUM})")
    for level in ("high", "medium", "low"):
        grp = [r for r in ins if r["confidence"].get("level") == level and not r["no_answer"]]
        if grp:
            ok = sum(r["verdict"] == "correct" for r in grp)
            print(f"  {level:6}: {len(grp):3} answers, {ok / len(grp):.0%} correct")

    points = [(r["confidence"]["score"], r["verdict"] == "correct")
              for r in ins if not r["no_answer"] and r["confidence"].get("score")]
    hi, med = suggest_threshold(points, 0.90), suggest_threshold(points, 0.70)
    if hi:
        print("\nSuggested thresholds (put these in your .env):")
        print(f"  CONF_HIGH={hi:.2f}      # answers at/above this were right >= 90% of the time")
        if med and med < hi:
            print(f"  CONF_MEDIUM={med:.2f}    # answers at/above this were right >= 70% of the time")

    bad = [r for r in ins if r["verdict"] != "correct"]
    if bad:
        print("\nNOT FULLY CORRECT - look at these:")
        for r in bad:
            print(f"\n  Q: {r['question']}   [{r['verdict']}]")
            print(f"     expected: {r['expected']}  ({r['source']})")
            got = re.sub(r"\s+", " ", r["answer"])[:220]
            print(f"     got:      {got}")
            print(f"     cited:    {', '.join(r['citations']) or '-'}")
            if r.get("judge_reason"):
                print(f"     judge:    {r['judge_reason']}")
    print("=" * 64)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="delete saved results and start over")
    args = parser.parse_args()

    if args.fresh and RESULTS.exists():
        RESULTS.unlink()
    results = load_results()
    if not args.report:
        answer_questions(load_cases(), results, args.limit)
        judge(results)
    report(results)


if __name__ == "__main__":
    try:
        main()
    finally:
        db.close_pool()