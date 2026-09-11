# S3-2 至 S3-5 合成纵向验收

**日期：** 2026-09-06（Asia/Shanghai；artifact ID 使用 UTC）
**结论：** S3-2 RecallPolicy、S3-3 Shadow、S3-4/G5 与 S3-5/G6 在隔离合成场景中通过。完整运行 69/69 必需断言通过。

完整证据：[s3-1-20260905T160955Z-941808e7/s3-1.json](../../artifacts/probes/s3-1-20260905T160955Z-941808e7/s3-1.json)。此前 63/63 的运行保留在 `s3-1-20260905T160452Z-1685e03f`；最终版补充每条 G6 链路的独立删除和 Ledger 重开验收。

## S3-2：RecallPolicy

`src/sagacontext/recall_policy.py` 从搜索候选中读取 identity，再在单个 SQLite 读快照中回 Ledger 获取正文。owner、scope、revision、generation、inactive、conflict、missing、duplicate、budget 均有明确省略原因；后端正文不进入 ContextBundle。Application 已组合该策略，但没有启用正常会话自动注入。

预算覆盖最终序列化正文及 provenance；默认按 UTF-8 字节作保守估算，记录 `utf8_bytes_conservative_estimate`，不冒充模型 tokenizer 的精确 token 数。可显式传入 tokenizer。超预算的大条目被省略后，仍检查后续较小条目。Bundle 是当前 Ledger 快照的决定，不能作为长期缓存的注入许可。

## S3-3：Codex Shadow

`CodexShadowAdapter` 只接收固定 `codex-cli 0.153.4` 已验证的六种事件，映射到 EventJournal，并保留 probe/scenario/source event 关联。未知事件拒绝；同一事件重放得到 duplicate receipt。

本轮先重新运行独立 evaluator 核验 terra G3 capture，再回放实际采集的脱敏事件，将声明的合成 fixture 沿 candidate → 固定 batch → proposal → Ledger → outbox → 真实 OpenViking → 拟注入 bundle 走通。G3 脱敏事件没有正文，因此 fixture 不被描述为从原始会话自动抽取的语义。

证据入口：artifact 的 `shadow`、`maintenance_records` 和 `traces`。已核对 source event、candidate、proposal、revision evidence、projection receipt 与拟注入正文的关联。Shadow 自身不修改 Agent context；G6 另有实际源会话和消费会话证据。

## S3-4：G5

本轮修复了删除没有目标 locator、删除正文清空后未知旧写无法核验、旧 delete receipt 阻止再次清理，以及被取代内容重扫未抑制的问题。

| 场景 | 断言 |
|---|---|
| 删除/取代后的延迟候选 | 将真实后端 projection identity 作为已缓存的旧搜索结果回放，RecallPolicy 返回 inactive，正文为空 |
| 清理后在途写迟到 | 先清理，再向真实后端发送旧 projection；完成旧 claim 为 obsolete，重新排队并完成删除 |
| 重扫 | 用新 receipt 重交旧内容，返回 rejected / suppressed_after_deletion |
| 多 revision / generation | 本地回归覆盖旧 revision 与 inactive generation 的删除；真实 P3 覆盖 r2 后到达的 r1 |
| 恢复 | 本地回归覆盖删除后未知写恢复与 retired 记忆重开；G6 三条记忆另有真实删除后 Ledger 重开验证 |

索引延迟使用“缓存真实 identity 后继续返回”的受控回放；没有声称暂停或修改 OpenViking 的索引进程。远端删除后还检查精确读取与 namespace 搜索为空。抑制规则覆盖全部历史 revision 的 topic 和来源 claim，正文仍按原删除语义清空。

## S3-5：G6

宿主固定为 `codex-cli 0.153.4`、模型 `gpt-5.6-terra`。源会话实际提交合成偏好、项目事实与 checkpoint，返回 ACK；UserPromptSubmit 被采集并接入维护链路。每条记忆经真实 OpenViking 搜索和 Ledger 复核后，通过新 CLI 会话的 SessionStart.additionalContext 提供给 Agent。

| 链路 | 后续任务输入 | 实际结果 |
|---|---|---|
| 偏好 | 只询问 8 件加 7 件的结果，不重复 JSON 格式偏好 | `{"answer":15,"unit":"items"}` |
| 项目事实 | 询问 NOVA 六个机架的可用容量，不重复“7 槽位、预留 2 槽位” | `{"capacity":30,"unit":"slots"}` |
| checkpoint | 要求从已保存 checkpoint 继续，不重复小计和待处理数值 | `{"total":36,"status":"complete"}`，且真实写出同内容的 `checkpoint_result.json` |

每条 `g6.chains[]` 保存 source event、candidate、batch、proposal、memory/revision、projection receipt、recall decision、**实际 hook 输出全文**、下一会话 prompt、最终回答和 cleanup/recovery。checkpoint 还保存实际文件的解析内容。任务结果断言检查完整 JSON 值，不是只查关键词。每条消费后删除远端 projection，再重开 Ledger 验证旧命中不能重新生效。

## 复验与边界

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
PYTHONPATH=src .venv/bin/python scripts/verify_openviking_projector.py --policy-stages
PYTHONPATH=src .venv/bin/python scripts/verify_openviking_projector.py --longitudinal
```

最终本地回归覆盖 111 项测试。真实运行只使用临时用户、namespace、Git workspace、最小 Codex 配置与合成内容；没有修改全局 hooks 或读取私人 transcript。namespace 精确读取/搜索清理和临时用户撤销均通过，临时工作区随 runner 退出移除。

补齐并发删除返回 404 的幂等处理后，又运行了 [40/40 真实后端与策略回归](../../artifacts/probes/s3-1-20260905T161222Z-9482aa6f/s3-1.json)。首次扩展调试 `s3-1-20260905T160014Z-2fb03cc5` 因 runner 把 source event ID 当 evidence ID 查询而失败，修复为经 evidence 表关联后通过；失败记录及其成功清理结果均保留，没有覆盖历史证据。

这些结果证明隔离合成场景下的数据链路和实际消费，不是线上效果指标，也不是任意会话的自动抽取能力或生产服务验收。Candidate/proposal 使用可复验的 ScriptedJudge，将用户明确给出的合成事实提交到 Ledger；尚未引入通用语义抽取模型、常驻 worker、第二后端或正常会话自动化。
