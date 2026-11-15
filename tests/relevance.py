"""Relevance check against a loaded index: is the expected game in the top 3?

Run after every ranking change and record the pass rate in the README:
    python -m tests.relevance --mode keyword
    python -m tests.relevance --mode hybrid
"""
import argparse
import json
from pathlib import Path

from api.es import make_client
from api.models import Mode, SearchParams
from api.search import search

CASES_PATH = Path(__file__).with_name("relevance_cases.json")
TOP_N = 3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.keyword.value)
    args = parser.parse_args()

    es = make_client()
    cases = json.loads(CASES_PATH.read_text())
    passed = 0
    for case in cases:
        response = search(es, SearchParams(q=case["query"], mode=Mode(args.mode), size=TOP_N))
        ok = any(r.app_id in case["expect_any"] for r in response.results)
        passed += ok
        top = ", ".join(r.name for r in response.results) or "(no results)"
        print(f"{'PASS' if ok else 'FAIL'}  {case['query']!r:40} -> {top}")
    print(f"\n{args.mode}: {passed}/{len(cases)} passed ({passed / len(cases):.0%}) in the top {TOP_N}")


if __name__ == "__main__":
    main()
