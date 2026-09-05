from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

import typer
from pydantic import BaseModel, ValidationError

from .application import Application, CurrentMemoryInput, TaskContextInput
from .config import Config
from .ledger import CommitRequest


app = typer.Typer(no_args_is_help=True)


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _emit(value: object) -> None:
    typer.echo(json.dumps(_jsonable(value), ensure_ascii=False, indent=2))


def _load_json(source: str) -> object:
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    return json.loads(text)


def _execute(operation: Callable[[Application], object]) -> None:
    try:
        with Application(Config.load()) as runtime:
            result = operation(runtime)
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
        _emit({"status": "invalid_request", "reason": str(error)})
        raise typer.Exit(code=2) from error
    _emit(result)


@app.command("project-register")
def project_register(name: str, location: Path) -> None:
    _execute(lambda runtime: runtime.ledger.register_project(name, location))


@app.command("project-bind")
def project_bind(project_id: str, location: Path) -> None:
    def operation(runtime: Application) -> object:
        workspace_id = runtime.ledger.bind_location(project_id, location)
        return {"project_id": project_id, "workspace_id": workspace_id}

    _execute(operation)


@app.command("project-rebind")
def project_rebind(project_id: str, old_location: Path, new_location: Path) -> None:
    def operation(runtime: Application) -> object:
        workspace_id = runtime.ledger.rebind_location(project_id, old_location, new_location)
        return {"project_id": project_id, "workspace_id": workspace_id}

    _execute(operation)


@app.command("task-create")
def task_create(project_id: str, goal: str) -> None:
    _execute(
        lambda runtime: {
            "task_id": runtime.ledger.create_task(project_id, goal),
            "project_id": project_id,
        }
    )


@app.command("session-open")
def session_open(host: str, host_session_id: str, workspace_id: str) -> None:
    _execute(
        lambda runtime: {
            "session_id": runtime.ledger.open_session(host, host_session_id, workspace_id)
        }
    )


@app.command("task-bind")
def task_bind(session_id: str, task_id: str, start_event_id: str) -> None:
    _execute(
        lambda runtime: {
            "binding_id": runtime.ledger.bind_task(session_id, task_id, start_event_id),
            "session_id": session_id,
            "task_id": task_id,
        }
    )


@app.command("memory-commit")
def memory_commit(input_source: str = typer.Option("-", "--input")) -> None:
    _execute(
        lambda runtime: runtime.ledger.commit(
            CommitRequest.model_validate(_load_json(input_source))
        )
    )


@app.command("memory-current")
def memory_current(input_source: str = typer.Option("-", "--input")) -> None:
    def operation(runtime: Application) -> object:
        payload = CurrentMemoryInput.model_validate(_load_json(input_source))
        return runtime.ledger.get_current(payload.memory_ids, runtime.task_context(payload.context))

    _execute(operation)


@app.command("memory-history")
def memory_history(memory_id: str, input_source: str = typer.Option("-", "--input")) -> None:
    def operation(runtime: Application) -> object:
        payload = TaskContextInput.model_validate(_load_json(input_source))
        return runtime.ledger.read_history(memory_id, runtime.task_context(payload))

    _execute(operation)


@app.command("memory-forget")
def memory_forget(memory_id: str, receipt: str) -> None:
    _execute(lambda runtime: runtime.ledger.forget(memory_id, receipt))


@app.command("deletion-status")
def deletion_status(job_id: str) -> None:
    def operation(runtime: Application) -> object:
        result = runtime.ledger.deletion_status(job_id)
        if result is None:
            raise ValueError("deletion job not found")
        return result

    _execute(operation)


@app.command("outbox-list")
def outbox_list() -> None:
    _execute(lambda runtime: runtime.ledger.list_outbox())


if __name__ == "__main__":
    app()
