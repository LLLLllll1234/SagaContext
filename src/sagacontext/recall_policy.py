from __future__ import annotations

import json
from typing import Callable, Literal

from pydantic import BaseModel, Field

from .backends import BackendAdapter, BackendHit
from .ledger import Ledger, MemoryView, TaskContext


class Omission(BaseModel):
    memory_id: str
    revision: int
    reason: Literal["owner", "scope", "revision", "generation", "inactive", "missing", "conflict", "duplicate", "budget"]


class ContextBundle(BaseModel):
    owner_id: str
    generation: str
    ledger_sequence: int
    items: list[MemoryView] = Field(default_factory=list)
    omissions: list[Omission] = Field(default_factory=list)
    text: str = ""
    budget: int
    used: int = 0
    accounting: str = "utf8_bytes_conservative_estimate"


class RecallPolicy:
    """Only Ledger bodies may enter a bundle. A bundle is a point-in-time decision."""

    def __init__(self, ledger: Ledger, *, count_tokens: Callable[[str], int] | None = None):
        self.ledger = ledger
        self.count_tokens = count_tokens or (lambda text: len(text.encode("utf-8")))
        self.accounting = "provided_tokenizer" if count_tokens else "utf8_bytes_conservative_estimate"

    def recall(self, backend: BackendAdapter, query: str, generation: str, context: TaskContext,
               *, budget: int = 2000, limit: int = 30) -> ContextBundle:
        return self.assemble(backend.search(query, generation, limit), generation, context, budget=budget)

    def assemble(self, hits: list[BackendHit], generation: str, context: TaskContext,
                 *, budget: int = 2000) -> ContextBundle:
        self.ledger.db.execute("SAVEPOINT recall_snapshot")
        try:
            return self._assemble(hits, generation, context, budget=budget)
        finally:
            self.ledger.db.execute("RELEASE SAVEPOINT recall_snapshot")

    def _assemble(self, hits: list[BackendHit], generation: str, context: TaskContext,
                  *, budget: int) -> ContextBundle:
        if budget < 0:
            raise ValueError("budget must be nonnegative")
        bundle = ContextBundle(owner_id=context.owner_id, generation=generation,
                               ledger_sequence=self.ledger.sequence, budget=budget, accounting=self.accounting)
        accepted = set()
        blocks = []
        for hit in sorted(hits, key=lambda hit: hit.rank):
            reason = None
            row = self.ledger.db.execute("SELECT owner_id,state,current_revision,conflict_state FROM memories WHERE memory_id=?", (hit.memory_id,)).fetchone()
            views = self.ledger.get_current([hit.memory_id], context)
            if context.owner_id != self.ledger.owner_id or row and row["owner_id"] != context.owner_id:
                reason = "owner"
            elif hit.generation != generation:
                reason = "generation"
            elif row is None:
                reason = "missing"
            elif row["state"] != "active":
                reason = "inactive"
            elif row["current_revision"] != hit.revision:
                reason = "revision"
            elif row["conflict_state"] != "none":
                reason = "conflict"
            elif not views:
                reason = "scope"
            elif hit.memory_id in accepted:
                reason = "duplicate"
            if reason is None:
                view = views[0]
                # Serialize the entire authoritative object, including scope and revision.
                block = json.dumps(view.model_dump(), ensure_ascii=False, sort_keys=True)
                text = "\n".join([*blocks, block])
                cost = self.count_tokens(text)
                if cost > budget:
                    reason = "budget"
                else:
                    blocks.append(block)
                    bundle.items.append(view)
                    bundle.text = text
                    bundle.used = cost
                    accepted.add(hit.memory_id)
            if reason is not None:
                bundle.omissions.append(Omission(memory_id=hit.memory_id, revision=hit.revision, reason=reason))
        return bundle
