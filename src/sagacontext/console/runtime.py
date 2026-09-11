from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any


_CONFIGURED_MODES = {"off", "shadow", "guarded"}
_EFFECTIVE_MODES = {"shadow", "guarded"}
_RUNNING_STATUSES = {"running", "draining"}


@dataclass(frozen=True, slots=True)
class ReadLedger:
    """The minimal Ledger-shaped dependency accepted by read-only reports."""

    db: sqlite3.Connection
    owner_id: str


def _result(
    configured_mode: str,
    persisted_status: object,
    effective_mode: str,
    block_reason: str | None,
) -> dict[str, object]:
    return {
        "configured_mode": configured_mode,
        "persisted_status": persisted_status,
        "effective_mode": effective_mode,
        "block_reason": block_reason,
    }


def _deadline(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("deadline must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("deadline must include a timezone")
    return parsed.astimezone(timezone.utc)


def effective_state(
    run: dict | None,
    configured_mode: str,
    stop_active: bool,
    now: datetime,
) -> dict[str, object]:
    """Compute displayed runtime state without invoking the mutating runtime."""
    persisted_status = run.get("status") if isinstance(run, dict) else None
    if configured_mode not in _CONFIGURED_MODES:
        return _result(configured_mode, persisted_status, "unknown", "data_invalid")
    if run is None:
        return _result(configured_mode, None, "off", "no_run")
    if not isinstance(run, dict) or not isinstance(persisted_status, str):
        return _result(configured_mode, persisted_status, "unknown", "data_invalid")
    if configured_mode == "off":
        return _result(configured_mode, persisted_status, "off", "configured_off")
    if stop_active:
        return _result(configured_mode, persisted_status, "off", "stop_active")
    if now.tzinfo is None or now.utcoffset() is None:
        return _result(configured_mode, persisted_status, "unknown", "data_invalid")
    try:
        expired = _deadline(run.get("deadline")) <= now.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return _result(configured_mode, persisted_status, "unknown", "data_invalid")
    if expired:
        return _result(configured_mode, persisted_status, "off", "deadline_exceeded")
    if persisted_status not in _RUNNING_STATUSES:
        return _result(configured_mode, persisted_status, "off", "run_not_allowed")
    persisted_mode = run.get("mode")
    if persisted_mode not in _EFFECTIVE_MODES:
        return _result(configured_mode, persisted_status, "unknown", "data_invalid")
    if configured_mode == "shadow" and persisted_mode == "guarded":
        return _result(configured_mode, persisted_status, "off", "run_not_allowed")
    return _result(configured_mode, persisted_status, persisted_mode, None)
