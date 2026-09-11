from datetime import datetime, timedelta, timezone

import pytest

from sagacontext.console.runtime import effective_state


NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def run(*, status="running", mode="guarded", deadline=None):
    return {
        "status": status,
        "mode": mode,
        "deadline": deadline or (NOW + timedelta(hours=1)).isoformat(),
    }


@pytest.mark.parametrize(
    ("record", "configured", "stop", "effective", "reason"),
    [
        (None, "guarded", False, "off", "no_run"),
        (run(), "off", False, "off", "configured_off"),
        (run(), "guarded", True, "off", "stop_active"),
        (run(deadline=(NOW - timedelta(seconds=1)).isoformat()), "guarded", False, "off", "deadline_exceeded"),
        (run(status="paused"), "guarded", False, "off", "run_not_allowed"),
        (run(status="stopping"), "guarded", False, "off", "run_not_allowed"),
        (run(status="stopped"), "guarded", False, "off", "run_not_allowed"),
        (run(status="cleanup_required"), "guarded", False, "off", "run_not_allowed"),
        (run(mode="shadow"), "guarded", False, "shadow", None),
        (run(mode="guarded"), "guarded", False, "guarded", None),
    ],
)
def test_effective_state_precedence(record, configured, stop, effective, reason):
    result = effective_state(record, configured, stop, NOW)

    assert result == {
        "configured_mode": configured,
        "persisted_status": record["status"] if record else None,
        "effective_mode": effective,
        "block_reason": reason,
    }


def test_effective_state_compares_timezone_aware_deadlines():
    deadline = (NOW + timedelta(minutes=30)).astimezone(timezone(timedelta(hours=8)))
    assert effective_state(run(deadline=deadline.isoformat()), "guarded", False, NOW)["effective_mode"] == "guarded"


@pytest.mark.parametrize(
    ("record", "configured", "now"),
    [
        (run(), "active", NOW),
        (run(deadline="not-a-date"), "guarded", NOW),
        (run(mode="active"), "guarded", NOW),
        (run(), "guarded", NOW.replace(tzinfo=None)),
    ],
)
def test_effective_state_marks_invalid_data_unknown(record, configured, now):
    result = effective_state(record, configured, False, now)

    assert result["effective_mode"] == "unknown"
    assert result["block_reason"] == "data_invalid"


def test_effective_state_is_stable_for_fixed_inputs():
    record = run(mode="shadow")
    assert effective_state(record, "guarded", False, NOW) == effective_state(record, "guarded", False, NOW)
