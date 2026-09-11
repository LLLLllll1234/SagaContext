# v6 Judge 基线与 S3-3/S3-4 隔离验收

**日期：** 2026-09-07（Asia/Shanghai；artifact ID 使用 UTC）
**状态：** v6 基线冻结；S3-3 Host Shadow 与 S3-4/G5 隔离验收通过

## v6 Judge 基线

本报告将以下版本作为当前 Judge contract。除非后续真实运行出现新的、可复现的失败，
不再调整 prompt 或放宽语义规则：

- Prompt contract：`openai-judge-prompt-v6`
- Response schema：`delta-v3`
- Converter：`delta-to-proposal-v2`
- Dataset：`real-judge-v4`
- Dataset digest：`c07572973024b3b5227ee09b4fa2540c8eae6e313cc9da7cb65d714c31a05138`
- Body normalizer：`body-normalizer-v2`
- Request timeout：`300s`
- Maximum attempts per observation：`3`

正式验收保留为两个独立证据流，不能互相补齐或合并：

- Pro：[20260906T165252Z-v6-authorized/deepseek-v4-pro](../../artifacts/real-judge/20260906T165252Z-v6-authorized/deepseek-v4-pro/)，`42/42` successful calls，relation/body/evidence/conversion 均通过。
- Flash：[20260906T165252Z-v6-authorized/deepseek-v4-flash](../../artifacts/real-judge/20260906T165252Z-v6-authorized/deepseek-v4-flash/)，`42/42` successful calls，relation/body/evidence/conversion 均通过。
- Cross-model audit：[cross-model-audit.md](../../artifacts/real-judge/20260906T165252Z-v6-authorized/cross-model-audit.md)：42 个对齐 observation 全部 raw digest 相同，42 个语义等价，0 个 unresolved。

此前 v4/v5 命名的报告和失败 artifact 仍保留为历史证据，不改变本 v6 基线。

## S3-3 Host Shadow

本轮使用已通过的 host capture 和固定脱敏输入，通过
`PYTHONPATH=src .venv/bin/python scripts/verify_openviking_projector.py --policy-stages`
在真实 OpenViking sidecar 上运行。新 artifact：
[s3-1-20260906T173109Z-4d29eaf4](../../artifacts/probes/s3-1-20260906T173109Z-4d29eaf4/s3-1.json)。

S3-3 相关断言全部通过：

- 重新核验 G3 admission；固定 Codex event receipt 为 6 个唯一事件，另有 1 次重复重放并返回 `duplicate`。
- 脱敏事件关联到 candidate、fixed batch、proposal、Ledger evidence、OpenViking projection 和拟注入 bundle。
- bundle 只作为审计输出；owner 不匹配时被省略，旧 revision 过滤有效；没有修改 Agent context。
- runner 使用临时用户、临时 namespace、临时 SQLite Ledger 和 synthetic payload；formal memory write、recall、injection 均关闭。

## S3-4 / G5

删除和取代场景的 12 个断言全部通过，覆盖：

- stale index 在 inactive 后被拒绝；
- 早期删除清理完成；
- 清理后的 in-flight write 被标记 `obsolete` 并再次清理；
- rescan 返回 `suppressed_after_deletion`；
- fresh policy 不再提供已删除或被取代内容；
- 基础投影的未知写恢复、lease fencing、索引可见性和旧 revision 过滤。

运行共 `40/40` assertions passed。OpenViking search visibility 本次受控延迟检查为
`1.935s`（上限 60s），不作为生产延迟指标。

## 脱敏与清理

artifact 只记录 synthetic payload、digest、固定错误分类、状态和临时标识；未发现
endpoint、认证头、API key、token、secret 或 password 字段/值。临时 namespace 的精确
projection 清理、搜索为空和临时用户撤销均通过；cleanup 状态为 `passed`。

## 阶段边界

本报告只关闭 S3-3 和 S3-4。S3-5/G6 不在本轮运行，且在取得单独的真实注入授权前保持
私人数据导入、正常 hooks、自动写入、召回和自动注入关闭。下一阶段如申请授权，必须明确
workspace、事件类型、写入权限以及是否允许下一会话召回/注入，并继续保留 kill switch、
审计日志、有限重试和回滚路径。
