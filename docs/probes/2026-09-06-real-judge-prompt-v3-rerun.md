# 真实 Judge prompt v3 独立复验

**日期：** 2026-09-06（Asia/Shanghai）
**数据集：** `real-judge-v2` / `bench/cases/real_judge/cases-v2.yaml`
**Run ID：** `20260906T071912Z-2d9d7b94`
**状态：** `blocked`

## 结论

`openai-judge-prompt-v3` 与 `delta-to-proposal-v2` 已使用同一 `gpt-5.6-sol`、同一数据集、`temperature=0`、60 秒 timeout 完成一次独立三轮复验。运行完整保留 14 case x 3 repeat 共 42 个 observation，没有与旧 artifact 合并，也没有补跑失败样本。

成功响应中的正文评分为 `23/23`：此前失败的 refine 完整正文、`json` 枚举和 `prose` 枚举在三轮中均为 `3/3`。这为提示词修复提供了正向证据，但不能宣称正式正文质量已经通过：第一轮一个写入型 observation 超时，没有模型正文可评分。

整体调用仅成功 `40/42`，两次失败均为客户端 `read` 阶段的 `judge_timeout`，因此冻结规则下 Acceptance 仍为 `blocked`。`read` 只能说明客户端在等待读取响应时达到超时，不能进一步断言是中转排队、上游模型计算或其他服务端原因。

## 冻结配置

- 模型：`gpt-5.6-sol`
- Prompt contract：`openai-judge-prompt-v3`
- Response schema：`delta-v2`
- Converter：`delta-to-proposal-v2`
- 数据集 digest：`254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce`
- 重复轮次：3
- 单 observation 最大请求次数：1
- Timeout：60 秒
- Token usage / cost：provider 未返回，仍为 unavailable

完整 endpoint 未写入产物，仅保留规范化 endpoint 的 SHA-256 指纹；API key 未写入配置、仓库或 artifact。

## 结果

| 指标 | Repeat 1 | Repeat 2 | Repeat 3 | 总计 |
|---|---:|---:|---:|---:|
| Judge 调用成功 | 12/14 | 14/14 | 14/14 | 40/42 |
| Relation | 12/12 | 14/14 | 14/14 | 40/40 |
| Memory body | 7/7 | 8/8 | 8/8 | 23/23 |
| Evidence | 7/7 | 8/8 | 8/8 | 23/23 |
| Conversion fidelity | 12/12 | 14/14 | 14/14 | 40/40 |
| Ignore | 5/5 | 6/6 | 6/6 | 17/17 |

两次失败均发生在 Repeat 1：

| Case | Error | Timeout phase | Latency |
|---|---|---|---:|
| `v2-smoke-new-decision` | `judge_timeout` | `read` | 60804 ms |
| `v2-insufficient-unverified-gotcha` | `judge_timeout` | `read` | 60194 ms |

全体 observation 延迟 P50 为 5008 ms、P95 为 53239 ms、最大 60804 ms。只看成功调用时，P50 为 4876 ms、P95 为 45909 ms、最大 53400 ms。延迟包含第三方中转和模型响应的端到端等待，不能拆分归因。

## Artifact 与审计

- 报告：`artifacts/real-judge/20260906T071912Z-2d9d7b94/report.md`
- 原始结果：`artifacts/real-judge/20260906T071912Z-2d9d7b94/replay.jsonl`
- JSONL 行数为 42；唯一 run ID、模型、数据集 digest、prompt 和 converter 版本均一致。
- 三个 repeat 各有 14 条，14 个 case 均完整出现。
- 对新 artifact 扫描明文 URL、Authorization、Bearer 和 key 形态，无命中。

## 后续边界

本次不通过提高 timeout、更换模型或启用 provider JSON Schema 来覆盖失败。若继续测试中转可靠性，应使用新的 run ID 和显式配置标签；不同 timeout、模型或响应约束的结果分别报告。

在取得 42/42 成功且冻结语义门槛全部满足的独立 run 前，不启用正常会话自动写入、常驻 worker 或自动注入，也不进入自然事件到候选和 anchor 召回的真实入口实现。
