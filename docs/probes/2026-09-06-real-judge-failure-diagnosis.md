# 真实 Judge 新失败诊断

**日期：** 2026-09-06
**范围：** `openai-judge-prompt-v3`、`delta-to-proposal-v2`、`real-judge-v2`

## 结论

最新三组 run 的失败不是同一个根因：

| Run | 主要现象 | 判断 |
|---|---|---|
| `20260906T071912Z-2d9d7b94` / `gpt-5.6-sol` | 40/42 成功；两次 `read` 阶段 60 秒超时；成功正文 23/23 | 语义修复有效，阻断来自第三方中转端到端可靠性 |
| `20260906T073337Z-d7fefa0d` / `deepseek-v4-pro` | 11/42 成功；4 次 schema，27 次 service unavailable | provider/中转在本次运行中不可用；成功样本太少，不能评价语义质量 |
| `20260906T073920Z-fdb633f3` / `deepseek-v4-flash` | 40/42 成功；2 次 confirm schema；refine 正文 1/3 | schema 契约仍不稳定；refine 字段完整，但短语不命中冻结正文 |

完整 run 仍必须独立判定，不能跨模型或跨运行拼接成一次通过。

## 根因边界

### gpt-5.6-sol

此前失败的 refine 合并和 `json/prose` 枚举在新提示词下已经全部通过。两次失败都是客户端等待响应超过 60 秒，发生在 `read` 阶段；这只能说明端到端响应未及时返回，不能进一步断言是中转排队、模型计算还是服务端故障。提高 timeout 或重试可以作为新的可靠性实验，不能覆盖当前验收失败。

### DeepSeek Pro

第二轮从约 30 秒的失败开始，随后大量在 5–26 ms 返回 `judge_service_unavailable`；第三轮全部快速失败。旧 artifact 没有 HTTP status code，所以不能区分 408、5xx、限流、余额/模型不可用或客户端 RequestError。4 次 schema 也没有保留 validation 路径，无法从历史产物判断缺少了哪个字段。

### DeepSeek Flash

两次 refine 都保留了 anchor 的 `symptom`、`fix` 和新增条件，说明之前的“正文被当作 patch”问题已解决；失败是 `the async task is already closing` / `when the async task is already closing` 与冻结的 `task is already closing`、`async task is already closing` 不相等。它是严格正文契约下的表达差异，不能靠事后放宽 normalizer 或追加结果标注来改判。

## 本次修复

- `JudgeError` 和 replay artifact 现在保存非敏感 HTTP status、固定错误 detail、超时阶段和 response digest。
- schema 错误只保存 validation location/type，不保存输入值或模型响应正文；HTTP 响应只保存 SHA-256 digest。
- Markdown failure table 展示 status、detail 和 timeout phase，下一次运行可以判断 provider 层失败类型。
- 保留 `prompt-v3` 和 `delta-to-proposal-v2` 的正文安全约束，不自动修饰不完整 refine，也不把错误响应降级为 `no_change`。

本地定向测试为 `25/25`；当前全量回归为 `138/138`。现有历史 artifact 不可变。

## 下一次实验

1. 用原 `gpt-5.6-sol` 配置先做一个新的单 case 诊断，确认失败 artifact 是否出现稳定的 `read` 超时；然后用 `--timeout 90` 或 `120` 做独立 run，比较成功率和 P95。不同 timeout 不合并。
2. 对 DeepSeek 先做单请求健康、JSON 和 schema 诊断，记录非敏感 HTTP status 和 validation path；不要直接再跑 42 次三轮，避免把 provider 故障混入语义结论。
3. Flash 的 refine 需要二选一：继续保持冻结表达并在 prompt 中要求按规范短语输出；或者新建 dataset v3，在人工审计确认这些短语均由输入直接支持后，冻结新的 `acceptable_bodies` 和 digest。不能修改 v2 digest。
4. 在同一模型、同一 dataset、42/42 成功且每 case 稳定性及安全门槛都通过前，继续保持正常会话自动写入和自动注入关闭。
