from __future__ import annotations

from datetime import datetime, timezone

from ..ledger import Ledger
from ..maintenance import EventJournal, JournalEvent


EVENTS = {
    "SessionStart": "session_opened", "UserPromptSubmit": "user_message",
    "PreToolUse": "tool_started", "PostToolUse": "tool_finished",
    "Stop": "checkpoint_requested", "SessionEnd": "session_closed",
}


class CodexShadowAdapter:
    """Explicitly ingest redacted G3-format receipts; never return host context."""

    def __init__(self, ledger: Ledger, *, host_version: str, verified_events: set[str]):
        if host_version != "codex-cli 0.153.4" or not verified_events <= EVENTS.keys():
            raise ValueError("unverified_host_capability")
        self.journal = EventJournal(ledger)
        self.host_version = host_version
        self.verified_events = verified_events

    def ingest(self, record: dict, *, session_id: str, workspace_id: str,
               synthetic_payload: dict | None = None):
        event = record["hook_event_name"]
        if event not in self.verified_events:
            raise ValueError("unverified_host_event")
        source = record["source_event_ref"]
        generation = record["probe_id"] + ":" + record["scenario"]
        if not source or not record.get("session_ref"):
            raise ValueError("missing_event_identity")
        return self.journal.append(JournalEvent(
            host="codex", host_version=self.host_version, session_id=session_id,
            workspace_id=workspace_id, event_kind=EVENTS[event],
            occurred_at=datetime.now(timezone.utc), trust_class="synthetic",
            source_generation=generation, source_event_key=source,
            source_locator={"probe_id": record["probe_id"], "scenario": record["scenario"],
                            "source_event_ref": source, "session_ref": record["session_ref"]},
            payload={"hook_event_name": event, "payload_shape_digest": record["payload_shape_digest"],
                     "synthetic": synthetic_payload or {}}, parser_version="codex-shadow-v1",
        ))
