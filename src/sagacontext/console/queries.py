"""Scoped SQL building blocks. Every query uses its caller's read transaction."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3

from .db import ConsoleReadError
from .serialize import safe_payload


def workspace(db, owner_id, workspace_id):
    row = db.execute("SELECT DISTINCT project_id,workspace_id FROM project_locations WHERE owner_id=? AND workspace_id=?",
                     (owner_id, workspace_id)).fetchone()
    if row is None:
        raise ConsoleReadError("not_found")
    return dict(row)


def project(db, owner_id, project_id):
    if not db.execute("SELECT 1 FROM projects WHERE owner_id=? AND project_id=?", (owner_id, project_id)).fetchone():
        raise ConsoleReadError("not_found")


def page(db: sqlite3.Connection, sql: str, params: tuple, *, keys: tuple[str, ...],
         scope: object, cursor: str | None, limit: int, sanitize: bool = True) -> dict:
    if not 1 <= limit <= 100:
        raise ConsoleReadError("invalid_request")
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True, default=str).encode()).hexdigest()
    tail = ""
    extra = ()
    if cursor:
        try:
            if len(cursor) > 4096:
                raise ValueError()
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            values = decoded["after"]
            if decoded["scope"] != digest or not isinstance(values, list) or len(values) != len(keys) or not all(isinstance(v, str) for v in values):
                raise ValueError()
            tail = " WHERE (" + ",".join(keys) + ") < (" + ",".join("?" for _ in keys) + ")"
            extra = tuple(values)
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ConsoleReadError("invalid_request") from None
    ordered = ",".join(key + " DESC" for key in keys)
    rows = [dict(row) for row in db.execute(f"SELECT * FROM ({sql}) AS entries{tail} ORDER BY {ordered} LIMIT ?", (*params, *extra, limit+1))]
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = base64.urlsafe_b64encode(json.dumps({"scope":digest,"after":[rows[-1][key] for key in keys]}).encode()).decode()
    return {"items":safe_payload(rows) if sanitize else rows, "next_cursor":next_cursor}


def sessions(db, owner_id, workspace_id, cursor=None, limit=50):
    workspace(db, owner_id, workspace_id)
    return page(db, """SELECT s.session_id,s.host,s.opened_at,s.closed_at,
        (SELECT b.task_id FROM task_bindings b WHERE b.session_id=s.session_id AND b.owner_id=s.owner_id AND b.end_event_id IS NULL LIMIT 1) AS task_id,
        (SELECT MAX(e.received_at) FROM events e WHERE e.session_id=s.session_id AND e.owner_id=s.owner_id) AS last_event_at
        FROM sessions s WHERE s.owner_id=? AND s.workspace_id=?""", (owner_id,workspace_id),
        keys=("opened_at","session_id"),scope=("sessions",owner_id,workspace_id),cursor=cursor,limit=limit)


def _task_sql(*, current_only=False):
    current = """AND EXISTS (SELECT 1 FROM task_bindings current
          JOIN sessions current_session ON current_session.session_id=current.session_id
          WHERE current.owner_id=t.owner_id AND current.task_id=t.task_id
          AND current.end_event_id IS NULL AND current_session.owner_id=t.owner_id
          AND current_session.workspace_id=?) AND t.status='active'""" if current_only else ""
    return """SELECT t.task_id,t.project_id,t.goal,t.status,t.created_at,t.last_active,
        (SELECT s.session_id FROM task_bindings b JOIN sessions s ON s.session_id=b.session_id
          WHERE b.owner_id=t.owner_id AND s.owner_id=t.owner_id AND b.task_id=t.task_id
          AND (? IS NULL OR s.workspace_id=?) ORDER BY (b.end_event_id IS NULL) DESC,s.opened_at DESC,s.session_id DESC LIMIT 1) AS session_id,
        (SELECT MAX(e.received_at) FROM task_bindings b
          JOIN sessions s ON s.session_id=b.session_id AND s.owner_id=b.owner_id
          JOIN events e ON e.session_id=b.session_id AND e.owner_id=b.owner_id AND e.workspace_id=s.workspace_id
          JOIN events first ON first.event_id=b.start_event_id AND first.owner_id=b.owner_id AND first.session_id=b.session_id
          LEFT JOIN events last ON last.event_id=b.end_event_id AND last.owner_id=b.owner_id AND last.session_id=b.session_id
          WHERE b.owner_id=t.owner_id AND b.task_id=t.task_id AND e.event_kind='checkpoint_requested'
          AND e.ingest_sequence>=first.ingest_sequence AND (last.event_id IS NULL OR e.ingest_sequence<last.ingest_sequence)
          AND (? IS NULL OR s.workspace_id=?)) AS checkpoint_at
        FROM tasks t WHERE t.owner_id=? AND t.project_id=? """ + current


def tasks(db, owner_id, project_id, workspace_id=None, cursor=None, limit=50, current_only=False):
    project(db,owner_id,project_id)
    if workspace_id and workspace(db,owner_id,workspace_id)["project_id"] != project_id:
        raise ConsoleReadError("not_found")
    params=(workspace_id,workspace_id,workspace_id,workspace_id,owner_id,project_id)
    if current_only:
        if workspace_id is None:
            raise ConsoleReadError("invalid_request")
        params+=(workspace_id,)
    return page(db, _task_sql(current_only=current_only), params,
        keys=("last_active","task_id"),scope=("tasks",owner_id,project_id,workspace_id),cursor=cursor,limit=limit)


def task(db, owner_id, project_id, workspace_id, task_id):
    project(db,owner_id,project_id)
    if workspace(db,owner_id,workspace_id)["project_id"] != project_id:
        raise ConsoleReadError("not_found")
    row=db.execute(_task_sql()+" AND t.task_id=?",(workspace_id,workspace_id,workspace_id,workspace_id,owner_id,project_id,task_id)).fetchone()
    if row is None:
        raise ConsoleReadError("not_found")
    return safe_payload(dict(row))


def batches(db, owner_id, workspace_id, status=None, cursor=None, limit=50):
    workspace(db,owner_id,workspace_id)
    return page(db, """SELECT b.batch_id,b.session_id,b.task_id,b.status,b.created_at,b.settled_at,
        b.attempt_count,b.next_attempt_at,b.last_error_class,
        (SELECT rb.rollout_id FROM rollout_batches rb JOIN rollout_runs r USING(rollout_id)
          WHERE rb.batch_id=b.batch_id AND r.owner_id=b.owner_id AND r.workspace_id=s.workspace_id LIMIT 1) AS rollout_id,
        (SELECT COUNT(*) FROM proposals p WHERE p.batch_id=b.batch_id) AS proposal_count
        FROM batches b JOIN sessions s USING(session_id)
        WHERE b.owner_id=? AND s.owner_id=b.owner_id AND s.workspace_id=? AND (? IS NULL OR b.status=?)""",
        (owner_id,workspace_id,status,status),keys=("created_at","batch_id"),
        scope=("batches",owner_id,workspace_id,status),cursor=cursor,limit=limit)


def activity(db, owner_id, workspace_id, start, end, kinds=(), cursor=None, limit=50):
    workspace(db,owner_id,workspace_id)
    sql = """SELECT event_id AS object_id,event_kind AS kind,received_at AS recorded_at,occurred_at,
            'event' AS object_type,session_id,NULL AS batch_id,NULL AS memory_id,NULL AS rollout_id,
            NULL AS revision,event_kind AS status FROM events WHERE owner_id=? AND workspace_id=?
        UNION ALL SELECT b.batch_id,'batch',b.created_at,NULL,'batch',b.session_id,b.batch_id,NULL,NULL,NULL,b.status
            FROM batches b JOIN sessions s USING(session_id) WHERE b.owner_id=? AND s.owner_id=b.owner_id AND s.workspace_id=?
        UNION ALL SELECT c.proposal_id || ':' || c.memory_id || ':' || c.revision,'memory_' || c.operation,c.created_at,NULL,
            'memory',NULL,p.batch_id,c.memory_id,c.rollout_id,c.revision,c.operation
            FROM rollout_commits c JOIN rollout_runs r USING(rollout_id) LEFT JOIN proposals p USING(proposal_id)
            WHERE r.owner_id=? AND r.workspace_id=?
        UNION ALL SELECT a.receipt_id,a.kind,a.created_at,NULL,'receipt',a.session_id,NULL,NULL,a.rollout_id,NULL,a.kind
            FROM rollout_audit a WHERE a.owner_id=? AND a.workspace_id=? AND a.kind='injection'
        UNION ALL SELECT CAST(o.outbox_id AS TEXT),'projection',o.updated_at,NULL,'projection',NULL,NULL,b.memory_id,o.rollout_id,b.revision,o.state
            FROM projection_operations o JOIN rollout_runs r USING(rollout_id) JOIN outbox b USING(outbox_id)
            WHERE r.owner_id=? AND r.workspace_id=?
        UNION ALL SELECT c.receipt_id,'consumption',c.created_at,NULL,'receipt',NULL,NULL,NULL,c.rollout_id,NULL,c.status
            FROM rollout_consumption_receipts c JOIN rollout_runs r USING(rollout_id) WHERE c.owner_id=? AND r.owner_id=c.owner_id AND r.workspace_id=?"""
    params = (owner_id,workspace_id)*6
    sql = f"SELECT * FROM ({sql}) WHERE julianday(recorded_at)>=julianday(?) AND julianday(recorded_at)<julianday(?)"
    params += (start.isoformat(),end.isoformat())
    if kinds:
        sql += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
        params += tuple(kinds)
    return page(db,sql,params,keys=("recorded_at","object_type","object_id"),
        scope=("activity",owner_id,workspace_id,start.isoformat(),end.isoformat(),sorted(kinds)),cursor=cursor,limit=limit)
