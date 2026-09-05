# 验收报告索引

当前实现的整体入口见 [文档导航](../README.md)。下表将阶段验收、准入证据和历史失败分开；历史数字不代表当前测试总数。

## 当前验收

| 范围 | 报告 | 对应结果 |
|---|---|---|
| 真实 Judge 固定回放 | [适配器与回放报告](2026-09-06-real-judge-replay.md) | 6 个单候选 case 已冻结；本次无 LLM 配置，6/6 `blocked_configuration`，未伪造语义指标 |
| S3-1 真实后端 | [OpenViking 适配器与故障恢复](2026-09-05-s3-1-openviking-recovery.md) | 首次 22/22，后续完整运行复验 P1–P6 |
| S3-2 至 S3-5 | [RecallPolicy、Shadow、G5/G6](2026-09-06-s3-policy-shadow-g5-g6.md) | 完整纵向 69/69；最终后端/策略回归 40/40；本地测试 111 项 |
| G1 后端准入 | [OpenViking](2026-09-05-s3-g1-openviking.md) | 17/17，固定镜像与真实后端 |
| G3 宿主准入 | [Codex CLI terra](2026-09-05-s3-g3-codex-host-terra.md) | 19/19，固定 CLI 版本与合成事件 |

原始 S3 JSON、失败运行和提交前本地测试日志见 [测试结果清单](../../artifacts/probes/S3-RESULTS.md)。真实纵向验收仅覆盖隔离合成会话，没有启用正常会话自动化。

## 前序阶段

- [S2 持续维护](2026-09-05-s2-acceptance.md)：86 项阶段测试快照。
- [S1 数据收口](2026-09-05-s1-acceptance.md)：53 项阶段测试快照。
- [S0 本地前置探针](2026-09-05-s0-local.md)：早期环境观察，后端/宿主准入以 G1/G3 为准。

## 历史探针

- [G3 初次记录](2026-09-05-s3-g3-codex-host.md)：blocked_environment，保留认证阻断证据。
- [G3 sol 记录](2026-09-05-s3-g3-codex-host-sol.md)：failed_contract，5/19；不覆盖 terra 通过结论。

旧报告与原始 artifact 保持原位。新增运行使用独立 ID，不能覆盖失败记录或将不同运行的断言拼成一次通过结果。
