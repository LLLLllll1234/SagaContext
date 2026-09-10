# 验收报告索引

当前实现的整体入口见 [文档导航](../README.md)。下表将阶段验收、准入证据和历史失败分开；历史数字不代表当前测试总数。

## 当前验收

最新离线验收：[2026-09-11 Scheduler 与运维验收](2026-09-11-offline-scheduler-acceptance.md)：合成隔离运行 34/34、全量 246 tests / 96 subtests；Codex provider 超时暂缓，真实 scheduler-driven 宿主准入未完成。
受控真实闭环：[2026-09-10 Guarded 真实运行闭环](2026-09-10-guarded-runtime-closure.md)。
日常调度接线与故障矩阵：[2026-09-10 Daily Scheduler](2026-09-10-daily-scheduler.md)。
已通过真实宿主、Judge、OpenViking 的受控写入/跨会话消费/回滚验收，零残留。
当前 runtime 为 off，STOP 已置位；日常质量样本数为 0，不据此扩大范围。
历史阶段报告保留以下各自的实验边界。

| 范围 | 报告 | 对应结果 |
|---|---|---|
| S3-5/G6 真实隔离纵向生命周期 | [G6 lifecycle 真实验收](2026-09-08-g6-lifecycle-acceptance.md) | 新增/取代/删除三条真实跨会话链路 `103/103` 通过；清理通过；正常自动化仍关闭 |
| v6 Judge 基线、S3-3 Host Shadow 与 S3-4/G5 | [v6 基线与 S3-3/S3-4 隔离验收](2026-09-07-v6-judge-baseline.md) | v6 Pro/Flash 正式 artifact 各 `42/42`；跨模型 `42/42` 语义等价；新鲜 S3-3/S3-4 运行 `40/40`；S3-5/G6 未运行 |
| Judge Pro/Flash 双模型与隔离 Shadow | [双模型验收与 shadow](2026-09-06-dual-model-acceptance.md) | 正式 artifact 均 `42/42 passed`；授权 shadow 中 Flash `42/42 passed`、Pro `41/42` 且含 conversion/body 阻断；无正式写入/召回/注入 |
| Judge schema-v3 / dataset-v3 | [schema 与双人 refine 审计](2026-09-06-real-judge-v3-audit.md) | `WireDeltaV3` 定向测试 28/28；v2 digest 保持不变；v3 digest 固定并待 DeepSeek 单请求诊断 |
| Judge timeout 对比 | [sol 60/90/120 秒独立运行](2026-09-06-real-judge-timeout-comparison.md) | 120 秒 run 为 42/42 且全部语义指标满分，正式 Judge 验收 `passed`；运行时自动化仍关闭 |
| Judge DeepSeek 独立运行 | [Pro/Flash provider、schema 与正文记录](2026-09-06-real-judge-deepseek-runs.md) | pro 11/42、flash 40/42；两个模型均 `blocked`，不与 sol 合并 |
| Judge prompt v3 独立复验 | [三轮复验与 read timeout 记录](2026-09-06-real-judge-prompt-v3-rerun.md) | sol 调用 40/42；成功响应 body 23/23，两个 read timeout，正式验收仍为 `blocked` |
| Judge 正文修复 | [失败诊断与本地修复](2026-09-06-real-judge-body-repair.md) | prompt v3、refine 保护与超时阶段；137 项回归通过，后续云端结果见上方独立复验 |
| Judge 新失败诊断 | [三组云端 run 根因与诊断修复](2026-09-06-real-judge-failure-diagnosis.md) | sol 40/42 read timeout；DeepSeek Pro provider/schema 失败；Flash schema 与严格正文差异 |
| 真实 Judge v2 语义验收 | [v2 数据、评分与阻断记录](2026-09-06-real-judge-semantic-acceptance.md) | sol 调用 41/42，body 15/23；一次超时且正文未达标，`blocked` |
| S3-1 真实后端 | [OpenViking 适配器与故障恢复](2026-09-05-s3-1-openviking-recovery.md) | 首次 22/22，后续完整运行复验 P1–P6 |
| S3-2 至 S3-5（历史合成纵向） | [RecallPolicy、Shadow、G5/G6](2026-09-06-s3-policy-shadow-g5-g6.md) | 历史完整纵向 69/69；当前 S3-3/S3-4 fresh evidence 见上方 v6 报告；本地测试数字以最新运行日志为准 |
| G1 后端准入 | [OpenViking](2026-09-05-s3-g1-openviking.md) | 17/17，固定镜像与真实后端 |
| G3 宿主准入 | [Codex CLI terra](2026-09-05-s3-g3-codex-host-terra.md) | 19/19，固定 CLI 版本与合成事件 |

原始 S3 JSON、失败运行和提交前本地测试日志见 [测试结果清单](../../artifacts/probes/S3-RESULTS.md)。真实纵向验收仅覆盖隔离合成会话，没有启用正常会话自动化。

## 前序阶段

- [S2 持续维护](2026-09-05-s2-acceptance.md)：86 项阶段测试快照。
- [S1 数据收口](2026-09-05-s1-acceptance.md)：53 项阶段测试快照。
- [S0 本地前置探针](2026-09-05-s0-local.md)：早期环境观察，后端/宿主准入以 G1/G3 为准。

## 历史探针

- [真实 Judge v1 六例回放](2026-09-06-real-judge-replay.md)：仅链路冒烟，存在一个无依据标注和一个答案泄露样本；v2 已修正且保留旧 digest。
- [G3 初次记录](2026-09-05-s3-g3-codex-host.md)：blocked_environment，保留认证阻断证据。
- [G3 sol 记录](2026-09-05-s3-g3-codex-host-sol.md)：failed_contract，5/19；不覆盖 terra 通过结论。

旧报告与原始 artifact 保持原位。新增运行使用独立 ID，不能覆盖失败记录或将不同运行的断言拼成一次通过结果。
