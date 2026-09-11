# 2026-09-11 离线 Scheduler 与运维验收

后续更新：[真实 Scheduler 闭环](2026-09-11-scheduler-real-acceptance.md) 已在同一 Codex 模型下通过；本报告保留跳过 provider 时的离线验收范围和状态快照。

本轮按授权跳过 Codex provider 超时排查，完成不依赖模型服务的 scheduler 故障恢复与 operator 验收。最终离线运行 **34/34** 检查通过，全量回归 **246 tests、96 subtests** 通过。真实 workspace 保持 `off + STOP`，未扩配额、未进入 `active`。

这份证据只覆盖临时 workspace、合成事件、ScriptedJudge 和 InMemoryBackend。人工审核操作由验收脚本显式模拟，下一会话召回由 runtime API 模拟；没有运行 Codex，也没有新增真实宿主消费回执。provider 请求数、真实日常质量样本数均为 **0**。已有受控真实闭环和尚未完成的 scheduler-driven 宿主验收，仍以各自报告为准。

## 本轮修复

- 质量报告的顶层指标改为引用已标注对象，明确 `rate_basis=operator_labeled_targets_only`。`evaluated_samples=yes+no`；`unknown` 单独展示，不算消费或未消费；零有效样本显示 `null`。
- annotation 校验字段类型、owner/rollout/target 归属和全局 receipt 冲突。相同 target/kind/value 重复提交只计一次，冲突值拒绝，两个并发写入也不会重复计数。
- 审核修改标注要求已存在 review receipt；错误召回和消费标注只接受已 emitted 且 memory_ids 非空的 injection。操作者标注不生成宿主消费回执。
- `pending` 显示 proposal 的 candidate ID；`report.observation_targets` 给出可标注的 ID，`report.rollback` 汇总清理目标、保留审计行、补偿回执和持久化残留状态。
- `report` 缺少 rollout ID、`annotate` 输入非 JSON object 时明确拒绝。
- 新增可独立执行的离线 runner，验证 scheduler 产生 proposal、重启等待审核、审核提交、projection、模拟召回及 rollout 回滚。测试禁止它访问网络或加载本机配置。

## 最终运行

[完整 artifact](../../artifacts/probes/20260911-offline-scheduler/acceptance-02.json)。每条场景独立 rollout，数据没有合并。

| 场景 | 检查 | 候选 | memory 清理目标 | projection 清理目标 | 最终残留 |
|---|---:|---:|---:|---:|---|
| shadow | 15/15 | 1 | 0 | 0 | Ledger/backend 均 0 |
| guarded | 19/19 | 1 | 1 | 1 | Ledger/backend 均 0 |

两条场景均验证：重启不绕过审核、相同 rollback 请求重复回执幂等、新回执重新校验、结束模式为 off、临时目录删除。guarded 的 `consumed=unknown` 仅用于验证统计口径，消费率为 `null`，真实宿主消费回执数为 0。

回滚中的 candidate quarantine、memory tombstone、event/control receipt 是审计保留，不等同于仍可召回的残留。artifact 分别记录 `before_cleanup`、`after_cleanup`、`final_report`；backend 零残留另行检查。报告中的持久化计数不能替代外部 backend 的实时验证。本次临时目录在生成报告前已删除。

调试记录保留：9 月 10 日 `acceptance-01` 因 runner 把临时配置设为 off 而被 activation 正确拒绝；`acceptance-02` 因同一回执重试时改变授权时间而被正确拒绝（首轮清理已完成）；`acceptance-03` 修正调用后通过。9 月 11 日 `acceptance.json` 为增加 observation_targets 前的通过版本。最终结论仅采用上述最新完整运行，不拼接不同 artifact。

## 故障与命令验证

`tests/test_daily_scheduler.py` 共 19 项，包括原有 checkpoint、lease/restart、并发 worker、STOP、retry/backoff、deadline、10-session draining、candidate quota、晚到 projection 补偿，以及新增：

- backend 删除失败持久化 `cleanup_required`；重启后修复 backend，使用新授权回执完成精确删除；失败和成功 receipt 均可幂等查询。
- 标注冲突、类型错误、不可用注入、尚未审核对象、并发去重和有效分母。
- CLI `pending → review reject → annotate → report` 经真实 FastAPI 路由运行；测试中使用临时 token 和进程内 HTTP transport。
- shadow/guarded 离线 runner 禁止 socket connect、禁止 Config.load，拒绝覆盖已有 artifact。

## 可执行操作

以下在仓库根目录执行。读取队列和报告不会启用 rollout：

```sh
bin/sagacontext-daemon-control status
bin/sagacontext-daemon-control pending
bin/sagacontext-daemon-control report --rollout-id ROLLOUT_ID
```

真实运行恢复后，由操作者读取 proposal 再选择审核决定：

```sh
bin/sagacontext-daemon-control review --batch-id BATCH_ID --decision approve
# 或使用 --decision reject
```

只有 approve/reject 两种操作，目前没有正文编辑命令。`review_modified` 是操作者的观察标签，不代表系统实现了编辑能力。HTTP 请求超时后先查 pending/report，确认该 batch 是否已经审核；不要把新的 CLI 调用视为原始 receipt 的完全重放。

从 `report.observation_targets` 选择对应 kind 的 ID，创建 observation JSON（只允许以下四个字段，不写正文或凭据）：

```json
{"receipt_id":"quality-observation-001","kind":"consumed","target_id":"INJECTION_AUDIT_RECEIPT_ID","value":"unknown"}
```

```sh
bin/sagacontext-daemon-control annotate --rollout-id ROLLOUT_ID --observation-file /absolute/path/observation.json
bin/sagacontext-daemon-control report --rollout-id ROLLOUT_ID
```

| kind | target_id | value |
|---|---|---|
| candidate_valid | 本 rollout candidate ID | yes / no |
| review_modified | 已审核 batch ID | yes / no |
| wrong_recall | 非空 emitted injection 的 audit receipt ID | yes / no |
| consumed | 非空 emitted injection 的 audit receipt ID | yes / no / unknown |

同一对象同一标签只能有一个值；冲突修正当前会拒绝，不覆盖原审计。观察样本占比不代表总体质量；`scope_expansion_admitted` 仍为 false。

停止与清理入口：

```sh
bin/sagacontext-daemon-control stop
bin/sagacontext-daemon-control rollback --rollout-id ROLLOUT_ID
bin/sagacontext-daemon-control report --rollout-id ROLLOUT_ID
```

`stop` 设置 STOP、冻结 rollout 并停止受控 daemon，不等同于清理；`rollback` 使用本机当前 operator 凭据、固定 rollout plan 清理归属资源。若返回 `cleanup_required`，排除 backend 故障后重新调用 rollback，形成新授权回执继续恢复。`start` 不会清除 STOP。本轮没有执行这些真实状态变更命令。

## 复跑与后续边界

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_daily_scheduler.py --output /absolute/path/new-acceptance.json
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

本轮全量结果：246 passed、96 subtests passed；compileall、diff 检查通过。仅保留既存 Starlette/httpx 与 anyio 弃用警告。

读取本机状态确认 daemon 正常监听、mode=off、stop_switch=true。下一阶段仍需 scheduler-driven 真实宿主消费证据和真实日常标注样本。Codex provider 超时按本轮要求暂缓处理，不能据此将宿主准入标为通过，也不扩大 workspace、session/candidate quota 或进入 active。
