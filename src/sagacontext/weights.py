from __future__ import annotations
import sqlite3
from datetime import datetime, timezone

def ensure_schema(db: sqlite3.Connection):
    db.execute("CREATE TABLE IF NOT EXISTS weights(user TEXT, layer TEXT, type TEXT, scope_level TEXT, w REAL, updated_at TEXT, PRIMARY KEY(user,layer,type,scope_level))"); db.commit()

def update(db: sqlite3.Connection, user: str, layer: str, type_: str, scope_level: str, used: bool, rate: float = 0.05) -> float:
    row = db.execute("SELECT w FROM weights WHERE user=? AND layer=? AND type=? AND scope_level=?", (user, layer, type_, scope_level)).fetchone()
    current = float(row[0]) if row else 1.0
    value = max(0.1, min(2.0, current + (rate if used else -rate)))
    db.execute("INSERT OR REPLACE INTO weights VALUES (?,?,?,?,?,?)", (user, layer, type_, scope_level, value, datetime.now(timezone.utc).isoformat())); db.commit()
    return value
