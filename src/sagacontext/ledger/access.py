"""Shared memory scope matching; callers must validate context identity first."""
from pathlib import Path
from .models import Scope, TaskContext


def scope_allows(scope: Scope, context: TaskContext) -> bool:
    if scope.kind == "global":
        return True
    if scope.project_id != context.project_id:
        return False
    if scope.kind == "project":
        return True
    if scope.kind == "task":
        return scope.task_id == context.task_id
    root = Path("/")
    return any((root/path).match(scope.path_pattern or "") for path in context.touched_paths if not Path(path).is_absolute())
