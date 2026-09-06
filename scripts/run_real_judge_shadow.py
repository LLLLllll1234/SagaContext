"""Run the frozen Judge replay in an isolated, non-writing shadow environment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sagacontext.bench.admission import admission_errors
from sagacontext.bench.real_judge import (
    build_adapter,
    load_replay_dataset,
    markdown_report,
    run_replay,
    write_results,
)
from sagacontext.config import Config
from sagacontext.ledger import Ledger


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _slug(model: str) -> str:
    return model.replace("/", "-").replace(".", "-")


def run_model(
    dataset_path: Path,
    output_dir: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    attempts: int,
) -> dict[str, object]:
    dataset = load_replay_dataset(dataset_path)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_slug(model)}"
    model_dir = output_dir / _slug(model)
    model_dir.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="sagacontext-shadow-") as temp_root:
        temp_path = Path(temp_root)
        input_copy = temp_path / "shadow-input.yaml"
        input_copy.write_bytes(dataset_path.read_bytes())
        namespace = f"viking://user/shadow-{run_id}/memories/sagacontext/{run_id}"
        ledger = Ledger(temp_path / "shadow-ledger.db", owner_id=f"shadow-{run_id}")
        try:
            workspace = ledger.register_project("isolated-shadow", temp_path)
            before = ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            adapter = build_adapter(base_url, api_key, model, timeout=timeout)
            results = run_replay(dataset, adapter, repeats=3, max_attempts=attempts, run_id=run_id)
            after = ledger.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            result_path = model_dir / "replay.jsonl"
            report_path = model_dir / "report.md"
            write_results(results, result_path)
            report_path.write_text(markdown_report(results))
            run_manifest = {
                "run_id": run_id,
                "model": model,
                "mode": "isolated_shadow",
                "shadow_only": True,
                "formal_memory_write": False,
                "recall_enabled": False,
                "injection_enabled": False,
                "dataset_digest": dataset.dataset_digest,
                "input_digest": _digest(input_copy.read_bytes().decode("utf-8")),
                "temporary_namespace": namespace,
                "temporary_ledger_created": True,
                "workspace_id": workspace["workspace_id"],
                "temporary_memory_count_before": before,
                "temporary_memory_count_after": after,
                "temporary_memory_write_observed": after != before,
                "cleanup": {"temporary_root_removed": False, "temporary_ledger_removed": False},
                "admission_errors": admission_errors(results, model=model),
                "artifact_files": ["replay.jsonl", "report.md", "manifest.json"],
            }
        finally:
            ledger.close()
    run_manifest["cleanup"] = {
        "temporary_root_removed": not temp_path.exists(),
        "temporary_ledger_removed": not (temp_path / "shadow-ledger.db").exists(),
    }
    (model_dir / "manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=True, indent=2) + "\n")
    return run_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("bench/cases/real_judge/cases-v3.yaml"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--models", nargs="+", default=["deepseek-v4-pro", "deepseek-v4-flash"])
    args = parser.parse_args()
    if args.timeout != 300.0 or args.attempts != 3:
        parser.error("shadow requires timeout=300 and attempts=3")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"refusing to write into non-empty output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = Config.load()
    base_url = os.environ.get("SAGACONTEXT_LLM_BASE_URL", config.llm_base_url)
    api_key = os.environ.get("SAGACONTEXT_LLM_API_KEY", config.llm_api_key)
    manifests = []
    for model in args.models:
        manifests.append(run_model(
            args.cases, args.output_dir,
            base_url=base_url, api_key=api_key, model=model,
            timeout=args.timeout, attempts=args.attempts,
        ))
    (args.output_dir / "shadow-manifest.json").write_text(
        json.dumps({
            "mode": "isolated_shadow",
            "models": manifests,
            "formal_memory_write": False,
            "recall_enabled": False,
            "injection_enabled": False,
        }, ensure_ascii=True, indent=2) + "\n"
    )
    print(args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
