from __future__ import annotations
from .models import BenchmarkCase, CaseResult, Observation

def judge(case: BenchmarkCase, system: str, observed: Observation) -> CaseResult:
    expected = set(case.expected_recall); recalled = set(observed.recalled)
    forbidden = set(case.forbidden_recall)
    followed = set(observed.followed); expected_follow = set(case.expected_follow)
    return CaseResult(case_id=case.id, system=system, dataset_kind=case.dataset_kind, category=case.category,
        recall_hits=len(expected & recalled), recall_total=len(expected), false_injections=len(forbidden & recalled),
        injected_total=len(recalled), follow_hits=len(expected_follow & followed), follow_total=len(expected_follow),
        evolution_correct=int(case.expected_relation is not None and observed.relation == case.expected_relation),
        evolution_total=int(case.expected_relation is not None),
        task_correct=int(case.expected_task is not None and observed.resumed_task == case.expected_task),
        task_total=int(case.expected_task is not None),
        false_blocks=int(case.expected_block is False and observed.blocked is True),
        block_total=int(case.expected_block is False))
