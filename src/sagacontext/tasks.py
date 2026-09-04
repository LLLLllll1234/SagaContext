from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta, timezone

def ensure_schema(db: sqlite3.Connection):
    db.execute("CREATE TABLE IF NOT EXISTS tasks_index(task_id TEXT PRIMARY KEY, repo_key TEXT, branch TEXT, goal TEXT, task_status TEXT, last_active TEXT, uri TEXT)"); db.commit()

def resume_candidate(db: sqlite3.Connection, repo_key: str, branch: str | None, days: int = 14):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = db.execute("SELECT * FROM tasks_index WHERE repo_key=? AND task_status='active' AND last_active>=? ORDER BY last_active DESC", (repo_key, cutoff)).fetchall()
    if not rows: return None
    for row in rows:
        if branch and row["branch"] == branch: return dict(row) if hasattr(row, "keys") else row
    return dict(rows[0]) if hasattr(rows[0], "keys") else rows[0]
