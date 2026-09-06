from __future__ import annotations

import argparse
import os
from pathlib import Path

from sagacontext.bench.real_judge import (
    build_adapter,
    load_replay_dataset,
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
        default=Path("bench/cases/real_judge/cases-v2.yaml"),
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--jsonl", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()

    for explicit_path in (args.jsonl, args.report):
        if explicit_path is not None and explicit_path.exists():
            parser.error(f"refusing to overwrite existing artifact: {explicit_path}")

    dataset = load_replay_dataset(args.cases)
    config = Config.load()
    adapter = build_adapter(
        os.environ.get("SAGACONTEXT_LLM_BASE_URL", config.llm_base_url),
        os.environ.get("SAGACONTEXT_LLM_API_KEY", config.llm_api_key),
        os.environ.get("SAGACONTEXT_LLM_MODEL", config.llm_model),
        timeout=args.timeout,
    )
    results = run_replay(dataset, adapter, repeats=args.repeats, max_attempts=args.attempts)
    output_dir = args.output_dir or Path("artifacts/real-judge") / results[0].run_id
    jsonl_path = args.jsonl or output_dir / "replay.jsonl"
    report_path = args.report or output_dir / "report.md"
    for output_path in (jsonl_path, report_path):
        if output_path.exists():
            parser.error(f"refusing to overwrite existing artifact: {output_path}")
    write_results(results, jsonl_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown_report(results))
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
