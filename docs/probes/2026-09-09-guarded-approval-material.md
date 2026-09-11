# Guarded rollout approval material

状态：**不可批准，待准入证据闭合**。当前配置和运行状态继续为 `off`。

## 固定边界

- workspace：只允许 activation receipt 中绑定的单一 registered workspace root；路径、workspace id、owner 和 generation 必须相等。
- 写入权限：仅 `guarded` 的人工 review receipt 可以提交本 rollout proposal；direct commit/forget、`active` 和历史 transcript 导入保持关闭。
- 召回权限：SessionStart 只允许该 workspace、owner、session 和 generation 的 fresh bundle；宿主消费回执与 injection receipt 分开保存。
- 配额：最多 10 个 session、20 个 candidate，任一达到上限进入 draining；第 11 个 session 或超额 candidate 拒绝。deadline 最长 24 小时。
- rollback：必须使用 current key、匹配不可变 rollback plan digest、workspace 和 owner；STOP/deadline 先递增 control epoch。rollback 以持久化 operation/claim/compensation receipt 工作，失败进入 `cleanup_required`，不得自动扩大清理范围。

## 批准前证据

1. 真实 Codex probe 必须 `passed`，且 `marker_context_injection` 为 `pass`；[最新 v5 结果](2026-09-09-marker-consumption-admission.md)为 `21/21 passed`，仅适用于 `codex-cli 0.153.4` + `gpt-5.6-terra`。
2. Judge Pro/Flash shadow 必须完成语义 observation；当前缺少 LLM 配置，preflight 为 `blocked_configuration`，模型请求数 0；旧 artifact 的 `unsuccessful_observation` 不作为准入证据。
3. 在授权环境中重新执行非空 locator 删除、重启恢复、并发 rollback、重复 receipt 和 compensation failure，并保存脱敏 shadow audit。
4. 审批 receipt 必须包含 workspace、owner、approver、current key id、generation、session/candidate quota、deadline、rollback plan digest 和明确的 STOP/rollback 操作人。

本任务的 workspace shadow 实施已获用户授权，仍等待 Judge 准入闭合；当前不启用正常 workspace hooks。guarded 需上述证据闭合并由批准主体单独签署，不得自动写入或自动注入。
