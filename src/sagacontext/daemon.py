from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .application import Application, CurrentMemoryInput, TaskContextInput
from .config import Config
from .ledger import CommitRequest
from .ledger.schema import SCHEMA_VERSION


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

    @api.post("/events", status_code=501)
    def events_disabled():
        return {"status": "host_ingestion_disabled", "stage": "S1"}

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
        try:
            return _runtime(request).ledger.commit(payload)
        except ValueError as error:
            raise _invalid(error) from error

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
        return _runtime(request).ledger.forget(memory_id, payload.receipt)

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
