# 2026-09-11 Codex provider 恢复与真实 Scheduler 闭环

此前阻断 scheduler-driven 验收的 Codex 超时，本轮未复现。固定 `codex-cli 0.153.4`、同一 `gpt-6-astra`、既有本机 provider/Judge/backend 配置，真实受控闭环 **11/11** 步骤通过，独立持久化与远端复核 **17/17** 通过。结束时恢复 `off + STOP`，没有留下 live rollout。

本轮未修改实现代码、provider 地址/密钥、模型、120 秒宿主超时上限或重试策略；不将环境恢复描述为已定位并修复了 provider 根因。旧失败记录继续保留。当前证据表明这一次真实链路可用，不能证明长期超时已彻底消除。

## 证据与执行范围

- [最小 provider 预检](../../artifacts/probes/20260911-provider-recovery/preflight.json)：约 14.3 秒返回预期最终消息，退出码 0，无 hooks、无正式数据写入。
- [真实 scheduler 运行](../../artifacts/probes/20260911-provider-recovery/scheduled-01.json)：后台 scheduler 驱动 Judge 和 Projector，runner 不调用显式 batch-run 或 projector-drain。
- [独立评价](../../artifacts/probes/20260911-provider-recovery/evaluation.json)：源 artifact SHA-256、实现 commit、17 项校验和清理后 report。
- rollout：`5b725e16-85a7-4286-bed6-f1e80cc59ff8`。
- workspace：`/Users/lqy0584/Downloads/SagaContext`；10 session、20 candidate 上限，1 小时 deadline，配置 generation 不变。实际 3 个 rollout session、1 个 candidate；回滚后负对照在 off 状态，不属于新的活动 rollout。

按照已有授权短时解除 STOP、认证激活一次 guarded 测试 rollout，使用真实 Codex hooks、真实 Judge 和 OpenViking。审核由用户授权代理进行，校验 proposal 包含待保存的测试命令之后才 commit；不是用户逐条点击，也不是无人审核自动提交。

宿主使用临时最小 CODEX_HOME，复制既有认证仅供子进程使用；不导入历史 transcript。运行结束后删除临时目录、回滚测试资源，再恢复 STOP。未启用 active，未扩大工作区或配额。

## 实际结果

| 环节 | 结果 |
|---|---|
| 无记忆对照 | 独立 Codex 会话返回 `MISSING` |
| 新事件 | 真实 UserPromptSubmit 生成 1 个 candidate |
| Scheduler → Judge | 1 条 `judge_scheduled` receipt，proposal 进入 awaiting_review |
| 审核 → commit | 1 条 approve receipt、1 条 memory，绑定同一 batch/rollout |
| Scheduler → Projector | 真实 projection 确认并可搜索 |
| 下一会话消费 | 最终消息准确返回 `uv run --locked pytest -q` |
| Consumption receipt | 与 emitted bundle、session digest、memory ID、最终结果 digest 逐项一致 |
| Rollback | memory/candidate/operation/outbox 残留均 0；1 条 compensation receipt |
| 远端独立复核 | 精确 inspect 不存在，search 结果 0 |
| 回滚后对照 | 新独立 Codex 会话返回 `MISSING` |
| 最终状态 | rollout stopped，runtime off，STOP true，无 live rollout |

四次真实宿主会话分别约 25.7、16.0、19.0、80.0 秒，均退出 0。最后一次仍有较大延迟，故不能宣称 provider 延迟已经稳定。stderr 均出现非致命鉴权信号，但最终响应成功；不把这类背景诊断单独视为执行失败。

Judge 持久化 receipt 记录 11556 ms。runner 在源会话结束后另等待 proposal 约 3060 ms；两者计时起点不同，不能用后者替代 Judge 总耗时。审核后 projection 观察等待约 2007 ms。

独立验证保留 12 个事件审计行、1 个 batch、1 个 memory target、1 个 projection target、1 条 compensation receipt。保留审计/tombstone 不等于可用数据残留。

## 验证与下一阶段

本轮实现代码未变；全量回归 246 tests、96 subtests 通过，2 个既存弃用警告；compileall、git diff --check 通过。

**scheduler-driven 真实受控宿主闭环的环境阻塞已经解除**。它与先前显式 runner 闭环、离线故障矩阵是三类独立证据。本次样本是事先指定测试命令，真实日常质量样本仍为 0；独立 report 中尚未填写的人工质量标签仍为 null，消费回执数为 1，二者不混用。

下一步是在已批准 workspace 和原配额内开始有限期真实日常观察，逐条审核候选并记录有效性、错误召回与实际消费证据；根据样本质量另作扩围决策。本轮结束不保留活动 rollout，也不自动进入 active。

复跑同一 runner（前提为按已批准边界短时解除 STOP，结束必须恢复并核对清理）：

```sh
PYTHONPATH=src .venv/bin/python scripts/run_guarded_runtime.py --execute --scheduled --model gpt-6-astra --output /absolute/path/new-run.json
bin/sagacontext-daemon-control status
```

旧超时证据：[2026-09-10 acceptance-03](../../artifacts/probes/20260910-daily-scheduler/acceptance-03.json)。不覆盖、不删除，不把本轮结果追溯改写为旧运行成功。
