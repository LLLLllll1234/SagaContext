from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from ..ledger import Ledger
from .models import CursorUpdate, EventReceipt, JournalEvent, QuarantineReceipt


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class EventJournal:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def append(
        self, event: JournalEvent, cursor_update: CursorUpdate | None = None
    ) -> EventReceipt:
        self._validate_event(event)
        if cursor_update is not None:
            self._validate_cursor(cursor_update, event)
        existing = self.ledger.db.execute(
            "SELECT event_id,ingest_sequence,payload_json,source_locator_json FROM events "
            "WHERE owner_id=? AND host=? AND session_id=? AND source_generation=? "
            "AND source_event_key=?",
            (
                self.ledger.owner_id,
                event.host,
                event.session_id,
                event.source_generation,
                event.source_event_key,
            ),
        ).fetchone()
        if existing:
            if existing["payload_json"] != _canonical(event.payload) or existing[
                "source_locator_json"
            ] != _canonical(event.source_locator):
                raise ValueError("source_event_key_reused")
            if cursor_update is not None:
                with self.ledger._write_transaction():
                    self._write_cursor(cursor_update)
            return EventReceipt(
                status="duplicate",
                event_id=existing["event_id"],
                ingest_sequence=existing["ingest_sequence"],
            )

        event_id = str(uuid.uuid4())
        with self.ledger._write_transaction():
            sequence = self._next_ingest_sequence()
            self.ledger.db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    self.ledger.owner_id,
                    event.session_id,
                    event.workspace_id,
                    event.host,
                    event.host_version,
                    event.schema_version,
                    event.event_kind,
                    event.occurred_at.isoformat(),
                    _now(),
                    event.trust_class,
                    event.source_generation,
                    event.source_event_key,
                    _canonical(event.source_locator),
                    _canonical(event.payload),
                    event.parser_version,
                    sequence,
                ),
            )
            if cursor_update is not None:
                self._write_cursor(cursor_update)
        return EventReceipt(status="accepted", event_id=event_id, ingest_sequence=sequence)

    def record_invalid_line(
        self,
        *,
        cursor: CursorUpdate,
        byte_start: int,
        byte_end: int,
        payload: bytes,
        error_class: str,
        complete: bool,
    ) -> QuarantineReceipt:
        if not complete:
            return QuarantineReceipt(status="partial")
        self._validate_cursor(cursor)
        locator_digest = hashlib.sha256(cursor.source_locator.encode()).hexdigest()
        payload_digest = hashlib.sha256(payload).hexdigest()
        identity = "\0".join(
            (
                self.ledger.owner_id,
                cursor.source_generation,
                locator_digest,
                str(byte_start),
                str(byte_end),
                payload_digest,
            )
        )
        quarantine_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        with self.ledger._write_transaction():
            self.ledger.db.execute(
                "INSERT OR IGNORE INTO event_quarantine VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    quarantine_id,
                    self.ledger.owner_id,
                    cursor.host,
                    cursor.session_id,
                    cursor.source_generation,
                    locator_digest,
                    byte_start,
                    byte_end,
                    payload_digest,
                    error_class,
                    _now(),
                ),
            )
            self._write_cursor(cursor)
        return QuarantineReceipt(status="quarantined", quarantine_id=quarantine_id)

    def add_alias(
        self,
        *,
        host: str,
        session_id: str,
        source_generation: str,
        alias_event_key: str,
        canonical_event_id: str,
        alias_kind: str,
    ) -> str:
        canonical = self.ledger.db.execute(
            "SELECT event_id FROM events WHERE event_id=? AND owner_id=? AND host=? "
            "AND session_id=? AND source_generation=?",
            (
                canonical_event_id,
                self.ledger.owner_id,
                host,
                session_id,
                source_generation,
            ),
        ).fetchone()
        if not canonical:
            raise ValueError("canonical_event_out_of_scope")
        existing = self.ledger.db.execute(
            "SELECT canonical_event_id FROM event_aliases WHERE owner_id=? AND host=? "
            "AND session_id=? AND source_generation=? AND alias_event_key=?",
            (self.ledger.owner_id, host, session_id, source_generation, alias_event_key),
        ).fetchone()
        if existing:
            if existing["canonical_event_id"] != canonical_event_id:
                raise ValueError("alias_already_mapped")
            return existing["canonical_event_id"]
        with self.ledger._write_transaction():
            self.ledger.db.execute(
                "INSERT INTO event_aliases VALUES (?,?,?,?,?,?,?,?)",
                (
                    self.ledger.owner_id,
                    host,
                    session_id,
                    source_generation,
                    alias_event_key,
                    canonical_event_id,
                    alias_kind,
                    _now(),
                ),
            )
        return canonical_event_id

    def resolve_event_key(
        self, host: str, session_id: str, source_generation: str, source_event_key: str
    ) -> str | None:
        event = self.ledger.db.execute(
            "SELECT event_id FROM events WHERE owner_id=? AND host=? AND session_id=? "
            "AND source_generation=? AND source_event_key=?",
            (self.ledger.owner_id, host, session_id, source_generation, source_event_key),
        ).fetchone()
        if event:
            return event["event_id"]
        alias = self.ledger.db.execute(
            "SELECT canonical_event_id FROM event_aliases WHERE owner_id=? AND host=? "
            "AND session_id=? AND source_generation=? AND alias_event_key=?",
            (self.ledger.owner_id, host, session_id, source_generation, source_event_key),
        ).fetchone()
        return alias["canonical_event_id"] if alias else None

    def cursor(self, identity: CursorUpdate) -> int | None:
        row = self.ledger.db.execute(
            "SELECT byte_offset FROM source_cursors WHERE owner_id=? AND host=? "
            "AND session_id=? AND source_locator=? AND source_generation=?",
            (
                self.ledger.owner_id,
                identity.host,
                identity.session_id,
                identity.source_locator,
                identity.source_generation,
            ),
        ).fetchone()
        return row["byte_offset"] if row else None

    def _validate_event(self, event: JournalEvent) -> None:
        row = self.ledger.db.execute(
            "SELECT host,workspace_id FROM sessions WHERE session_id=? AND owner_id=?",
            (event.session_id, self.ledger.owner_id),
        ).fetchone()
        if not row or row["host"] != event.host or row["workspace_id"] != event.workspace_id:
            raise ValueError("event_session_out_of_scope")

    def _validate_cursor(
        self, cursor: CursorUpdate, event: JournalEvent | None = None
    ) -> None:
        row = self.ledger.db.execute(
            "SELECT host FROM sessions WHERE session_id=? AND owner_id=?",
            (cursor.session_id, self.ledger.owner_id),
        ).fetchone()
        if not row or row["host"] != cursor.host or cursor.byte_offset < 0:
            raise ValueError("cursor_session_out_of_scope")
        if event and (
            cursor.host != event.host
            or cursor.session_id != event.session_id
            or cursor.source_generation != event.source_generation
        ):
            raise ValueError("cursor_event_mismatch")

    def _write_cursor(self, cursor: CursorUpdate) -> None:
        current = self.cursor(cursor)
        if current is not None and cursor.byte_offset < current:
            raise ValueError("cursor_cannot_move_backwards")
        self.ledger.db.execute(
            "INSERT INTO source_cursors VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(owner_id,host,session_id,source_locator,source_generation) "
            "DO UPDATE SET byte_offset=excluded.byte_offset,"
            "source_fingerprint=excluded.source_fingerprint,updated_at=excluded.updated_at",
            (
                self.ledger.owner_id,
                cursor.host,
                cursor.session_id,
                cursor.source_locator,
                cursor.source_generation,
                cursor.byte_offset,
                cursor.source_fingerprint,
                _now(),
            ),
        )

    def _next_ingest_sequence(self) -> int:
        row = self.ledger.db.execute(
            "SELECT COALESCE(MAX(ingest_sequence),0)+1 FROM events WHERE owner_id=?",
            (self.ledger.owner_id,),
        ).fetchone()
        return int(row[0])
