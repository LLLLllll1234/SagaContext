from __future__ import annotations

import argparse
import os
from pathlib import Path

from sagacontext.bench.real_judge import (
    build_adapter,
    load_replay_cases,
    markdown_report,
    run_replay,
    write_results,
)
from sagacontext.config import Config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real OpenAI-compatible Judge replay set")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("bench/cases/real_judge/cases.yaml"),
    )
    parser.add_argument("--jsonl", type=Path, default=Path("artifacts/real-judge/replay.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("artifacts/real-judge/report.md"))
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    cases = load_replay_cases(args.cases)
    config = Config.load()
    adapter = build_adapter(
        os.environ.get("SAGACONTEXT_LLM_BASE_URL", config.llm_base_url),
        os.environ.get("SAGACONTEXT_LLM_API_KEY", config.llm_api_key),
        os.environ.get("SAGACONTEXT_LLM_MODEL", config.llm_model),
        timeout=args.timeout,
    )
    results = run_replay(cases, adapter)
    write_results(results, args.jsonl)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(markdown_report(results))
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
