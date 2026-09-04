from __future__ import annotations
import uuid
from .reconcile import compress, correction_plan
from .writer import apply
from .llm import Judge
from .store import Store
from .ov_client import OpenVikingClient
from .transcript import read_incremental

async def run(host: str, session_id: str, store: Store, client: OpenVikingClient, judge: Judge | None = None) -> dict:
    row = store.get_session(host, session_id)
    if not row: return {"status": "missing_session"}
    candidates = store.unconsumed_candidates(host, session_id)
    turns, offset = read_incremental(row["transcript_path"], row["cursor_offset"] or 0)
    summary = compress(turns)
    plans = []
    if judge and candidates:
        deltas = await judge.judge([], candidates, summary)
        for delta in deltas:
            if delta.relation == "new" and delta.type == "dev_correction":
                candidate = next((c for c in candidates if c.text in str(delta.fields) or c.text == delta.key), None)
                if candidate: plans.append(correction_plan(candidate, "viking://~/memories/dev", row["repo_key"]))
    written = await apply(plans, client) if plans else []
    if written: store.consume_candidates(host, session_id)
    store.upsert_session(host, session_id, cursor_offset=offset)
    store.add_trace(str(uuid.uuid4()), "reconcile", host, session_id, {"candidate_count": len(candidates), "planned": len(plans), "written": len(written)})
    return {"status": "ok", "candidates": len(candidates), "written": written}
