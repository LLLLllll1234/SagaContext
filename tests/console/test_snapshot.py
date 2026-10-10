import sqlite3

import pytest

from sagacontext.console.db import ConsoleReadError, read_snapshot
from sagacontext.console.models import Page, SnapshotMeta
from sagacontext.console.runtime import ReadLedger
from sagacontext.ledger import Ledger


def test_snapshot_rejects_writes(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = Ledger(path, owner_id="owner-a")
    ledger.close()

    with read_snapshot(path) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM owners")
        assert db.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 1


def test_snapshot_missing_ledger_does_not_create_it(tmp_path):
    path = tmp_path / "missing.db"

    with pytest.raises(ConsoleReadError) as caught:
        with read_snapshot(path):
            pass

    assert caught.value.code == "ledger_missing"
    assert not path.exists()


def test_snapshot_uses_independent_connections_and_sees_active_wal(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = Ledger(path, owner_id="owner-a")
    ledger.db.execute(
        "INSERT INTO owners(owner_id,created_at) VALUES (?,?)",
        ("owner-b", "2026-09-11T00:00:00+00:00"),
    )

    with read_snapshot(path) as first, read_snapshot(path) as second:
        assert first is not second
        assert first.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 2
        assert second.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 2

    ledger.close()


def test_snapshot_rejects_incomplete_schema(tmp_path):
    path = tmp_path / "ledger.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    db.execute("INSERT INTO schema_migrations VALUES (1, 'now')")
    db.commit()
    db.close()

    with pytest.raises(ConsoleReadError) as caught:
        with read_snapshot(path):
            pass

    assert caught.value.code == "schema_unsupported"


def test_snapshot_rejects_schema_with_invalid_migration_sequence(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = Ledger(path, owner_id="owner-a")
    ledger.db.execute("DELETE FROM schema_migrations WHERE version=2")
    ledger.close()

    with pytest.raises(ConsoleReadError) as caught:
        with read_snapshot(path):
            pass

    assert caught.value.code == "schema_unsupported"


@pytest.mark.parametrize("value", ["invalid", "-1"])
def test_snapshot_rejects_invalid_ledger_sequence(tmp_path, value):
    path = tmp_path / "ledger.db"
    ledger = Ledger(path, owner_id="owner-a")
    ledger.db.execute("UPDATE ledger_meta SET value=? WHERE key='sequence'", (value,))
    ledger.close()

    with pytest.raises(ConsoleReadError) as caught:
        with read_snapshot(path):
            pass

    assert caught.value.code == "data_invalid"


def test_snapshot_reports_busy_database(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = Ledger(path, owner_id="owner-a")
    ledger.close()
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("PRAGMA journal_mode=DELETE")
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(ConsoleReadError) as caught:
            with read_snapshot(path):
                pass
        assert caught.value.code == "ledger_busy"
    finally:
        blocker.rollback()
        blocker.close()


def test_read_models_are_strict_and_read_ledger_has_no_write_surface(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = Ledger(path, owner_id="owner-a")
    ledger.close()
    with read_snapshot(path) as db:
        reader = ReadLedger(db, "owner-a")
        assert reader.db is db
        assert reader.owner_id == "owner-a"
        assert not hasattr(reader, "commit")

    meta = SnapshotMeta(
        observed_at="2026-09-11T00:00:00Z",
        schema_version=5,
        ledger_sequence=0,
        request_id="request-a",
    )
    assert meta.schema_version == 5
    assert Page[int](items=[1], next_cursor=None).items == [1]
    with pytest.raises(ValueError):
        SnapshotMeta(
            observed_at="2026-09-11T00:00:00Z",
            schema_version=5,
            ledger_sequence=0,
            request_id="request-a",
            unexpected=True,
        )
