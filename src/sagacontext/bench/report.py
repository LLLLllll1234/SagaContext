from __future__ import annotations
from collections import defaultdict
from .models import CaseResult

def _ratio(hit: int, total: int) -> str: return "n/a" if total == 0 else f"{hit / total:.3f}"

def markdown(results: list[CaseResult]) -> str:
    grouped = defaultdict(list)
    for result in results: grouped[result.system].append(result)
    kinds = sorted({result.dataset_kind for result in results})
    title = "# SagaContext Benchmark Report\n\n"
    warning = "> This report contains smoke data. Smoke values verify the runner and are not product-quality measurements.\n\n" if "smoke" in kinds else ""
    lines = ["| System | Preference adherence | Evolution accuracy | False injection rate | Fact recall | Task resume | Block false-positive rate |", "|---|---:|---:|---:|---:|---:|---:|"]
    for system, rows in sorted(grouped.items()):
        sums = {field: sum(getattr(row, field) for row in rows) for field in ("follow_hits", "follow_total", "evolution_correct", "evolution_total", "false_injections", "injected_total", "recall_hits", "recall_total", "task_correct", "task_total", "false_blocks", "block_total")}
        project = [row for row in rows if row.category == "project_fact"]
        fact_hits = sum(row.recall_hits for row in project); fact_total = sum(row.recall_total for row in project)
        lines.append(f"| {system} | {_ratio(sums['follow_hits'], sums['follow_total'])} | {_ratio(sums['evolution_correct'], sums['evolution_total'])} | {_ratio(sums['false_injections'], sums['injected_total'])} | {_ratio(fact_hits, fact_total)} | {_ratio(sums['task_correct'], sums['task_total'])} | {_ratio(sums['false_blocks'], sums['block_total'])} |")
    return title + warning + "\n".join(lines) + "\n"
