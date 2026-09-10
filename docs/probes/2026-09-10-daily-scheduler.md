# 2026-09-10 日常 Scheduler 接线与故障矩阵

已完成持久化日常调度实现，但真实后台调度验收仍被 Codex 模型运行环境阻断。

## 已实现

- scheduler 使用 Ledger 中的 candidate、batch、lease、proposal 和 outbox；不使用内存队列作为事实来源。
- daemon lifespan 可按本机配置启用独立 scheduler 线程；每个 scheduler 使用独立 Application/SQLite 连接。
- session checkpoint/SessionEnd 后才冻结 pending candidates，避免每个 prompt 触发 Judge。
- 重启恢复 pending/retry/过期 lease；`stop_after_proposals` 在恢复路径同样停在 `proposed`，不能绕过人工审核。
- Judge 外部调用前后检查 rollout/control epoch；STOP/deadline 后阻止 proposal 持久化。
- Projector 复用持久化 claim、operation、epoch 和 compensation；外部调用前后检查状态，晚到 locator 进入补偿协议。
- 新增 `pending`、`review`、`report`、`annotate` operator 入口。
- quality report 只接受固定枚举观察标签；未标注的错误召回、审核修改率和消费率显示 `null`，不会把零样本误报为零错误。
- `start/stop/status/rollback` 已改为受控独立进程入口；STOP 不会被 start 清除。

## 故障矩阵

`tests/test_daily_scheduler.py` 覆盖：

- 单进程 checkpoint proposal；
- 重启后 lease 恢复仍等待 review；
- 两 worker 并发只创建一个 batch；
- Judge 调用期间 STOP；
- retry/backoff 与到期恢复；
- projection materialize 期间 STOP 的补偿；
- session quota draining 与第 11 个 session 拒绝；
- candidate quota；
- deadline fencing；
- compensation failure → `cleanup_required`。

当前全量验证：`237 passed, 96 subtests passed`。

## 真实运行边界

受控 runner 的真实闭环报告仍为 [2026-09-10 Guarded 真实运行闭环](2026-09-10-guarded-runtime-closure.md)，它验证了直接显式调度路径。
本轮 scheduler-driven runner 在无记忆 Codex 对照阶段超时；rollout 自动 rollback，memory、projection、candidate、operation、outbox 均为零残留。artifact：`artifacts/probes/20260910-daily-scheduler/acceptance-02.json`。
因此当前不能声称后台 scheduler 已完成真实宿主准入，也不能扩大 workspace 或 quota。

最终仍保持：runtime `off`、STOP 置位、`active` 不可达、历史 transcript 不导入。下一次运行应在模型可稳定完成 Codex 无记忆对照后，重新执行 scheduler-driven runner，并逐条通过 `pending`/`review`/`annotate` 记录日常观察。
