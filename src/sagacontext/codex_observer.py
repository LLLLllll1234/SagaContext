from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def snapshot(codex_home: Path | None = None) -> dict[str, Any]:
    home = codex_home or (Path.home() / ".codex")
    memories: list[dict[str, Any]] = []
    db = home / "memories_1.sqlite"
    if db.exists():
        with sqlite3.connect(db) as con:
            for row in con.execute("SELECT thread_id, raw_memory, rollout_summary, generated_at, usage_count, last_usage, selected_for_phase2 FROM stage1_outputs ORDER BY generated_at DESC"):
                memories.append({"id": row[0], "content": row[1], "summary": row[2], "generated_at": row[3], "usage_count": row[4] or 0, "last_usage": row[5], "selected": bool(row[6]), "source": "memories_1.sqlite"})
    sessions: list[dict[str, Any]] = []
    db = home / "thread_history_1.sqlite"
    if db.exists():
        with sqlite3.connect(db) as con:
            for row in con.execute("SELECT thread_id, COUNT(*), MAX(created_at_ms), SUM(CASE WHEN item_type='userMessage' THEN 1 ELSE 0 END) FROM thread_items GROUP BY thread_id ORDER BY MAX(created_at_ms) DESC LIMIT 100"):
                sessions.append({"id": row[0], "items": row[1], "last_activity": row[2], "user_messages": row[3], "status": "active" if row[2] and row[2] > 0 else "ended"})
    return {"memories": memories, "sessions": sessions, "counts": {"memories": len(memories), "sessions": len(sessions)}}
