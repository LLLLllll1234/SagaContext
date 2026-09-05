from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import Config
from .ledger import Ledger, TaskContext
from .maintenance import BatchService, BatchWorker, EventJournal, ReviewService
from .projection import Projector
from .recall_policy import RecallPolicy


class TaskContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    workspace_id: str | None = None
    task_id: str | None = None
    touched_paths: list[str] = Field(default_factory=list)
    stage: Literal["orient", "investigate", "implement", "verify"] = "orient"


class CurrentMemoryInput(BaseModel):
    memory_ids: list[str]
    context: TaskContextInput


class Application:
    def __init__(self, config: Config):
        self.config = config
        self.ledger = Ledger(config.ledger_path)
        self.event_journal = EventJournal(self.ledger)
        self.batches = BatchService(self.ledger)
        self.batch_worker = BatchWorker(self.ledger)
        self.reviews = ReviewService(self.ledger)
        self.projector = Projector(self.ledger)
        self.recall_policy = RecallPolicy(self.ledger)
        self._closed = False

    @property
    def owner_id(self) -> str:
        return self.ledger.owner_id

    def task_context(self, payload: TaskContextInput) -> TaskContext:
        return TaskContext(owner_id=self.owner_id, **payload.model_dump())

    def close(self) -> None:
        if not self._closed:
            self.ledger.close()
            self._closed = True

    def __enter__(self) -> "Application":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
