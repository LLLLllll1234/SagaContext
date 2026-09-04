from __future__ import annotations
import uuid
from .reconcile import compress, evolve
from .writer import apply
from .llm import Judge
from .store import Store
from .ov_client import OpenVikingClient
from .transcript import read_incremental
from .anchors import select as select_anchors
from .tasks import apply_delta as apply_task_delta

async def run(host: str, session_id: str, store: Store, client: OpenVikingClient, judge: Judge | None = None, dev_root: str = "viking://~/memories/dev") -> dict:
    row = store.get_session(host, session_id)
    if not row: return {"status": "missing_session"}
    candidates = store.unconsumed_candidates(host, session_id)
    turns, offset = read_incremental(row["transcript_path"], row["cursor_offset"] or 0)
    summary = compress(turns)
    plans = []
    pending = []
    anchors = await select_anchors(host, session_id, summary, dev_root, store, client) if judge and candidates else []
    if judge and candidates:
        deltas = await judge.judge([{"uri": a.uri, "type": a.type, "fields": a.fields} for a in anchors], candidates, summary)
        for delta in deltas:
            existing = next((a for a in anchors if a.uri == delta.anchor_uri), None)
            if delta.relation != "new" and existing is None:
                delta.relation, delta.anchor_uri = "new", None
            delta_plans, delta_pending = evolve(existing, delta, dev_root, row["repo_key"])
            plans.extend(delta_plans); pending.extend(delta_pending)
    written = await apply(plans, client) if plans else []
    complete = bool(plans) and len(written) == len(plans)
    if complete:
        store.add_pending(pending)
        store.consume_candidates(host, session_id)
        if row["task_id"]:
            for delta in deltas:
                if delta.type == "dev_task": apply_task_delta(store.db, row["task_id"], delta.fields)
    store.upsert_session(host, session_id, cursor_offset=offset)
    store.add_trace(str(uuid.uuid4()), "reconcile", host, session_id, {"candidate_count": len(candidates), "anchor_count": len(anchors), "planned": len(plans), "written": len(written), "pending": len(pending)})
    return {"status": "ok", "candidates": len(candidates), "written": written, "pending": len(pending)}
