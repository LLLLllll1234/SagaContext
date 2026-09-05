from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class BackendCapabilities(BaseModel):
    backend: str
    version: str
    stable_id_mapping: bool
    enumerable_managed_area: bool
    visibility_check: bool
    exactly_once: bool = False


class BackendHit(BaseModel):
    memory_id: str
    revision: int
    generation: str
    rank: int
    score: float | None = None
    backend_locator: str


class Projection(BaseModel):
    owner_id: str
    memory_id: str
    revision: int
    generation: str
    memory_type: str
    searchable_text: str
    scope_filter_tags: list[str]
    payload_digest: str


class BackendAdapter(Protocol):
    def capabilities(self) -> BackendCapabilities: ...
    def search(self, query: str, generation: str, limit: int) -> list[BackendHit]: ...
    def materialize(self, projection: Projection, operation_key: str) -> str: ...
    def locate_projection(self, memory_id: str, revision: int, generation: str) -> str | None: ...
    def remove_projection(self, locators: list[str]) -> int: ...
