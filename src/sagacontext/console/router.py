from __future__ import annotations

from datetime import datetime
from typing import Annotated
from fastapi import APIRouter, Depends, Query, Request
from sagacontext.ledger import TaskContext
from .security import check_console_access
from .db import ConsoleReadError
from .models import (Envelope,ProjectDirectory,WorkspaceOverview,SessionPage,TaskPage,TaskRow,ActivityPage,
    BatchPage,MemoryPage,SessionDetail,BatchDetail,MemoryDetail,RolloutDetail)

Limit=Annotated[int,Query(ge=1,le=100)]


def _service(request):
    return request.app.state.console


def _context(request,workspace_id,project_id,task_id,paths):
    return TaskContext(owner_id=_service(request).owner_id,workspace_id=workspace_id,
                       project_id=project_id,task_id=task_id,touched_paths=paths)


def create_console_router():
    router=APIRouter(prefix='/console/v1',dependencies=[Depends(check_console_access)])

    @router.get('/projects',response_model=Envelope[ProjectDirectory])
    def projects(request: Request):
        return _service(request).projects()

    @router.get('/workspaces/{workspace_id}/overview',response_model=Envelope[WorkspaceOverview])
    def overview(workspace_id: str,request: Request,start: datetime,end: datetime):
        config=request.app.state.runtime.config
        scheduler=request.app.state.scheduler
        state={'configured_mode':config.rollout_mode,
            'stop_active':config.rollout_stop_file.exists() if config.rollout_stop_file else False,
            'scheduler':'disabled' if scheduler is None else 'error' if scheduler.error_class else 'running'}
        return _service(request).overview(workspace_id,start,end,state)

    @router.get('/workspaces/{workspace_id}/sessions',response_model=Envelope[SessionPage])
    def sessions(workspace_id: str,request: Request,cursor: str | None=None,limit: Limit=50):
        return _service(request).sessions(workspace_id,cursor,limit)

    @router.get('/projects/{project_id}/tasks',response_model=Envelope[TaskPage])
    def tasks(project_id: str,request: Request,workspace_id: str | None=None,cursor: str | None=None,limit: Limit=50):
        return _service(request).tasks(project_id,workspace_id,cursor,limit)

    @router.get('/projects/{project_id}/tasks/{task_id}',response_model=Envelope[TaskRow])
    def task(project_id: str,task_id: str,request: Request,workspace_id: str):
        return _service(request).task(project_id,workspace_id,task_id)

    @router.get('/workspaces/{workspace_id}/activity',response_model=Envelope[ActivityPage])
    def activity(workspace_id: str,request: Request,start: datetime,end: datetime,kinds: Annotated[list[str],Query()]=[],cursor: str | None=None,limit: Limit=50):
        return _service(request).activity(workspace_id,start,end,kinds,cursor,limit)

    @router.get('/workspaces/{workspace_id}/batches',response_model=Envelope[BatchPage])
    def batches(workspace_id: str,request: Request,status: str | None=None,cursor: str | None=None,limit: Limit=50):
        return _service(request).batches(workspace_id,status,cursor,limit)

    @router.get('/workspaces/{workspace_id}/rollouts/{rollout_id}',response_model=Envelope[RolloutDetail])
    def rollout(workspace_id: str,rollout_id: str,request: Request):
        return _service(request).rollout(workspace_id,rollout_id)

    @router.get('/workspaces/{workspace_id}/sessions/{session_id}',response_model=Envelope[SessionDetail])
    def session(workspace_id: str,session_id: str,request: Request):
        return _service(request).session(workspace_id,session_id)

    @router.get('/workspaces/{workspace_id}/batches/{batch_id}',response_model=Envelope[BatchDetail])
    def batch(workspace_id: str,batch_id: str,request: Request,project_id: str | None=None,task_id: str | None=None,touched_paths: Annotated[list[str],Query()]=[]):
        context=_context(request,workspace_id,project_id,task_id,touched_paths) if project_id else None
        if not project_id and (task_id or touched_paths):
            raise ConsoleReadError('invalid_request')
        return _service(request).batch(workspace_id,batch_id,context)

    @router.get('/projects/{project_id}/memories',response_model=Envelope[MemoryPage])
    def memories(project_id: str,request: Request,workspace_id: str,task_id: str | None=None,
                 touched_paths: Annotated[list[str],Query()]=[],cursor: str | None=None,limit: Limit=50,view: str='applicable'):
        return _service(request).memories(project_id,workspace_id,_context(request,workspace_id,project_id,task_id,touched_paths),cursor,limit,view)

    @router.get('/workspaces/{workspace_id}/memories/{memory_id}',response_model=Envelope[MemoryDetail])
    def memory(workspace_id: str,memory_id: str,request: Request,project_id: str | None=None,task_id: str | None=None,touched_paths: Annotated[list[str],Query()]=[]):
        context=_context(request,workspace_id,project_id,task_id,touched_paths) if project_id else None
        if not project_id and (task_id or touched_paths):
            raise ConsoleReadError('invalid_request')
        return _service(request).memory(workspace_id,memory_id,context)

    return router
