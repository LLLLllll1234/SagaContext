from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

class Observation(BaseModel):
    recalled: list[str] = Field(default_factory=list)
    followed: list[str] = Field(default_factory=list)
    relation: str | None = None
    resumed_task: str | None = None

class BenchmarkCase(BaseModel):
    id: str
    dataset_kind: Literal["smoke", "labeled"] = "smoke"
    category: Literal["preference", "evolution", "project_fact", "task_resume"]
    expected_recall: list[str] = Field(default_factory=list)
    forbidden_recall: list[str] = Field(default_factory=list)
    expected_follow: list[str] = Field(default_factory=list)
    expected_relation: str | None = None
    expected_task: str | None = None
    observations: dict[str, Observation] = Field(default_factory=dict)

class CaseResult(BaseModel):
    case_id: str
    system: str
    dataset_kind: str
    category: str
    recall_hits: int = 0
    recall_total: int = 0
    false_injections: int = 0
    injected_total: int = 0
    follow_hits: int = 0
    follow_total: int = 0
    evolution_correct: int = 0
    evolution_total: int = 0
    task_correct: int = 0
    task_total: int = 0
