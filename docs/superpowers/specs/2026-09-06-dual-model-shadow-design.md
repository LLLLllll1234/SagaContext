# Pro/Flash 隔离 Shadow 与 Judge 准入设计

**日期：** 2026-09-06

## 目标

在冻结 `openai-judge-prompt-v4`、`delta-v3` 和 `delta-to-proposal-v2` 后，对
`deepseek-v4-pro` 与 `deepseek-v4-flash` 分别运行一轮隔离 shadow。shadow 只执行
候选输入、Judge 判定和提案转换，保留脱敏 artifact；不调用正式 Ledger 写入、RecallPolicy、
宿主 hook 或自动注入。

## 方案

复用现有 `load_replay_dataset`、`build_adapter` 和 `run_replay`，新增
`scripts/run_real_judge_shadow.py` 作为隔离编排器。每个模型单独创建一个临时目录、临时
输入副本、临时 SQLite Ledger 路径和带 run ID 的 namespace 标识；模型运行结果写入独立
artifact 目录，两个模型不共享运行状态，也不互相补齐结果。

`run_replay` 增加每条 observation 的实际 attempt 数和失败历史，最终报告继续只记录错误分类、
延迟、摘要 digest 与配置指纹，不记录 API key、完整 endpoint、异常正文或完整响应正文。
shadow manifest 明确标记 `shadow_only=true`、`formal_memory_write=false`、
`recall_enabled=false`、`injection_enabled=false`，并记录临时资源是否清理。

跨模型审计读取两份独立 JSONL，按 `repeat_index + case_id` 对齐，分别比较 relation、body、
evidence 和 proposal conversion 的 canonical digest；只报告一致、差异和缺失，不合并模型结果。

## 准入与错误处理

Pro 与 Flash 必须分别拥有完整 42/42 的固定验收 artifact，且每一份 artifact 的 prompt、
schema、converter、dataset digest、timeout 和 max attempts 与冻结配置一致，才能标记
`shadow_admitted`。该判定不授权正常会话自动注入或正式写入。

Judge schema、认证、转换和其他非成功错误继续保持 error/blocked 分类；它们不能被转换为
`no_change`。retry 仍为有限次数，正式验收和 shadow 默认使用 3 次 attempt 与 300 秒 timeout。

## 验证

新增 `l0` 输入、重复 `fields.key`、错误诊断敏感信息和 42/42 准入校验的固定单测。执行
全量 unittest、`compileall` 和 `git diff --check`；完成 shadow 后检查两个模型 artifact、
跨模型审计、cleanup manifest 和正式验收总报告。
