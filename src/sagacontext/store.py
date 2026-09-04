from __future__ import annotations
import sqlite3
from pathlib import Path

class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=2000;
        CREATE TABLE IF NOT EXISTS repo_keys(realpath TEXT PRIMARY KEY, repo_key TEXT NOT NULL, kind TEXT NOT NULL, git_root TEXT);
        CREATE TABLE IF NOT EXISTS sessions(host TEXT, session_id TEXT, cwd TEXT, repo_key TEXT, branch TEXT, transcript_path TEXT, cursor_offset INTEGER DEFAULT 0, turn_count INTEGER DEFAULT 0, token_estimate INTEGER DEFAULT 0, ended INTEGER DEFAULT 0, PRIMARY KEY(host, session_id));
        CREATE TABLE IF NOT EXISTS recalled(host TEXT, session_id TEXT, uri TEXT, type TEXT, score REAL, at_event TEXT, PRIMARY KEY(host, session_id, uri));
        """)
        self.db.commit()

    def upsert_session(self, host: str, session_id: str, **values):
        cols = {"host": host, "session_id": session_id, **values}
        names = list(cols); updates = [f"{n}=excluded.{n}" for n in names if n not in ("host", "session_id")]
        self.db.execute(f"INSERT INTO sessions ({','.join(names)}) VALUES ({','.join('?' for _ in names)}) ON CONFLICT(host,session_id) DO UPDATE SET {','.join(updates)}", list(cols.values()))
        self.db.commit()

    def get_session(self, host: str, session_id: str):
        return self.db.execute("SELECT * FROM sessions WHERE host=? AND session_id=?", (host, session_id)).fetchone()

    def remember_recalled(self, host: str, session_id: str, uri: str, typ: str, score: float | None, event: str):
        self.db.execute("INSERT OR REPLACE INTO recalled VALUES (?,?,?,?,?,?)", (host, session_id, uri, typ, score, event)); self.db.commit()

    def recalled_uris(self, host: str, session_id: str) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT uri FROM recalled WHERE host=? AND session_id=?", (host, session_id))}
