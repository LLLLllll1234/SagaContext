from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .base import BackendCapabilities, BackendHit, Projection


class BackendDefiniteError(RuntimeError):
    pass


class BackendUnknownError(TimeoutError):
    pass


class BackendVerificationTimeout(TimeoutError):
    pass


@dataclass
class InMemoryBackendState:
    items: dict[str, Projection] = field(default_factory=dict)
    operations: dict[str, str] = field(default_factory=dict)
    materialize_calls: int = 0
    locate_calls: int = 0


class InMemoryBackend:
    def __init__(
        self,
        state: InMemoryBackendState | None = None,
        *,
        materialize_fault: Literal["before_write", "after_write_timeout"] | None = None,
        locate_fault: Literal["timeout"] | None = None,
    ) -> None:
        self.state = state or InMemoryBackendState()
        self.materialize_fault = materialize_fault
        self.locate_fault = locate_fault

    @property
    def items(self) -> dict[str, Projection]:
        return self.state.items

    @property
    def operations(self) -> dict[str, str]:
        return self.state.operations

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend="memory-test-double",
            version="1",
            stable_id_mapping=True,
            enumerable_managed_area=True,
            visibility_check=True,
            exactly_once=True,
        )

    def materialize(self, projection: Projection, operation_key: str) -> str:
        self.state.materialize_calls += 1
        if self.materialize_fault == "before_write":
            self.materialize_fault = None
            raise BackendDefiniteError("injected failure before write")
        if operation_key in self.state.operations:
            return self.state.operations[operation_key]
        locator = f"memory://{projection.generation}/{projection.memory_id}/{projection.revision}"
        self.state.items[locator] = projection
        self.state.operations[operation_key] = locator
        if self.materialize_fault == "after_write_timeout":
            self.materialize_fault = None
            raise BackendUnknownError("injected timeout after write")
        return locator

    def locate_projection(
        self,
        memory_id: str,
        revision: int,
        generation: str,
        operation_key: str | None = None,
    ) -> str | None:
        self.state.locate_calls += 1
        if self.locate_fault == "timeout":
            self.locate_fault = None
            raise BackendVerificationTimeout("injected locate timeout")
        if operation_key and operation_key in self.state.operations:
            return self.state.operations[operation_key]
        suffix = f"/{memory_id}/{revision}"
        return next(
            (
                key
                for key in self.state.items
                if key.startswith(f"memory://{generation}/") and key.endswith(suffix)
            ),
            None,
        )

    def inspect_projection(self, locator: str) -> Projection | None:
        return self.state.items.get(locator)

    def search(self, query: str, generation: str, limit: int = 10) -> list[BackendHit]:
        matches = [
            (locator, item)
            for locator, item in self.state.items.items()
            if item.generation == generation and query.lower() in item.searchable_text.lower()
        ][:limit]
        return [
            BackendHit(
                memory_id=item.memory_id,
                revision=item.revision,
                generation=item.generation,
                rank=index,
                backend_locator=locator,
            )
            for index, (locator, item) in enumerate(matches, 1)
        ]

    def remove_projection(self, locators: list[str]) -> int:
        removed = 0
        for locator in locators:
            removed += int(self.state.items.pop(locator, None) is not None)
        return removed
