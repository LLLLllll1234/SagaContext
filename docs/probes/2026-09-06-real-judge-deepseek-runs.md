# 真实 Judge DeepSeek 独立运行记录

**日期：** 2026-09-06（Asia/Shanghai）
**数据集：** `real-judge-v2` / `bench/cases/real_judge/cases-v2.yaml`
**状态：** 两个模型均为 `blocked`

## 结论

使用同一 DeepSeek provider 分别对 `deepseek-v4-pro` 和 `deepseek-v4-flash` 完成了两个独立三轮 run。每个 run 均保留 14 case x 3 repeat 共 42 个 observation，使用 `openai-judge-prompt-v3`、`delta-v2`、`delta-to-proposal-v2`、`temperature=0`、60 秒 timeout 和单 observation 一次请求。两个模型的结果没有互相补齐，也没有与 `gpt-5.6-sol` 合并。

`deepseek-v4-pro` 只有 `11/42` 调用成功，无法形成有效语义质量结论。失败包含 4 个 `judge_schema_error` 和 27 个 `judge_service_unavailable`。后者在当前错误产物中没有保存 HTTP status code，不能区分 408、5xx 或客户端 RequestError；正式 run 后的一次最小诊断返回 HTTP 200，只能证明诊断时服务已恢复，不能反推 run 内失败原因。

`deepseek-v4-flash` 有 `40/42` 调用成功，但两次 `judge_schema_error` 已阻断验收；成功响应的 memory body 为 `20/22`，也未达到门槛。其余 relation、evidence、conversion fidelity 和 ignore 在适用的成功响应上均正确。

## 结果对比

| 模型 | Run ID | 调用成功 | Relation | Body | Evidence | Conversion | Ignore | Acceptance |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `deepseek-v4-pro` | `20260906T073337Z-d7fefa0d` | 11/42 | 11/11 | 5/5 | 5/5 | 11/11 | 6/6 | `blocked` |
| `deepseek-v4-flash` | `20260906T073920Z-fdb633f3` | 40/42 | 40/40 | 20/22 | 22/22 | 40/40 | 18/18 | `blocked` |

Pro 的成功样本过少，`5/5` body 不能与其他模型的完整三轮正文结果作质量比较。

## Pro 失败分布

- Repeat 1：10/14 成功，4 个 `judge_schema_error`。
- Repeat 2：1/14 成功，随后 13 个 `judge_service_unavailable`。
- Repeat 3：0/14 成功，14 个 `judge_service_unavailable`。
- 多数后期 service unavailable 在 5-26 ms 内返回；另一个失败耗时约 30.7 秒。现有分类不足以将这些响应归因到余额、限流、中转或上游服务。

Schema 失败发生在 `v2-smoke-new-decision`、`v2-smoke-confirm-convention`、`v2-duplicate-exact-convention` 和 `v2-reversal-explicit`。当前 trace 不保留 schema 失败的原始模型正文或 validation detail，不能从 artifact 进一步判断具体缺失或非法字段。

## Flash 失败分布

- Repeat 1：13/14 成功；`v2-smoke-confirm-convention` 为 `judge_schema_error`；body 6/7。
- Repeat 2：14/14 成功；body 8/8。
- Repeat 3：13/14 成功；`v2-smoke-confirm-convention` 再次为 `judge_schema_error`；body 6/7。

两个 body 失败均为 `v2-smoke-refine-gotcha`。模型完整保留了 `symptom=task hangs`、`fix=cancel task` 和新增的 `applies_when`，没有重现旧版丢字段风险；但分别输出了 `the async task is already closing` 和 `when the async task is already closing`，不等于冻结的 expected/acceptable bodies。不能在看到结果后放宽 normalizer 或标注将其改判通过。

## Artifact 与安全

- Pro 报告：`artifacts/real-judge/20260906T073337Z-d7fefa0d/report.md`
- Pro 原始结果：`artifacts/real-judge/20260906T073337Z-d7fefa0d/replay.jsonl`
- Flash 报告：`artifacts/real-judge/20260906T073920Z-fdb633f3/report.md`
- Flash 原始结果：`artifacts/real-judge/20260906T073920Z-fdb633f3/replay.jsonl`
- 两个 JSONL 均为 42 行；各自的 run ID、模型、endpoint 指纹、数据集 digest、prompt 和 converter 版本唯一且一致。
- 完整 endpoint 和 API key 未写入配置、仓库或 artifact；两个新 artifact 的明文 URL、认证头和 key 形态扫描均无命中。

## 边界与后续

这两次运行不改变 `gpt-5.6-sol` 独立 run 的结论，也不授权正常会话自动写入或注入。任何响应 schema、prompt、timeout 或重试策略变更都必须使用新的版本和 run ID。

## 后续单请求诊断

在增加非敏感 HTTP status、validation path 和 response digest 记录后，没有立即重跑 42 次，而是对两个模型各执行一次 `v2-smoke-confirm-convention`：

| 模型 | Run ID | 延迟 | JSON | Delta schema | Validation path |
|---|---|---:|---|---|---|
| `deepseek-v4-pro` | `20260906T081229Z-f220e25a` | 9175 ms | 可解析 | 失败 | `0.confidence_hint` |
| `deepseek-v4-flash` | `20260906T081328Z-1754693c` | 6516 ms | 可解析 | 失败 | `0.confidence_hint` |

两个请求都已通过认证、HTTP 响应封装和 JSON 解析并进入 Pydantic Delta 校验，response digest 已保留，原始正文未保存。当前 `http_status_code` 字段只在 HTTP 异常上记录状态；成功响应后的 schema failure 因此显示为空，而不是显式的 200。健康状态可从进入 schema 校验推断，但不能声称是从该字段直接读取。

两个模型在同一字段重复失败，说明 DeepSeek 的下一步应先约束或适配 `confidence_hint`，再使用新版本做独立诊断；本轮不据此启动新的三轮 run。

Flash refine 的两个失败属于完整正文中的自由文本表面差异，不是丢字段。后续有两个互斥方向，尚未实施：继续要求规范短语；或创建 dataset v3，在人工审计后冻结新的 acceptable bodies 并保留 v2 digest 不变。推荐后一种，以免把自由文本 Judge 优化成对单一措辞的拟合。
