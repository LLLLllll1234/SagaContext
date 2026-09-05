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
from .transcript import read_incremental, read_edit_attempts
from .reconcile import correction_plan
from .runner import run as run_reconcile
from .compliance import compile_rules, evaluate, run_commands, violation_candidate
from .llm import OpenAIJudge
import uuid
from pathlib import Path
from .revert import detect as detect_reverts, file_sha
from .tasks import create as create_task, resume_candidate, touch as touch_task

cfg = Config.load(); store = Store(cfg.state_path); ov = OpenVikingClient(cfg.ov_base_url, cfg.ov_api_key)
judge = OpenAIJudge(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model) if cfg.llm_base_url and cfg.llm_model else None
app = FastAPI(title="SagaContext", version="0.1.0")

def _tool_input(raw: dict) -> tuple[str, str, str]:
    tool = raw.get("tool_input") or raw.get("tool", {}).get("input") or raw.get("tool") or {}
    if not isinstance(tool, dict): return "", "", str(tool)
    name = str(raw.get("tool_name") or raw.get("tool", {}).get("name") or "")
    path = str(tool.get("file_path") or tool.get("path") or "")
    content = str(tool.get("new_string") or tool.get("content") or tool.get("patch") or tool)
    return name, path, content

async def _command_checks(ev: HostEvent):
    results = await asyncio.to_thread(run_commands, store.compliance_rules(ev.host, ev.session_id), ev.cwd)
    for result in results: store.add_violation(ev.host, ev.session_id, result.uri, "", result.reason)

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
            if any(marker in ev.prompt.lower() for marker in ("这次就这样", "仅这一次", "just this once")):
                store.soften_compliance_after_override(ev.host, ev.session_id)
            candidates = detect(ev.prompt) + detect_reverts(ev.host, ev.session_id, ev.cwd, store)
            store.add_candidates(ev.host, ev.session_id, candidates)
            violations = store.pending_violations(ev.host, ev.session_id)
            violation_context = "\n".join(f'<memory uri="{row["uri"]}" type="compliance_violation" layer="preference" scope="repo" confidence="1.00">{row["reason"]}</memory>' for row in violations)
            if violations:
                store.add_candidates(ev.host, ev.session_id, [violation_candidate(type("R", (), {"reason": row["reason"], "uri": row["uri"]})(), row["path"]) for row in violations])
                store.mark_violations_injected([row["id"] for row in violations])
            row = store.get_session(ev.host, ev.session_id)
            task_candidate = next((candidate for candidate in candidates if candidate.kind == "task_stmt"), None)
            if row and task_candidate and not row["task_id"]:
                task = create_task(store.db, info["repo_key"], info["branch"], task_candidate.text, cfg.dev_root)
                store.upsert_session(ev.host, ev.session_id, task_id=task["task_id"])
            if row and ev.transcript_path:
                turns, new_offset = read_incremental(ev.transcript_path, row["cursor_offset"] or 0)
                store.upsert_session(ev.host, ev.session_id, cursor_offset=new_offset, turn_count=(row["turn_count"] or 0) + 1, token_estimate=(row["token_estimate"] or 0) + len(ev.prompt) // 3)
        if ev.event == "post_tool":
            tool = raw.get("tool") or raw.get("tool_input") or {}
            name = raw.get("tool_name") or tool.get("name", "")
            path_value = tool.get("file_path") or tool.get("path")
            if name in {"Edit", "Write", "MultiEdit", "apply_patch"} and path_value:
                path = Path(path_value)
                if not path.is_absolute(): path = ev.cwd / path
                if path.exists() and path.is_file(): store.add_tool_edit(ev.host, ev.session_id, str(path_value), file_sha(path), str(raw.get("tool_call_id", "")))
            _, checked_path, checked_content = _tool_input(raw)
            if ev.host == "codex" and checked_path:
                for result in evaluate(store.compliance_rules(ev.host, ev.session_id), checked_path, checked_content):
                    store.add_violation(ev.host, ev.session_id, result.uri, checked_path, result.reason)
            return {}
        if ev.event == "pre_tool":
            _, path, content = _tool_input(raw)
            results = evaluate(store.compliance_rules(ev.host, ev.session_id), path, content)
            for result in results: store.add_violation(ev.host, ev.session_id, result.uri, path, result.reason)
            blocked = next((result for result in results if result.decision == "deny"), None)
            warnings = [result for result in results if result.decision == "warn"]
            if blocked or warnings:
                reason = f"{blocked.reason} ({blocked.uri})" if blocked else "; ".join(f"{result.reason} ({result.uri})" for result in warnings)
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny" if blocked else "allow", "permissionDecisionReason": reason, "additionalContext": reason}}
        if ev.event in ("stop", "session_end", "pre_compact"):
            store.upsert_session(ev.host, ev.session_id, ended=1 if ev.event == "session_end" else 0)
            # Deterministic fallback: preserve L0 evidence as plans for a future
            # provider-backed judge; do not write to OV without semantic review.
            row = store.get_session(ev.host, ev.session_id)
            if row:
                if ev.host == "codex":
                    attempts, compliance_offset = read_edit_attempts(row["transcript_path"], store.compliance_cursor(ev.host, ev.session_id))
                    for checked_path, checked_content in attempts:
                        for result in evaluate(store.compliance_rules(ev.host, ev.session_id), checked_path, checked_content):
                            store.add_violation(ev.host, ev.session_id, result.uri, checked_path, result.reason)
                    store.set_compliance_cursor(ev.host, ev.session_id, compliance_offset)
                if row["task_id"]: touch_task(store.db, row["task_id"])
                plans = [correction_plan(c, cfg.dev_root, row["repo_key"]) for c in store.unconsumed_candidates(ev.host, ev.session_id) if c.kind == "explicit_negation"]
                store.add_trace(str(uuid.uuid4()), "reconcile_plan", ev.host, ev.session_id, {"reason": ev.event, "plans": [p.uri for p in plans]})
                asyncio.create_task(run_reconcile(ev.host, ev.session_id, store, ov, judge, cfg.dev_root))
                asyncio.create_task(_command_checks(ev))
            return {"reconcile": "scheduled", "reason": ev.event} if ev.event == "session_end" else {}
        if ev.event not in ("session_start", "prompt"): return {}
        targets = [f"{cfg.dev_root}/convention/global/", f"{cfg.dev_root}/convention/repo-{info['repo_key']}/"]
        query = ev.prompt or ""
        try: found = await asyncio.wait_for(ov.find(query, targets, limit=12 if ev.event == "session_start" else 6, score_threshold=0.0), cfg.recall_budget_tokens / 1000)
        except Exception: found = []
        items = records(found)
        if ev.event == "session_start": store.replace_compliance_rules(ev.host, ev.session_id, compile_rules(items))
        for item in items: store.remember_recalled(ev.host, ev.session_id, item.uri, item.type, item.score, ev.event)
        context = render(items, cfg.recall_budget_tokens if ev.event == "session_start" else cfg.prompt_budget_tokens)
        if ev.event == "prompt" and 'violation_context' in locals() and violation_context:
            context = f"{violation_context}\n{context}" if context else violation_context
        if ev.event == "session_start":
            task = resume_candidate(store.db, info["repo_key"], info["branch"])
            if task and (not info["branch"] or task["branch"] == info["branch"]):
                store.upsert_session(ev.host, ev.session_id, task_id=task["task_id"])
                task_block = f'<memory uri="{task["uri"]}" type="dev_task" layer="task" scope="repo" confidence="1.00">Goal: {task["goal"]}</memory>'
                context = f"{task_block}\n{context}" if context else task_block
        if not context: return {}
        hook_name = "SessionStart" if ev.event == "session_start" else "UserPromptSubmit"
        return {"hookSpecificOutput": {"hookEventName": hook_name, "additionalContext": context}}
    except Exception:
        return {}

def main():
    import uvicorn
    uvicorn.run(app, host=cfg.host, port=cfg.port)
