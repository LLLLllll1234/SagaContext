# S2 持续维护验收记录

**日期：** 2026-09-05

**范围：** 本地 `ledger-v3.db`、合成 EventJournal、固定 batch、`ScriptedJudge`、人工 review 领域接口、显式调用 projector 与独立 `InMemoryBackendState`。未连接真实 OpenViking，未启用 hooks、自动注入、常驻 worker 或私人 transcript。

## 结论

S2 本地退出条件满足。Schema v2 以原子迁移升级现有 v1 Ledger，迁移前写入的 S1 memory/revision 升级后仍可读取；结构校验拒绝半套迁移和伪造的 v2 标记。旧 `state.db` 不读取、不修改、不创建。

`Application` 只组合 `EventJournal`、`BatchService`、`BatchWorker`、`ReviewService` 和 `Projector`，不启动后台任务。所有 Judge 与 backend 操作由测试或显式本地调用触发。

## Schema 与组合层

| 验收项 | 具名测试 | 结果 |
|---|---|---|
| v1 到 v2 原子升级且 S1 正文可读 | `test_schema_v2_migrates_v1_and_preserves_s1_data` | 通过 |
| 合法但不完整的 v2 迁移整体回滚 | `test_schema_v2_failure_rolls_back_all_v2_changes` | 通过 |
| v2 标记与实际结构不一致时拒绝打开 | `test_schema_v2_marker_without_complete_schema_is_rejected` | 通过 |
| 旧 `state.db` 不变 | `test_schema_v2_never_reads_or_changes_legacy_state_database` | 通过 |
| `CommitBatchPlan` 冻结且不可携带可执行字段 | `test_commit_batch_plan_is_frozen_and_forbids_executable_fields` | 通过 |
| 组合 S2 服务但不启动 worker | `test_application_composes_s2_services_without_starting_workers` | 通过 |

## J1-J4 与 B1-B5

| 编号 | 具名测试 | 结果 |
|---|---|---|
| J1 | `test_j1_event_and_cursor_commit_or_roll_back_together` | 通过 |
| J2 | `test_j2_partial_does_not_advance_and_bad_line_replay_is_idempotent` | 通过 |
| J3 | `test_j3_alias_is_single_target_and_cannot_cross_generation_or_owner` | 通过 |
| J4 | `test_j4_alias_resolves_to_one_canonical_evidence_event` | 通过 |
| B1、B2 | `test_b1_b2_batch_freezes_inputs_and_leaves_new_candidates_pending` | 通过 |
| B3 | `test_b3_batch_and_candidate_tokens_fence_expired_worker` | 通过 |
| B4 | `test_b4_judge_failure_is_retry_not_no_change`、`test_b4_repeated_judge_failure_stops_at_bounded_blocked_state` | 通过 |
| B5 | `test_b5_no_change_is_persisted_and_settled_once` | 通过 |

固定 batch 的摘要覆盖 event、candidate、anchor revision、policy、maintenance schema 和 Judge 版本。`test_r1_judge_version_drift_blocks_fixed_batch_before_judge_call` 进一步证明 Judge 版本漂移会在调用前进入 `blocked`。

## R1-R4、A1-A2 与 C1

| 编号 | 具名测试 | 结果 |
|---|---|---|
| R1 | `test_r1_persisted_proposal_resumes_without_calling_judge_again`、`test_r1_judge_version_drift_blocks_fixed_batch_before_judge_call` | 通过 |
| R2 | `test_r2_stale_anchor_invalidates_proposal_before_rejudging` | 通过 |
| R3 | `test_r3_unknown_non_new_target_is_rejected_not_created` | 通过 |
| R4 | `test_r4_conflict_releases_batch_lease_but_freezes_candidate_until_review`、`test_r4_accept_new_rechecks_head_and_commits_with_idempotent_receipt` | 通过 |
| A1、C1 | `test_a1_c1_commit_batch_rolls_back_memory_checkpoint_and_candidate` | 通过 |
| A2 | `test_a2_stale_expected_revision_never_partially_commits`、`test_a2_batch_confirm_cannot_change_payload_or_omit_evidence` | 通过 |

`commit_batch` 只接收冻结、结构化领域对象，并在一个 Ledger 写事务内完成 revision、evidence、outbox、candidate、batch 与 task checkpoint 更新。batch lease token 和 candidate claim token 任一不匹配均拒绝提交。

## P1-P6 与 G2 时序

| 编号 | 具名测试 | 结果 |
|---|---|---|
| P1 | `test_p1_committed_outbox_survives_until_explicit_drain` | 通过 |
| P2 | `test_p2_unknown_write_is_located_after_client_restart_without_duplicate` | 通过 |
| P3 | `test_p3_late_old_revision_becomes_obsolete_after_new_revision` | 通过 |
| P4 | `test_p4_expired_lease_fences_old_worker_completion` | 通过 |
| P5 | `test_p5_unknown_verification_timeout_is_bounded_and_blocks` | 通过 |
| P6 | `test_p6_duplicate_confirmation_reuses_receipt_and_attempt_numbers_are_unique` | 通过 |

补充测试 `test_locate_result_requires_matching_identity_generation_and_digest` 验证 locate 结果必须同时匹配 operation identity、generation 与 `payload_digest`；`test_backend_adapter_calls_never_run_inside_ledger_write_transaction` 验证后端调用不持有 Ledger 写事务；`test_lease_configuration_must_exceed_backend_timeout_and_margin` 验证 lease 配置不变量。

三组最低 G2 时序的生命周期区分如下：

| 时序 | 客户端状态 | 后端状态 | 已验证结果 |
|---|---|---|---|
| Ledger 已提交、projector 领取前停止 | 未启动或重建 | 初始为空 | outbox 持久存在，显式 drain 后投影一次 |
| 后端已写、客户端确认前超时 | 重建 `InMemoryBackend` 客户端 | 保留同一 `InMemoryBackendState` | 恢复先 locate，不重复 materialize，只写一个 receipt |
| revision 2 已投影、revision 1 worker 迟到 | 旧 worker 继续返回 | 保留包含新旧调用结果的 state | revision 1 只能进入 `obsolete`，显式 cleanup 后读取只保留当前 revision |

清空 `InMemoryBackendState` 表示后端数据丢失，不是“远端已成功、客户端失去确认”。S2 未把该场景计作未知写入恢复成功；只有 adapter 能可靠证明未找到时才可转 `retry`，否则保持可观测的 `unknown/blocked`。

## 固定命令结果

```text
.venv/bin/python -m unittest discover -s tests -v  # Ran 86 tests, OK
.venv/bin/python -m compileall -q src tests        # passed
.venv/bin/python -m pip check                      # No broken requirements found.
git diff --check                                   # passed
```

测试期间 FastAPI `TestClient` 报告上游 `httpx` 兼容层弃用警告，不影响当前验收；依赖升级不属于 S2。

## 保留边界

- Ledger 仍是正文与 revision 的唯一权威；projection payload 始终从指定 Ledger revision 重建。
- daemon `POST /events` 仍固定返回无副作用 HTTP `501`。
- S2 不提供生产 HTTP/CLI 入口，不启动常驻 worker，不配置宿主 hooks 或自动注入。
- `InMemoryBackend` 只是可控故障测试后端；真实 OpenViking、远端删除、检索效果与生产可靠性仍由 S0 探针和 S3 门槛约束。
- cleanup 是显式 locator action，不代表真实 forget 已完成。
