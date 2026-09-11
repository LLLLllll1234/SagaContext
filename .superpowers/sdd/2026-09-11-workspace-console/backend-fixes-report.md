# Backend review fixes

Implemented all five findings from `backend-review.md`.

- Proposal details resolve committed output memories through `rollout_commits` and fail closed when an output is deleted, out of scope, or missing for a committed proposal. The fixture now models `new` proposals with `target_id=NULL`.
- Memory directory payload JSON remains intact until `json.loads`; redaction is then applied recursively to parsed values.
- Workspace overview tasks require an active task with a current binding in that workspace. Checkpoints are attributed only inside each binding's persisted event interval.
- A persisted, active guarded rollout remains effectively guarded when configuration has changed to shadow, matching `RolloutRuntime` behavior.
- Added direct scoped task detail: `GET /console/v1/projects/{project_id}/tasks/{task_id}?workspace_id=...`, returning `Envelope[TaskRow]` without pagination.

Regression coverage includes deleted committed new proposals, JSON redaction cases, overview workspace/status filtering, checkpoint binding intervals, configured-shadow/persisted-guarded runtime state, scoped task detail, schema migration sequence, invalid ledger sequence, and busy database handling.

Validation: `PYTHONPATH=src /Users/lqy0584/Downloads/SagaContext/.venv/bin/python -m pytest tests/console -q` (`45 passed`).

DTO note: `TaskRow` contains `task_id`, `project_id`, goal/status/timestamps, latest scoped `session_id`, and scoped `checkpoint_at`. It does not carry navigation context. Callers opening batch or memory details from a task should retain `project_id` and `task_id` in those existing detail query parameters.
