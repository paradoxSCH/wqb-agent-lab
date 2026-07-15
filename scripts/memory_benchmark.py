from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.alpha_memory.evaluation import run_retrieval_benchmark
from src.alpha_memory.store import SQLiteMemoryStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the memory retriever against a labeled query set.")
    parser.add_argument("--db", default=".local/data/memory/alpha_memory.db")
    parser.add_argument("--cases", default="tests/fixtures/memory_retrieval_benchmark.zh.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-recall", type=float, default=0.7)
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    report = run_retrieval_benchmark(SQLiteMemoryStore(args.db), cases, top_k=args.top_k)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["metrics"]["recall_at_k"] >= args.min_recall else 2


if __name__ == "__main__":
    raise SystemExit(main())
