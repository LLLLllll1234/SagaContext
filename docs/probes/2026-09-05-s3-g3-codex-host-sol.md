# Codex CLI G3 宿主事件准入探针

**Probe ID：** `g3-20260905T132958Z-d5a5a76e`
**G3 状态：** `failed_contract`
**断言：** 5/19 `pass`

## 固定环境与证据边界

- CLI：`codex-cli 0.153.4`（固定期望 `codex-cli 0.150.0-alpha.8`）
- 模型：`gpt-5.6-sol`
- 配置 digest：`sha256:9f252e8a99b19ac754ba0b163a604765b20cbc7f32bc2dcdb94c8c3fbf8571d0`
- Capture digest：`sha256:6846762a5ffe39b815d7b1f93cdf670885f947accc1c398a80d5c2a145bb28bb`
- Payload：仅合成 prompt、固定 marker 和临时 Git 仓库；仅保存字段形状、稳定 probe 内引用、枚举、摘要与耗时。
- 模型运行时：临时最小 `CODEX_HOME`；不保存 provider 地址或认证材料，不加载用户全局 hooks/插件。

## 场景与等待时间

| 场景 | Codex 退出码 | 耗时 | 阻塞分类 |
|---|---:|---:|---|
| `baseline_with_duplicate` | 0 | 20.260s | `none` |
| `hook_nonzero_exit` | 0 | 19.500s | `none` |
| `hook_timeout` | 0 | 17.419s | `none` |
| `restart_recovery` | 0 | 15.617s | `none` |

## 必需断言

| 断言 | 结果 |
|---|---|
| `executable_version_pinned` | **fail** |
| `config_fingerprint_recorded` | **pass** |
| `hooks_runtime_enabled` | **pass** |
| `synthetic_isolation` | **pass** |
| `event_SessionStart` | **not_observed** |
| `event_UserPromptSubmit` | **not_observed** |
| `event_PreToolUse` | **not_observed** |
| `event_PostToolUse` | **not_observed** |
| `event_Stop` | **not_observed** |
| `event_SessionEnd` | **not_observed** |
| `stable_event_linkage` | **not_observed** |
| `duplicate_event_observed` | **not_observed** |
| `hook_nonzero_exit_observed` | **not_observed** |
| `hook_timeout_observed` | **not_observed** |
| `restart_recovery_observed` | **not_observed** |
| `marker_context_injection` | **not_observed** |
| `failure_degrades_and_recovers` | **pass** |
| `redacted_receipts` | **not_observed** |
| `temporary_cleanup` | **pass** |

## 清理

- 临时根目录删除：`True`
- 全局配置修改：`False`
- 私人 transcript 读取：`False`
- 原始 ID 映射保存：`False`

Runner 只生成 capture；本结论由独立 evaluator 读取 capture 后产生。只有全部必需断言为 `pass`，G3 才为 `passed`；模型认证阻塞单独归类为 `blocked_environment`。
