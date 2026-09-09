# Rollout 控制面修复与验证

日期：2026-09-09。状态：本地隔离验证，正常 workspace 保持 off，未启用 hooks、自动写入、自动注入或历史 transcript 导入。

## 本轮问题与改动

先前 173/174/175 项测试通过不能证明完整安全边界：部分测试在 activation、review、SessionStart 和 API 读写断言之前提前 return；先前远端提交还缺少本地 Config、schema、Ledger 和 daemon 配套修改。本轮恢复测试的实际流程，并将这些运行依赖纳入同一可复现提交。

- 授权、action receipt 查询、grant 校验/消费、业务状态变化和结果 receipt 均在同一 SQLite 写事务中执行。精确重试在业务门禁之前返回原结果；摘要、action 或目标变化返回冲突。
- previous key 仅可消费 activation/review 的登记 grant；rollback 继续仅接受 current key。grant 登记的 receipt 与目标 receipt 分离，限制五分钟有效期并保存幂等 action receipt。
- schema v3 控制面之外增加独立 migration v4，持久化控制密钥身份摘要及服务端登记/退役时间。轮换由受信配置加载触发，与 grant 登记使用同一数据库写锁。旧进程无法用旧配置重新登记；仅填写 previous-key 配置不能制造服务端轮换证据。既有配置中的 previous_rotated_at 不作为授权依据。
- 提交前复查授权/grant 期限；失败提交回滚 grant 消费、memory 和 review 状态。STOP 使正常处理停止并递增 control epoch。
- 提供 `/rollout/grants` HTTP 登记入口。activation 受 off、mode 上限、配额、deadline 和 generation 校验约束。
- 修复 rollback 忽略 phase 而直接 forget 的行为：目前只实现经过 current key、目标和 plan digest 校验的 freeze 阶段，其结果为 frozen。其他阶段返回 rollback_phase_not_implemented，不再报告 completed/已清理。
- 增加 setuptools 包配置、pytest 开发依赖和测试导入路径，支持直接使用 `uv sync --locked` / `uv run --locked pytest -q`，包含子进程导入测试。

## 验证范围

`tests/test_rollout_auth.py` 从真实公开 runtime 方法和 HTTP 入口验证登记、轮换、activation、review、冻结；并发用两个独立 Ledger 连接和 barrier，不依赖私有辅助函数或伪造成功 receipt。

覆盖精确重试、重启/停止后的重试、未登记 grant、错 token、计划/配额/摘要篡改、过期、轮换时刻边界、旧进程登记、登记幂等与冲突、review 事务失败回滚、提交期间期限/STOP、10 个 session draining、11th 拒绝、并发 activation/review 只产生一次业务写入，以及 rollback current-key/目标/plan/phase 拒绝。

迁移测试覆盖 v1 到 v4 保留原数据、v3 到 v4 保留 grant、重复打开、迁移失败不残留表或版本标记。恢复的 daemon 测试同时验证 direct commit/forget 返回 403、读取及 owner 隔离仍有效。

本地全量结果：194 passed，91 subtests passed；2 个既有 Starlette/httpx/AnyIO 弃用警告。compileall 与 git diff --check 通过。推送前还需在只含提交文件的干净检出中重复运行相同检查。

## 未完成的准入项

本报告不宣称方案 B 全部落地。完整冻结计划 schema/阶段执行、正式及隔离数据的定向回收、Projector 精确 operation/locator 归属与补偿收据、draining 自动结束、event/candidate 一体化事务、返回前 bundle digest 完整复验及消费证明仍需独立实现和验收。真实 Codex schema/消费 probe 仍为独立准入证据。

在上述工作完成并获得明确运行授权前，正常 workspace 保持 off。测试只使用临时 Ledger、合成 key 和本地脚本 Judge，不使用真实 LLM 凭据。

## 后续修复（2026-09-09）

补充了 rollback runner 的 locator fail-closed 行为：存在目标 locator 时必须提供具备 `remove_projection` 的 backend 且返回可确认结果；backend 缺失或清理结果不确定时写入 `cleanup_required`，不能把 rollback 步骤标记为完成。Application 将配置的 rollout backend 显式传递给 runner。schema v4 启动校验同时检查 rollback_runs、rollback_steps 和控制密钥列。

恢复测试中的提前返回并加入真实流程覆盖后，验证结果为 `195 passed`、`91 subtests passed`，2 个既有 Starlette/httpx/AnyIO 弃用警告；compileall 与 git diff --check 通过。当前仍无真实 rollout，配置保持 `off`。

## Probe 重跑（2026-09-09）

Codex probe 使用固定 `codex-cli 0.153.4` 与 `gpt-5.6-terra` 重跑：六类要求事件均已观察到，19 项中 17 项通过；模型请求超时，因此总体仍为 `blocked_environment`，没有注入、正式写入或历史 transcript 读取。证据：[probe](../../artifacts/probes/20260909-codex-rerun/probe.json)、[evaluation](../../artifacts/probes/20260909-codex-rerun/evaluation.json)。

Judge isolated shadow 在当前进程没有 LLM endpoint/key 的情况下重跑，Pro/Flash 均保持临时 Ledger `0 -> 0` 并清理成功，但 admission 为 `unsuccessful_observation`；不能作为模型语义准入证据。证据：[shadow](../../artifacts/real-judge/20260909-shadow-rerun/shadow-manifest.json)。

## 最终复核（2026-09-09）

回滚 runner、schema v4、Projector 补偿和授权路径复核后，`uv run --locked pytest -q` 结果为 `196 passed, 91 subtests passed`；仅有既有依赖弃用警告。Codex 90 秒重跑的 evaluator 为 `18/19`, `inconclusive`，唯一未观察到的是 `marker_context_injection`；Judge shadow 仍为 `unsuccessful_observation`。因此真实 Codex/Judge 准入证据尚未闭合，正常 workspace 继续保持 `off`。
