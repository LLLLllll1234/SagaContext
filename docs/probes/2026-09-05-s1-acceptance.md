# S1 数据收口验收记录

**日期：** 2026-09-05

**范围：** 本地 Ledger、daemon、CLI、身份 fixture 与测试后端；未连接真实后端，未启用 hooks/transcript，未读取或迁移旧数据。

## 结论

S1 本地数据收口退出条件满足。`Application` 是 daemon/CLI 的组合入口，两个入口统一使用 `${SAGACONTEXT_HOME:-~/.sagacontext}/ledger-v3.db`。入口代码不再导入或构造 `Store`、`OpenVikingClient`；旧源码仍保留，但不在 S1 运行时依赖图内。

`POST /events` 固定返回 HTTP `501` 和 `host_ingestion_disabled`。无效请求体不会被解析，调用前后 Ledger sequence、全部表计数以及旧 `state.db` 的 SHA-256、大小、`mtime_ns` 均不变化；旧库原先不存在时也不会被创建。

## 入口与生命周期

| 验收项 | 具名测试 | 结果 |
|---|---|---|
| 默认 ledger 路径唯一 | `test_config_uses_one_ledger_path_under_sagacontext_home` | 通过 |
| owner 稳定、关闭幂等 | `test_application_reuses_owner_and_closes_idempotently` | 通过 |
| daemon 导入无文件副作用 | `test_daemon_import_has_no_filesystem_side_effect` | 通过 |
| `/events` 501、表/sequence/旧库不变 | `test_events_is_501_and_has_no_database_side_effects` | 通过 |
| `/events` 不创建缺失旧库 | `test_events_does_not_create_missing_legacy_database` | 通过 |
| daemon 项目到删除纵向链路 | `test_local_api_closes_project_task_memory_and_deletion_flow` | 通过 |
| CLI 全命令链、统一路径、旧库不变 | `test_cli_and_daemon_config_share_ledger_and_leave_legacy_state_untouched` | 通过 |
| 旧运行时命令不再注册 | `test_old_runtime_commands_are_not_registered` | 通过 |

## I01–I11

| 不变量 | 具名测试 | 结果 |
|---|---|---|
| I01、I04、I05 | `test_i01_i04_i05_backend_cannot_override_or_run_during_local_commit` | 通过 |
| I02 | `test_i02_read_rejects_forged_workspace_and_task_context`、`test_authoritative_read_rechecks_project_task_and_path_scope` | 通过 |
| I03 | `test_i03_revision_evidence_and_outbox_share_commit`、`test_i03_commit_failure_rolls_back_head_revision_and_outbox` | 通过 |
| I06 | `test_i06_replayed_source_claim_is_one_independent_evidence` | 通过 |
| I07 | `test_i07_stale_revision_conflicts_without_overwrite` | 通过 |
| I08、I09 | `test_i08_i09_retired_memory_is_not_current_and_scope_cannot_expand` | 通过 |
| I10、I11 | `test_i10_i11_verification_is_independent_and_forget_differs_from_retire` | 通过 |

补充删除测试覆盖相同 receipt 幂等、receipt 跨 memory 重用拒绝，以及旧 evidence 重放不能复活已删除记忆。

## G4 身份 fixture

`tests/fixtures/identity/g4.json` 固定七类预期：

- 同项目 worktree 只有显式绑定后共享 project，workspace 独立；
- 未确认 fork 与 clone 分别隔离；
- monorepo 根绑定继承与显式子项目最长路径匹配；
- 移动目录显式 rebind 后保留 workspace ID；
- 同一分支的并行 task 使用不同 task ID，session 切换不暂停另一任务。

对应测试为 `test_g4_fixture_declares_all_identity_scenarios`、`test_g4_worktree_fork_and_clone_boundaries`、`test_g4_monorepo_longest_location_and_explicit_subproject`、`test_g4_move_preserves_workspace_and_parallel_tasks_stay_active`，全部通过。

## 固定命令结果

```text
.venv/bin/python -m unittest discover -s tests -v  # Ran 53 tests, OK
.venv/bin/python -m compileall -q src tests        # passed
.venv/bin/pip check                                # No broken requirements found
git diff --check                                   # passed
```

入口依赖扫描确认 `application.py`、`daemon.py`、`cli.py` 不包含 `Store` 或 `OpenVikingClient` 引用。

测试期间 FastAPI `TestClient` 报告上游 `httpx` 兼容层弃用警告，不影响当前测试结果；升级测试客户端依赖应单独处理，不作为 S1 功能通过的证据。

## 保留边界

- outbox 只创建和查询，不消费；真实 projector 属于 S2/S3。
- `local_redacted` 与 `remote_pending` 可查询，S1 不返回远端删除 `completed`。
- 旧 `state.db` 不迁移；旧 Store/reconcile/compliance/capture/transcript 源码尚未删除。
- G1 真实后端与 G3 真实宿主仍未通过，继续禁止真实投影、自动宿主接入和私人数据导入。
