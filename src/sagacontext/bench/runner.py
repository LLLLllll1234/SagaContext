from __future__ import annotations
import json
from pathlib import Path
import yaml
from .adapters import Adapter
from .judges import judge
from .models import BenchmarkCase, CaseResult

def load_cases(path: Path) -> list[BenchmarkCase]:
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    cases = []
    for file in files:
        payload = yaml.safe_load(file.read_text()) or []
        entries = payload if isinstance(payload, list) else payload.get("cases", [payload])
        cases.extend(BenchmarkCase.model_validate(entry) for entry in entries)
    return cases

def run(cases: list[BenchmarkCase], adapters: list[Adapter]) -> list[CaseResult]:
    return [judge(case, adapter.name, adapter.run(case)) for adapter in adapters for case in cases]

def write_jsonl(results: list[CaseResult], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(result.model_dump(), ensure_ascii=True) + "\n" for result in results))
