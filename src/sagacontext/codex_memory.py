"""Read-only Codex memory learning projection.

The projection deliberately keeps raw content optional and never mutates the
Codex session store. It is a small, version-aware skeleton for the console.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MemoryEvent:
    id: str
    session: str
    kind: str
    timestamp: str | None
    summary: str
    source: str
    evidence: str = "observed"
    raw: str | None = None


def source_version(source: Path) -> dict[str, str | None]:
    """Return a local source revision without contacting the network."""
    revision: str | None = None
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {"path": str(source), "revision": revision}


def _event_from_json(path: Path, value: dict[str, Any], index: int) -> MemoryEvent:
    kind = str(value.get("type") or value.get("event") or "message")
    text = str(value.get("text") or value.get("content") or value.get("summary") or "")
    summary = " ".join(text.split())[:240] or kind
    return MemoryEvent(
        id=f"{path.name}:{index}", session=path.stem, kind=kind,
        timestamp=value.get("timestamp") or value.get("created_at"),
        summary=summary, source=str(path), raw=text or None,
    )


def read_sessions(root: Path, *, include_raw: bool = False) -> list[MemoryEvent]:
    """Best-effort parse JSON/JSONL sessions; malformed files remain visible."""
    events: list[MemoryEvent] = []
    if not root.exists():
        return events
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        try:
            if path.suffix == ".jsonl":
                values = [json.loads(line) for line in path.read_text(errors="replace").splitlines() if line.strip()]
            else:
                loaded = json.loads(path.read_text(errors="replace"))
                values = loaded if isinstance(loaded, list) else [loaded]
            for index, value in enumerate(values):
                if isinstance(value, dict):
                    event = _event_from_json(path, value, index)
                    events.append(event if include_raw else MemoryEvent(**{**asdict(event), "raw": None}))
        except (OSError, ValueError, TypeError) as exc:
            events.append(MemoryEvent(f"{path.name}:error", path.stem, "unparsed", None, f"解析失败: {exc}", str(path), "error"))
    return events
