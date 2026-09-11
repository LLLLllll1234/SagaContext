from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict


T = TypeVar("T")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(_StrictModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class SnapshotMeta(_StrictModel):
    observed_at: datetime
    schema_version: int
    ledger_sequence: int
    request_id: str
