from __future__ import annotations
import asyncio
from fastapi import FastAPI, Request
from .config import Config
from .models import HostEvent
from .scope import resolve
from .store import Store
from .ov_client import OpenVikingClient
from .recall import records, render
from .capture import detect
from .transcript import read_incremental
from .reconcile import correction_plan
import uuid

cfg = Config.load(); store = Store(cfg.state_path); ov = OpenVikingClient(cfg.ov_base_url, cfg.ov_api_key)
app = FastAPI(title="SagaContext", version="0.1.0")

def _normalize(host: str, event: str, raw: dict) -> HostEvent:
    return HostEvent(host=host, event=event, session_id=str(raw.get("session_id", "unknown")), cwd=raw.get("cwd", "."), transcript_path=raw.get("transcript_path"), prompt=raw.get("prompt") or raw.get("user_prompt"), raw=raw)

@app.get("/health")
async def health(): return {"status": "ok"}

@app.post("/events")
async def events(request: Request):
    host, event = request.query_params.get("host", "codex"), request.query_params.get("event", "prompt")
    try: raw = await request.json()
    except Exception: raw = {}
    try:
        ev = _normalize(host, event, raw); info = resolve(ev.cwd, store)
        store.upsert_session(ev.host, ev.session_id, cwd=str(ev.cwd), repo_key=info["repo_key"], branch=info["branch"], transcript_path=str(ev.transcript_path) if ev.transcript_path else None)
        if ev.event == "prompt" and ev.prompt:
            candidates = detect(ev.prompt)
            store.add_candidates(ev.host, ev.session_id, candidates)
            row = store.get_session(ev.host, ev.session_id)
            if row and ev.transcript_path:
                turns, new_offset = read_incremental(ev.transcript_path, row["cursor_offset"] or 0)
                store.upsert_session(ev.host, ev.session_id, cursor_offset=new_offset, turn_count=(row["turn_count"] or 0) + 1, token_estimate=(row["token_estimate"] or 0) + len(ev.prompt) // 3)
        if ev.event in ("stop", "session_end", "pre_compact"):
            store.upsert_session(ev.host, ev.session_id, ended=1 if ev.event == "session_end" else 0)
            # Deterministic fallback: preserve L0 evidence as plans for a future
            # provider-backed judge; do not write to OV without semantic review.
            row = store.get_session(ev.host, ev.session_id)
            if row:
                plans = [correction_plan(c, cfg.dev_root, row["repo_key"]) for c in store.unconsumed_candidates(ev.host, ev.session_id) if c.kind == "explicit_negation"]
                store.add_trace(str(uuid.uuid4()), "reconcile_plan", ev.host, ev.session_id, {"reason": ev.event, "plans": [p.uri for p in plans]})
            return {"reconcile": "scheduled", "reason": ev.event} if ev.event == "session_end" else {}
        if ev.event not in ("session_start", "prompt"): return {}
        targets = [f"{cfg.dev_root}/convention/global/", f"{cfg.dev_root}/convention/repo-{info['repo_key']}/"]
        query = ev.prompt or ""
        try: found = await asyncio.wait_for(ov.find(query, targets, limit=12 if ev.event == "session_start" else 6, score_threshold=0.0), cfg.recall_budget_tokens / 1000)
        except Exception: found = []
        items = records(found)
        for item in items: store.remember_recalled(ev.host, ev.session_id, item.uri, item.type, item.score, ev.event)
        context = render(items, cfg.recall_budget_tokens if ev.event == "session_start" else cfg.prompt_budget_tokens)
        if not context: return {}
        hook_name = "SessionStart" if ev.event == "session_start" else "UserPromptSubmit"
        return {"hookSpecificOutput": {"hookEventName": hook_name, "additionalContext": context}}
    except Exception:
        return {}

def main():
    import uvicorn
    uvicorn.run(app, host=cfg.host, port=cfg.port)
