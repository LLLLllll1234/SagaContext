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
        CREATE TABLE IF NOT EXISTS sessions(host TEXT, session_id TEXT, cwd TEXT, repo_key TEXT, branch TEXT, transcript_path TEXT, cursor_offset INTEGER DEFAULT 0, turn_count INTEGER DEFAULT 0, token_estimate INTEGER DEFAULT 0, task_id TEXT, ended INTEGER DEFAULT 0, PRIMARY KEY(host, session_id));
        CREATE TABLE IF NOT EXISTS recalled(host TEXT, session_id TEXT, uri TEXT, type TEXT, score REAL, at_event TEXT, PRIMARY KEY(host, session_id, uri));
        CREATE TABLE IF NOT EXISTS buffer(id INTEGER PRIMARY KEY AUTOINCREMENT, host TEXT, session_id TEXT, turn_idx INTEGER, level TEXT, layer_guess TEXT, kind TEXT, text TEXT, files TEXT, confidence REAL, created_at TEXT, consumed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS pending(id TEXT PRIMARY KEY, created_at TEXT, layer TEXT, type TEXT, old_uri TEXT, new_summary TEXT, resolved TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS traces(trace_id TEXT PRIMARY KEY, kind TEXT, host TEXT, session_id TEXT, created_at TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS tool_edits(id INTEGER PRIMARY KEY AUTOINCREMENT, host TEXT, session_id TEXT, turn_idx INTEGER, path TEXT, sha_after TEXT, tool_call_id TEXT, created_at TEXT, checked INTEGER DEFAULT 0);
        """)
        session_columns = {row[1] for row in self.db.execute("PRAGMA table_info(sessions)")}
        if "task_id" not in session_columns:
            self.db.execute("ALTER TABLE sessions ADD COLUMN task_id TEXT")
        from .tasks import ensure_schema
        from .weights import ensure_schema as ensure_weights
        ensure_schema(self.db); ensure_weights(self.db)
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

    def add_candidates(self, host: str, session_id: str, candidates):
        import json, datetime
        self.db.executemany("INSERT INTO buffer(host,session_id,turn_idx,level,layer_guess,kind,text,files,confidence,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", [(host, session_id, c.turn_idx, c.level, c.layer_guess, c.kind, c.text, json.dumps(c.files), c.confidence, datetime.datetime.now(datetime.timezone.utc).isoformat()) for c in candidates]); self.db.commit()

    def unconsumed_candidates(self, host: str, session_id: str):
        import json
        from .models import Candidate
        rows = self.db.execute("SELECT * FROM buffer WHERE host=? AND session_id=? AND consumed=0 ORDER BY id", (host, session_id)).fetchall()
        return [Candidate(level=r["level"], layer_guess=r["layer_guess"], kind=r["kind"], turn_idx=r["turn_idx"], text=r["text"], files=json.loads(r["files"] or "[]"), confidence=r["confidence"]) for r in rows]

    def consume_candidates(self, host: str, session_id: str):
        self.db.execute("UPDATE buffer SET consumed=1 WHERE host=? AND session_id=? AND consumed=0", (host, session_id)); self.db.commit()

    def add_trace(self, trace_id: str, kind: str, host: str, session_id: str, payload: dict):
        import datetime, json
        self.db.execute("INSERT INTO traces VALUES (?,?,?,?,?,?)", (trace_id, kind, host, session_id, datetime.datetime.now(datetime.timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=True))); self.db.commit()

    def add_pending(self, items):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.db.executemany("INSERT OR REPLACE INTO pending(id,created_at,layer,type,old_uri,new_summary,resolved) VALUES (?,?,?,?,?,?,'')",
                            [(item.id, now, item.layer, item.type, item.old_uri, item.new_summary) for item in items])
        self.db.commit()

    def resolve_pending(self, pending_id: str, resolution: str) -> bool:
        cursor = self.db.execute("UPDATE pending SET resolved=? WHERE id=? AND resolved=''", (resolution, pending_id))
        self.db.commit()
        return cursor.rowcount == 1

    def get_trace(self, trace_id: str):
        return self.db.execute("SELECT * FROM traces WHERE trace_id=?", (trace_id,)).fetchone()

    def add_tool_edit(self, host: str, session_id: str, path: str, sha_after: str, tool_call_id: str = ""):
        import datetime
        row = self.get_session(host, session_id)
        turn = int(row["turn_count"] or 0) if row else 0
        self.db.execute("INSERT INTO tool_edits(host,session_id,turn_idx,path,sha_after,tool_call_id,created_at) VALUES (?,?,?,?,?,?,?)", (host, session_id, turn, path, sha_after, tool_call_id, datetime.datetime.now(datetime.timezone.utc).isoformat())); self.db.commit()

    def tool_edits(self, host: str, session_id: str):
        return self.db.execute("SELECT * FROM tool_edits WHERE host=? AND session_id=? AND checked=0 ORDER BY created_at", (host, session_id)).fetchall()

    def mark_tool_edits_checked(self, ids: list[int]):
        if not ids: return
        self.db.executemany("UPDATE tool_edits SET checked=1 WHERE id=?", [(item,) for item in ids]); self.db.commit()
