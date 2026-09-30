"""Single-key edits to host settings files for the observation window.

Only one key per host is touched. Files can hold credentials (Claude Code keeps
provider tokens in settings.json), so nothing here copies, logs or returns any
value other than the one key being changed.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

# host -> (settings file relative to home, key path, value that disables updates)
AUTO_UPDATE = {
    "claude-code": (".claude/settings.json", ("env", "DISABLE_AUTOUPDATER"), "1"),
    "codex": (".codex/app-server-daemon/settings.json", ("updater", "autoUpdateEnabled"), False),
}
MISSING = {"state": "missing"}


def _path(host: str, home: Path | None) -> Path:
    return (home or Path.home()) / AUTO_UPDATE[host][0]


def _read(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("host_settings_not_object")
    return data


def _write(path: Path, data: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sagacontext-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def current(host: str, home: Path | None = None) -> dict[str, Any]:
    """State of the one key: file missing, key missing, or its value."""
    path, (section, key) = _path(host, home), AUTO_UPDATE[host][1]
    data = _read(path)
    if data is None:
        return {"state": "file_missing"}
    block = data.get(section)
    if not isinstance(block, dict) or key not in block:
        return MISSING
    return {"state": "present", "value": block[key]}


def disabled(host: str, home: Path | None = None) -> bool:
    return current(host, home) == {"state": "present", "value": AUTO_UPDATE[host][2]}


def disable(host: str, home: Path | None = None) -> dict[str, Any]:
    """Set the key and return its original state for later restore."""
    path, (section, key), value = _path(host, home), AUTO_UPDATE[host][1], AUTO_UPDATE[host][2]
    original = current(host, home)
    data = _read(path) or {}
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    block = data.get(section)
    if block is not None and not isinstance(block, dict):
        raise ValueError("host_settings_section_not_object")
    data[section] = {**(block or {}), key: value}
    _write(path, data, mode)
    if not disabled(host, home):
        raise ValueError("host_settings_verify_failed")
    return original


def restore(host: str, original: dict[str, Any], home: Path | None = None) -> None:
    path, (section, key) = _path(host, home), AUTO_UPDATE[host][1]
    if original["state"] == "file_missing":
        data = _read(path)
        if data is None:
            return
        # Remove only our key; delete the file only if nothing else was added since.
        block = data.get(section)
        if isinstance(block, dict):
            block.pop(key, None)
            if not block:
                data.pop(section)
        if data:
            _write(path, data, stat.S_IMODE(path.stat().st_mode))
        else:
            path.unlink()
        return
    data = _read(path)
    if data is None:
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    block = data.get(section)
    if not isinstance(block, dict):
        block = {}
    if original["state"] == "present":
        block[key] = original["value"]
    else:
        block.pop(key, None)
    if block:
        data[section] = block
    else:
        data.pop(section, None)
    _write(path, data, mode)


__all__ = ["AUTO_UPDATE", "current", "disable", "disabled", "restore"]
