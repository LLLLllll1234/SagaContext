import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sagacontext.ledger.schema import SCHEMA_VERSION


class ConsoleReadError(RuntimeError):
    """A stable, non-sensitive failure returned by console read services."""

    ALLOWED_CODES = {
        "ledger_missing",
        "schema_unsupported",
        "ledger_busy",
        "data_invalid",
        "not_found",
        "invalid_request",
    }

    def __init__(self, code: str):
        if code not in self.ALLOWED_CODES:
            raise ValueError("unsupported console read error code")
        self.code = code
        super().__init__(code)


def _is_busy(error: sqlite3.Error) -> bool:
    message = str(error).lower()
    return "locked" in message or "busy" in message


@contextmanager
def read_snapshot(path: Path) -> Iterator[sqlite3.Connection]:
    """Open one consistent, read-only Ledger snapshot without running migrations."""
    resolved = path.resolve()
    if not resolved.is_file():
        raise ConsoleReadError("ledger_missing")

    db: sqlite3.Connection | None = None
    transaction_started = False
    try:
        db = sqlite3.connect(
            resolved.as_uri() + "?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=0.25,
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=250")
        db.execute("BEGIN")
        transaction_started = True
        try:
            versions = [
                row[0]
                for row in db.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
        except sqlite3.Error as error:
            if _is_busy(error):
                raise ConsoleReadError("ledger_busy") from error
            raise ConsoleReadError("schema_unsupported") from error
        if versions != list(range(1, SCHEMA_VERSION + 1)):
            raise ConsoleReadError("schema_unsupported")
        try:
            sequence = db.execute(
                "SELECT value FROM ledger_meta WHERE key='sequence'"
            ).fetchone()
        except sqlite3.Error as error:
            if _is_busy(error):
                raise ConsoleReadError("ledger_busy") from error
            raise ConsoleReadError("schema_unsupported") from error
        if sequence is None:
            raise ConsoleReadError("schema_unsupported")
        try:
            value = int(sequence[0])
        except (TypeError, ValueError) as error:
            raise ConsoleReadError("data_invalid") from error
        if value < 0:
            raise ConsoleReadError("data_invalid")
    except ConsoleReadError:
        if db is not None:
            if transaction_started and db.in_transaction:
                db.rollback()
            db.close()
        raise
    except sqlite3.Error as error:
        if db is not None:
            if transaction_started and db.in_transaction:
                db.rollback()
            db.close()
        if _is_busy(error):
            raise ConsoleReadError("ledger_busy") from error
        raise ConsoleReadError("schema_unsupported") from error

    try:
        yield db
    finally:
        if db is not None:
            if transaction_started and db.in_transaction:
                db.rollback()
            db.close()
