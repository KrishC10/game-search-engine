"""Evaluate search quality on tip-of-the-tongue requests with known answers.

Every request runs through each mode; the report gives top-1, top-5 and top-10
accuracy and mean reciprocal rank (MRR@10), and each run is appended to results.md.

    python -m eval.eval --check                       # are all answers in the index?
    python -m eval.eval --split tune --note "baseline"
    python -m eval.eval --split holdout --note "final" # run once, at the very end

The split is fixed by a hash of each request id: about 70% tune, 30% holdout.
Tune on "tune" only, so the holdout score stays honest.
"""
import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

from api.es import INDEX, make_client
from api.find import find
from api.models import Mode, SearchParams
from api.search import search

EVAL_DIR = Path(__file__).resolve().parent
REQUESTS_PATH = EVAL_DIR / "requests.json"
RESULTS_PATH = EVAL_DIR / "results.md"
TOP_K = 10
HOLDOUT_SHARE = 0.3

MODES: Dict[str, str] = {
    "keyword": "Keyword",
    "semantic": "Semantic",
    "hybrid": "Hybrid",
    "find": "Hybrid + reviews (no AI)",
    "find_ai": "Hybrid + AI query step",
}


def split_of(request_id: str) -> str:
    bucket = int(hashlib.sha256(request_id.encode()).hexdigest(), 16) % 100
    return "holdout" if bucket < HOLDOUT_SHARE * 100 else "tune"


def runner(es, mode: str) -> Callable[[str], List[str]]:
    """A function from request text to the top TOP_K app ids for one mode."""
    if mode in ("find", "find_ai"):
        return lambda text: [r.app_id for r in find(es, text, use_ai=mode == "find_ai").results]
    search_mode = Mode(mode)
    return lambda text: [r.app_id for r in search(es, SearchParams(q=text, mode=search_mode, size=TOP_K)).results]


def rank_of(answer: str, ranked: List[str]) -> Optional[int]:
    return ranked.index(answer) + 1 if answer in ranked else None


def metrics(ranks: List[Optional[int]]) -> Dict[str, float]:
    n = len(ranks) or 1
    return {
        "top1": sum(r is not None and r <= 1 for r in ranks) / n,
        "top5": sum(r is not None and r <= 5 for r in ranks) / n,
        "top10": sum(r is not None and r <= 10 for r in ranks) / n,
        "mrr": sum(1 / r for r in ranks if r is not None) / n,
    }


def check(es, requests: List[dict]) -> None:
    missing = [r for r in requests if not es.exists(index=INDEX, id=r["answer_app_id"])]
    for r in missing:
        print(f"MISSING  {r['id']}: {r['answer_name']} ({r['answer_app_id']}) is not in the index")
    print(f"{len(requests) - len(missing)}/{len(requests)} answers are in the index.")
    if missing:
        sys.exit("Remove or fix the missing answers before evaluating.")


def table(rows: Dict[str, Dict[str, float]]) -> str:
    lines = ["| Mode | Top-1 | Top-5 | Top-10 | MRR |", "|---|---|---|---|---|"]
    for mode, m in rows.items():
        lines.append(f"| {MODES[mode]} | {m['top1']:.0%} | {m['top5']:.0%} | {m['top10']:.0%} | {m['mrr']:.3f} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["tune", "holdout", "all"], default="tune")
    parser.add_argument("--modes", nargs="+", choices=list(MODES), default=list(MODES))
    parser.add_argument("--note", default="", help="what changed since the last run (goes in results.md)")
    parser.add_argument("--check", action="store_true", help="only check that every answer is in the index")
    parser.add_argument("--verbose", action="store_true", help="print every miss")
    parser.add_argument("--no-record", action="store_true", help="do not append to results.md")
    args = parser.parse_args()

    es = make_client()
    requests = json.loads(REQUESTS_PATH.read_text())
    if args.check:
        check(es, requests)
        return
    if args.split != "all":
        requests = [r for r in requests if split_of(r["id"]) == args.split]
    if args.split == "holdout":
        print("Running the holdout set. Do this once, at the end, or it stops being a fair test.\n")
    if "find_ai" in args.modes:
        print(f"find_ai calls the LLM once per request ({len(requests)} calls).\n")

    rows = {}
    for mode in args.modes:
        run = runner(es, mode)
        ranks = []
        for r in requests:
            rank = rank_of(r["answer_app_id"], run(r["request"]))
            ranks.append(rank)
            if args.verbose and rank != 1:
                print(f"  {mode:8} {'miss' if rank is None else f'rank {rank}':8} {r['id']}: {r['answer_name']}")
        rows[mode] = metrics(ranks)

    report = table(rows)
    print(f"\n{len(requests)} requests, split: {args.split}\n\n{report}")
    if not args.no_record:
        heading = f"## {date.today().isoformat()} · {args.split} · {len(requests)} requests"
        note = f"\n\n{args.note}" if args.note else ""
        with RESULTS_PATH.open("a") as f:
            f.write(f"\n{heading}{note}\n\n{report}\n")
        print(f"\nRecorded in {RESULTS_PATH.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    main()
