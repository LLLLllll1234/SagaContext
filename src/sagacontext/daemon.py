from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .application import Application, CurrentMemoryInput, TaskContextInput
from .config import Config
from .ledger import CommitRequest
from .ledger.schema import SCHEMA_VERSION
from .rollout import RuntimeMode
from .maintenance.judge import OpenAIProposalJudge
from .llm import OpenAIJudge


class ProjectRegistration(BaseModel):
    name: str
    location: Path


class LocationBinding(BaseModel):
    location: Path


class TaskCreation(BaseModel):
    project_id: str
    goal: str


class SessionOpening(BaseModel):
    host: str
    host_session_id: str
    workspace_id: str


class TaskBinding(BaseModel):
    start_event_id: str


class ForgetRequest(BaseModel):
    receipt: str


def _runtime(request: Request) -> Application:
    return request.app.state.runtime


def _invalid(error: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail={"status": "invalid_request", "reason": str(error)})


def _auth_kwargs(request: Request, approver: str | None, key_id: str | None,
                 receipt: str | None, issued_at: str | None, expires_at: str | None) -> dict[str, str | None]:
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    return {"token": token, "key_id": key_id, "issued_at": issued_at, "expires_at": expires_at}


def create_app(config: Config | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(api: FastAPI):
        runtime = Application(config or Config.load())
        api.state.runtime = runtime
        try:
            yield
        finally:
            runtime.close()

    api = FastAPI(title="SagaContext", version="0.1.0", lifespan=lifespan)

    @api.get("/health")
    def health(request: Request):
        runtime = _runtime(request)
        return {
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "ledger_path": str(runtime.ledger.path),
            "host_ingestion": "disabled",
        }

    @api.post("/events")
    async def ingest_event(
        request: Request, host: str = "codex", event: str = "",
        host_version: str = Header("", alias="X-SagaContext-Host-Version"),
        generation: str = Header("", alias="X-SagaContext-Generation"),
    ):
        runtime = _runtime(request)
        try:
            decoded = json.loads((await request.body()).decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            if runtime.rollout.mode.value == "off":
                return JSONResponse(status_code=501, content={"status": "host_ingestion_disabled", "stage": "S1"})
            raise HTTPException(status_code=400, detail={"status": "invalid_request", "reason": "invalid_json"})
        if not isinstance(decoded, dict):
            raise HTTPException(status_code=400, detail={"status": "invalid_request", "reason": "object_required"})
        payload = decoded
        record = {
            **payload,
            "host": host,
            "hook_event_name": event or payload.get("hook_event_name", ""),
            "host_version": host_version or payload.get("host_version", ""),
            "source_generation": generation or payload.get("source_generation", ""),
        }
        if record["hook_event_name"] == "SessionStart":
            output, receipt = runtime.rollout.session_start(
                record, runtime.rollout_backend, query=str(payload.get("query", "workspace"))
            )
            return output
        receipt = runtime.rollout.ingest(record)
        if receipt.reason == "mode_off":
            return JSONResponse(status_code=501, content={"status": "host_ingestion_disabled", "stage": "S1", "sagacontextReceipt": receipt.model_dump(mode="json")})
        return {"status": receipt.status, "reason": receipt.reason}

    @api.post("/rollout/batches/run")
    def rollout_run_batch(payload: dict[str, Any], request: Request):
        runtime = _runtime(request)
        if not runtime.config.llm_base_url or not runtime.config.llm_api_key or not runtime.config.llm_model:
            raise HTTPException(status_code=503, detail={"status": "judge_configuration_missing"})
        session_id = str(payload.get("session_id", ""))
        if not session_id:
            raise HTTPException(status_code=400, detail={"status": "invalid_request", "reason": "session_id_required"})
        judge = OpenAIProposalJudge(OpenAIJudge(runtime.config.llm_base_url, runtime.config.llm_api_key, runtime.config.llm_model, timeout=30.0, temperature=0.0))
        try:
            return runtime.rollout.run_batch(judge, session_id, task_id=payload.get("task_id"))
        except ValueError as error:
            raise _invalid(error) from error

    @api.get("/rollout/audit")
    def rollout_audit(request: Request, kind: str | None = None):
        return _runtime(request).ledger.list_rollout_receipts(kind=kind)

    @api.post("/rollout/mode")
    def rollout_mode(payload: dict[str, Any], request: Request,
                     approver: str | None = Header(None, alias="X-SagaContext-Approver"),
                     key_id: str | None = Header(None, alias="X-SagaContext-Key-Id"),
                     receipt: str | None = Header(None, alias="X-SagaContext-Approval-Receipt"),
                     issued_at: str | None = Header(None, alias="X-SagaContext-Issued-At"),
                     expires_at: str | None = Header(None, alias="X-SagaContext-Expires-At")):
        try:
            runtime = _runtime(request).rollout
            mode = RuntimeMode(payload.get("mode", "off"))
            if mode is RuntimeMode.OFF:
                return runtime.stop(reason=str(payload.get("reason", "operator")))
            return runtime.activate(mode=mode, workspace=str(payload.get("workspace", "")),
                approver=approver or "", approval_receipt=receipt or "",
                deadline=payload["deadline"], max_sessions=int(payload.get("max_sessions", 10)), max_candidates=int(payload.get("max_candidates", 20)),
                generation=payload.get("generation"), rollback_plan=payload.get("rollback_plan"), **_auth_kwargs(request, approver, key_id, receipt, issued_at, expires_at))
        except (ValueError, KeyError, TypeError) as error:
            raise _invalid(error) from error

    @api.post("/rollout/batches/{batch_id}/review")
    def rollout_review(batch_id: str, payload: dict[str, Any], request: Request,
                       approver: str | None = Header(None, alias="X-SagaContext-Approver"),
                       key_id: str | None = Header(None, alias="X-SagaContext-Key-Id"),
                       receipt: str | None = Header(None, alias="X-SagaContext-Approval-Receipt"),
                       issued_at: str | None = Header(None, alias="X-SagaContext-Issued-At"),
                       expires_at: str | None = Header(None, alias="X-SagaContext-Expires-At")):
        try:
            return _runtime(request).rollout.review_batch(
                batch_id,
                payload.get("decision", "reject"),
                reviewer=payload.get("reviewer", "unknown"),
                receipt=receipt or "",
                approver=approver,
                **_auth_kwargs(request, approver, key_id, receipt, issued_at, expires_at),
            )
        except ValueError as error:
            raise _invalid(error) from error

    @api.post("/rollout/grants")
    def rollout_grant(payload: dict[str, Any], request: Request,
                      approver: str | None = Header(None, alias="X-SagaContext-Approver"),
                      key_id: str | None = Header(None, alias="X-SagaContext-Key-Id"),
                      receipt: str | None = Header(None, alias="X-SagaContext-Approval-Receipt"),
                      issued_at: str | None = Header(None, alias="X-SagaContext-Issued-At"),
                      expires_at: str | None = Header(None, alias="X-SagaContext-Expires-At")):
        try:
            if set(payload) != {"action", "target_receipt", "request_digest", "expires_at"}:
                raise ValueError("invalid_grant_fields")
            auth = _auth_kwargs(request, approver, key_id, receipt, issued_at, expires_at)
            return _runtime(request).rollout.register_grant(
                action=payload["action"], target_receipt=payload["target_receipt"],
                request_digest=payload["request_digest"], expires_at=payload["expires_at"],
                token=auth["token"], approver=approver, key_id=key_id, receipt=receipt or "",
                issued_at=issued_at, auth_expires_at=expires_at,
            )
        except (ValueError, TypeError, KeyError) as error:
            raise _invalid(error) from error

    @api.post("/rollout/rollback")
    def rollout_rollback(payload: dict[str, Any], request: Request,
                         approver: str | None = Header(None, alias="X-SagaContext-Approver"),
                         key_id: str | None = Header(None, alias="X-SagaContext-Key-Id"),
                         receipt: str | None = Header(None, alias="X-SagaContext-Approval-Receipt"),
                         issued_at: str | None = Header(None, alias="X-SagaContext-Issued-At"),
                         expires_at: str | None = Header(None, alias="X-SagaContext-Expires-At")):
        try:
            return _runtime(request).rollout.rollback(
                rollout_id=str(payload.get("rollout_id", "")),
                plan_digest=str(payload.get("rollback_plan_digest", "")),
                phase=str(payload.get("phase", "")), receipt=receipt or "",
                approver=approver,
                **_auth_kwargs(request, approver, key_id, receipt, issued_at, expires_at),
            )
        except ValueError as error:
            raise _invalid(error) from error

    @api.post("/rollout/consumption")
    def rollout_consumption(payload: dict[str, Any], request: Request):
        try:
            from .rollout import InjectionReceipt
            receipt = InjectionReceipt.model_validate(payload.get("injection_receipt", payload))
            return _runtime(request).rollout.record_consumption(receipt, result=payload.get("result", {}))
        except (ValueError, TypeError) as error:
            raise _invalid(error) from error

    @api.post("/projects/register")
    def register_project(payload: ProjectRegistration, request: Request):
        try:
            return _runtime(request).ledger.register_project(payload.name, payload.location)
        except ValueError as error:
            raise _invalid(error) from error

    @api.post("/projects/{project_id}/locations")
    def bind_location(project_id: str, payload: LocationBinding, request: Request):
        try:
            workspace_id = _runtime(request).ledger.bind_location(project_id, payload.location)
            return {"project_id": project_id, "workspace_id": workspace_id}
        except ValueError as error:
            raise _invalid(error) from error

    @api.post("/tasks")
    def create_task(payload: TaskCreation, request: Request):
        try:
            task_id = _runtime(request).ledger.create_task(payload.project_id, payload.goal)
            return {"task_id": task_id, "project_id": payload.project_id}
        except ValueError as error:
            raise _invalid(error) from error

    @api.post("/sessions")
    def open_session(payload: SessionOpening, request: Request):
        try:
            session_id = _runtime(request).ledger.open_session(
                payload.host, payload.host_session_id, payload.workspace_id
            )
            return {"session_id": session_id}
        except ValueError as error:
            raise _invalid(error) from error

    @api.post("/sessions/{session_id}/tasks/{task_id}")
    def bind_task(session_id: str, task_id: str, payload: TaskBinding, request: Request):
        try:
            binding_id = _runtime(request).ledger.bind_task(
                session_id, task_id, payload.start_event_id
            )
            return {"binding_id": binding_id, "session_id": session_id, "task_id": task_id}
        except ValueError as error:
            raise _invalid(error) from error

    @api.post("/memories/commit")
    def commit_memory(payload: CommitRequest, request: Request):
        raise HTTPException(status_code=403, detail={"status": "direct_commit_disabled"})

    @api.post("/memories/current")
    def current_memories(payload: CurrentMemoryInput, request: Request):
        runtime = _runtime(request)
        return runtime.ledger.get_current(payload.memory_ids, runtime.task_context(payload.context))

    @api.post("/memories/{memory_id}/history")
    def memory_history(memory_id: str, payload: TaskContextInput, request: Request):
        runtime = _runtime(request)
        return runtime.ledger.read_history(memory_id, runtime.task_context(payload))

    @api.post("/memories/{memory_id}/forget")
    def forget_memory(memory_id: str, payload: ForgetRequest, request: Request):
        raise HTTPException(status_code=403, detail={"status": "direct_forget_disabled"})

    @api.get("/deletions/{job_id}")
    def deletion_status(job_id: str, request: Request):
        result = _runtime(request).ledger.deletion_status(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail={"status": "not_found"})
        return result

    @api.get("/outbox")
    def outbox(request: Request):
        return _runtime(request).ledger.list_outbox()

    return api


app = create_app()


def main() -> None:
    import uvicorn

    config = Config.load()
    uvicorn.run(create_app(config), host=config.host, port=config.port)
