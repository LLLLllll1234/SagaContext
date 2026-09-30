"""FastAPI router for Memory Lab. All endpoints are GET and read-only."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from . import claude_data, codex_data, lifecycle, source_facts

STATIC = Path(__file__).parent / "static"
router = APIRouter(prefix="/memlab", tags=["memlab"])


def _home() -> Path:
    return codex_data.default_codex_home()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@router.get("/static/{name}", include_in_schema=False)
def static(name: str) -> FileResponse:
    path = (STATIC / name).resolve()
    if not path.is_file() or STATIC.resolve() not in path.parents:
        raise HTTPException(404)
    return FileResponse(path)


@router.get("/api/overview")
def overview() -> dict[str, Any]:
    src = source_facts.verify_all()
    jobs = codex_data.memory_jobs(_home())
    stage1_jobs = [j for j in jobs if j["kind"] == "memory_stage1"]
    errors = [j for j in stage1_jobs if j["status"] == "error"]
    health = {"evidence": "local_data", "stage1_jobs": len(stage1_jobs), "stage1_errors": len(errors),
              "last_error": errors[0]["last_error"] if errors else None,
              "last_error_at": errors[0]["started_at"] if errors else None,
              "last_success_at": next((j["finished_at"] for j in stage1_jobs if j["status"] == "done"), None),
              "last_consolidation": next((j for j in jobs if j["kind"] == "memory_consolidate_global"), None)}
    return {
        "codex": {"home": codex_data.home_overview(_home()), "source": src["source"], "fact_counts": src["counts"], "health": health},
        "claude": claude_data.overview(),
        "evidence_legend": {
            "source": "源码事实：在本地 Codex 源码中用锚点定位并可复核",
            "local_data": "本地数据事实：直接读自 ~/.codex 或 ~/.claude 的文件与 SQLite",
            "inference": "系统推断：本工具把源码与数据拼接后得到的结论，可能有误",
        },
    }


@router.get("/api/codex/facts")
def codex_facts(topic: str | None = None) -> dict[str, Any]:
    data = source_facts.verify_all()
    if topic:
        data["facts"] = [f for f in data["facts"] if f["topic"] == topic]
    return data


@router.get("/api/codex/assets")
def codex_assets() -> dict[str, Any]:
    data = lifecycle.build_lifecycles(_home())
    return {"assets": data["assets"], "counts": data["counts"], "summary": codex_data.memory_summary(_home()),
            "skills": codex_data.home_overview(_home())["skills"], "ad_hoc_notes": codex_data.ad_hoc_notes(_home()),
            "git_log": codex_data.memories_git_log(_home()), "consolidations": data["consolidations"]}


@router.get("/api/codex/lifecycles")
def codex_lifecycles() -> dict[str, Any]:
    data = lifecycle.build_lifecycles(_home())
    return {"lifecycles": data["lifecycles"], "counts": data["counts"]}


@router.get("/api/codex/stage1")
def codex_stage1() -> dict[str, Any]:
    return {"rows": codex_data.stage1_outputs(_home()), "jobs": codex_data.memory_jobs(_home())}


@router.get("/api/codex/summaries")
def codex_summaries() -> dict[str, Any]:
    return {"files": codex_data.rollout_summaries(_home())}


@router.get("/api/codex/threads")
def codex_threads(limit: int = Query(200, le=2000)) -> dict[str, Any]:
    return {"threads": codex_data.list_threads(_home(), limit=limit)}


@router.get("/api/codex/rollout/{thread_id}")
def codex_rollout(thread_id: str, text: bool = True) -> dict[str, Any]:
    data = codex_data.read_rollout(_home(), thread_id, include_text=text)
    if not data.get("found"):
        raise HTTPException(404, f"未找到线程 {thread_id} 的 rollout 文件")
    data["curve"] = lifecycle.context_curve(data)
    return data


@router.get("/api/codex/citations")
def codex_citations() -> dict[str, Any]:
    return {"citations": codex_data.scan_citations(_home())}


@router.get("/api/claude/sessions")
def claude_sessions() -> dict[str, Any]:
    return {"sessions": claude_data.list_sessions()}


@router.get("/api/claude/session/{session_id}")
def claude_session(session_id: str) -> dict[str, Any]:
    data = claude_data.read_session(None, session_id)
    if not data.get("found"):
        raise HTTPException(404)
    data["curve"] = lifecycle.context_curve(data)
    return data
