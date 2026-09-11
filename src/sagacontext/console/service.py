"""Read-only application facade for console queries and consistent overviews."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sagacontext.daily_report import report
from sagacontext.ledger.schema import SCHEMA_VERSION
from . import queries
from .db import ConsoleReadError, read_snapshot
from .runtime import ReadLedger, effective_state
from .serialize import safe_payload, safe_text


class ConsoleReadService:
    def __init__(self, path: Path, owner_id: str):
        self.path, self.owner_id = path, owner_id

    def _read(self, operation):
        try:
            with read_snapshot(self.path) as db:
                data = operation(db)
                meta = {"observed_at":datetime.now(timezone.utc).isoformat(),"schema_version":SCHEMA_VERSION,
                    "ledger_sequence":int(db.execute("SELECT value FROM ledger_meta WHERE key='sequence'").fetchone()[0]),"request_id":str(uuid.uuid4())}
                return {"meta":meta,"data":data}
        except sqlite3.Error as error:
            raise ConsoleReadError("ledger_busy" if "locked" in str(error).lower() or "busy" in str(error).lower() else "data_invalid") from None

    def projects(self):
        def operation(db):
            result=[]
            for row in db.execute("SELECT project_id,name FROM projects WHERE owner_id=? ORDER BY name,project_id", (self.owner_id,)):
                items=[]
                seen=set()
                for location in db.execute("SELECT workspace_id,realpath FROM project_locations WHERE owner_id=? AND project_id=? ORDER BY workspace_id",(self.owner_id,row["project_id"])):
                    if location["workspace_id"] not in seen:
                        items.append({"workspace_id":location["workspace_id"],"name":safe_text(Path(location["realpath"]).name)})
                        seen.add(location["workspace_id"])
                result.append({"project_id":row["project_id"],"name":safe_text(row["name"]),"workspaces":items})
            return {"items":result}
        return self._read(operation)

    def sessions(self, workspace_id, cursor=None, limit=50):
        return self._read(lambda db:queries.sessions(db,self.owner_id,workspace_id,cursor,limit))

    def tasks(self, project_id, workspace_id=None, cursor=None, limit=50):
        return self._read(lambda db:queries.tasks(db,self.owner_id,project_id,workspace_id,cursor,limit))

    def task(self, project_id, workspace_id, task_id):
        return self._read(lambda db:queries.task(db,self.owner_id,project_id,workspace_id,task_id))

    def batches(self, workspace_id, status=None, cursor=None, limit=50):
        return self._read(lambda db:queries.batches(db,self.owner_id,workspace_id,status,cursor,limit))

    def activity(self, workspace_id, start, end, kinds=(), cursor=None, limit=50):
        self._window(start,end)
        return self._read(lambda db:queries.activity(db,self.owner_id,workspace_id,start,end,kinds,cursor,limit))

    @staticmethod
    def _window(start,end):
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ConsoleReadError("invalid_request")

    def _rollout(self, db, workspace_id, rollout_id):
        queries.workspace(db,self.owner_id,workspace_id)
        row=db.execute("SELECT rollout_id,mode,status,started_at,deadline,stop_reason,generation FROM rollout_runs WHERE owner_id=? AND workspace_id=? AND rollout_id=?",(self.owner_id,workspace_id,rollout_id)).fetchone()
        if row is None:
            raise ConsoleReadError("not_found")
        stats=report(ReadLedger(db,self.owner_id),rollout_id)
        fields=("session_reservations","session_limit","candidate_reservations","candidate_limit","batches","reviews","proposals","judge_latency_ms","consumption_reports","observations","projection_states","rollback","quality_status")
        return safe_payload({**dict(row),**{key:stats[key] for key in fields}})

    def rollout(self,workspace_id,rollout_id):
        return self._read(lambda db:self._rollout(db,workspace_id,rollout_id))

    def session(self,workspace_id,session_id):
        from .details import session
        return self._read(lambda db:session(db,self.owner_id,workspace_id,session_id))

    def batch(self,workspace_id,batch_id,context=None):
        from .details import batch
        return self._read(lambda db:batch(db,self.owner_id,workspace_id,batch_id,context))

    def memory(self,workspace_id,memory_id,context=None):
        from .details import memory
        return self._read(lambda db:memory(db,self.owner_id,workspace_id,memory_id,context))

    def memories(self,project_id,workspace_id,context=None,cursor=None,limit=50,view='applicable'):
        from .details import memories
        return self._read(lambda db:memories(db,self.owner_id,project_id,workspace_id,context,cursor,limit,view))

    def overview(self,workspace_id,start,end,runtime):
        self._window(start,end)
        def operation(db):
            identity=queries.workspace(db,self.owner_id,workspace_id)
            def module(fn):
                try:
                    return {"availability":"available","value":fn(),"reason":None}
                except (sqlite3.Error,ValueError,TypeError,KeyError):
                    return {"availability":"unavailable","value":None,"reason":"data_invalid"}
            latest=db.execute("SELECT rollout_id,mode,status,deadline FROM rollout_runs WHERE owner_id=? AND workspace_id=? ORDER BY created_at DESC,rollout_id DESC LIMIT 1",(self.owner_id,workspace_id)).fetchone()
            run=dict(latest) if latest else None
            live=db.execute("SELECT workspace_id FROM rollout_runs WHERE owner_id=? AND status IN ('running','draining','stopping','cleanup_required') LIMIT 1",(self.owner_id,)).fetchone()
            state={**effective_state(run,runtime.get("configured_mode","off"),runtime.get("stop_active",True),datetime.now(timezone.utc)),
                "scheduler":runtime.get("scheduler","unknown"),"other_workspace_id":live[0] if live and live[0]!=workspace_id else None}
            def changes():
                operations={r[0]:r[1] for r in db.execute("SELECT c.operation,COUNT(*) FROM rollout_commits c JOIN rollout_runs r USING(rollout_id) WHERE r.owner_id=? AND r.workspace_id=? AND julianday(c.created_at)>=julianday(?) AND julianday(c.created_at)<julianday(?) GROUP BY c.operation",(self.owner_id,workspace_id,start.isoformat(),end.isoformat()))}
                projection={r[0]:r[1] for r in db.execute("SELECT o.state,COUNT(*) FROM projection_operations o JOIN rollout_runs r USING(rollout_id) WHERE r.owner_id=? AND r.workspace_id=? GROUP BY o.state",(self.owner_id,workspace_id))}
                return {"operations":operations,"projection_states":projection,"start":start.isoformat(),"end":end.isoformat()}
            return {"workspace_id":workspace_id,"project_id":identity["project_id"],
                "runtime":module(lambda:state),
                "tasks":module(lambda:queries.tasks(db,self.owner_id,identity["project_id"],workspace_id,limit=5,current_only=True)),
                "sessions":module(lambda:queries.sessions(db,self.owner_id,workspace_id,limit=5)),
                "rollout":module(lambda:self._rollout(db,workspace_id,run["rollout_id"]) if run else None),
                "memory_changes":module(changes),
                "activity":module(lambda:queries.activity(db,self.owner_id,workspace_id,start,end,limit=20))}
        return self._read(operation)
