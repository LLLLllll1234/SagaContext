from __future__ import annotations
from typing import Protocol
from .models import BenchmarkCase, Observation

class Adapter(Protocol):
    name: str
    def run(self, case: BenchmarkCase) -> Observation: ...

class FixtureAdapter:
    def __init__(self, name: str): self.name = name
    def run(self, case: BenchmarkCase) -> Observation:
        return case.observations.get(self.name, Observation())

class NoMemoryAdapter:
    name = "no_memory"
    def run(self, case: BenchmarkCase) -> Observation: return Observation()
