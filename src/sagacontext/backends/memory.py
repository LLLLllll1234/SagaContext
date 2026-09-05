from __future__ import annotations

from .base import BackendCapabilities, BackendHit, Projection


class InMemoryBackend:
    def __init__(self) -> None:
        self.items: dict[str, Projection] = {}
        self.operations: dict[str, str] = {}

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
        if operation_key in self.operations:
            return self.operations[operation_key]
        locator = f"memory://{projection.generation}/{projection.memory_id}/{projection.revision}"
        self.items[locator] = projection
        self.operations[operation_key] = locator
        return locator

    def locate_projection(self, memory_id: str, revision: int, generation: str) -> str | None:
        suffix = f"/{memory_id}/{revision}"
        return next((key for key in self.items if key.startswith(f"memory://{generation}/") and key.endswith(suffix)), None)

    def search(self, query: str, generation: str, limit: int = 10) -> list[BackendHit]:
        matches = [
            (locator, item)
            for locator, item in self.items.items()
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
            removed += int(self.items.pop(locator, None) is not None)
        return removed
