from __future__ import annotations
import sqlite3
import hashlib
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

def create(db: sqlite3.Connection, repo_key: str, branch: str | None, goal: str, dev_root: str) -> dict:
    task_id = hashlib.sha1(f"{repo_key}:{branch}:{goal}".encode()).hexdigest()[:12]
    now = datetime.now(timezone.utc).isoformat()
    uri = f"{dev_root}/task/repo-{repo_key}/{task_id}.md"
    db.execute("INSERT OR IGNORE INTO tasks_index VALUES (?,?,?,?,?,?,?)", (task_id, repo_key, branch, goal, "active", now, uri))
    db.execute("UPDATE tasks_index SET last_active=? WHERE task_id=?", (now, task_id)); db.commit()
    return {"task_id": task_id, "repo_key": repo_key, "branch": branch, "goal": goal, "task_status": "active", "last_active": now, "uri": uri}

def touch(db: sqlite3.Connection, task_id: str):
    db.execute("UPDATE tasks_index SET last_active=? WHERE task_id=?", (datetime.now(timezone.utc).isoformat(), task_id)); db.commit()

def apply_delta(db: sqlite3.Connection, task_id: str, fields: dict):
    allowed = {"goal", "task_status"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates["last_active"] = datetime.now(timezone.utc).isoformat()
    sql = ",".join(f"{key}=?" for key in updates)
    db.execute(f"UPDATE tasks_index SET {sql} WHERE task_id=?", [*updates.values(), task_id]); db.commit()
