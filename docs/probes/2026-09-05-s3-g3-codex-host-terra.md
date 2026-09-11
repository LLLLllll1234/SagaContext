# Codex CLI G3 宿主事件准入探针

**Probe ID：** `g3-20260905T135808Z-dbc31c09`
**G3 状态：** `passed`
**断言：** 19/19 `pass`

## 固定环境与证据边界

- CLI：`codex-cli 0.153.4`（固定期望 `codex-cli 0.153.4`）
- 模型：`gpt-5.6-terra`
- 配置 digest：`sha256:9f252e8a99b19ac754ba0b163a604765b20cbc7f32bc2dcdb94c8c3fbf8571d0`
- Capture digest：`sha256:1ef5f113a21a5ef8ea5188594096a5c8b3aa052fd81a985f8b9d9f95adc35c7f`
- Payload：仅合成 prompt、固定 marker 和临时 Git 仓库；仅保存字段形状、稳定 probe 内引用、枚举、摘要与耗时。
- 模型运行时：临时最小 `CODEX_HOME`；不保存 provider 地址或认证材料，不加载用户全局 hooks/插件。

## 场景与等待时间

| 场景 | Codex 退出码 | 耗时 | 阻塞分类 |
|---|---:|---:|---|
| `baseline_with_duplicate` | 0 | 80.043s | `none` |
| `hook_nonzero_exit` | 0 | 19.935s | `none` |
| `hook_timeout` | 0 | 68.842s | `none` |
| `restart_recovery` | 0 | 40.042s | `none` |

## 必需断言

| 断言 | 结果 |
|---|---|
| `executable_version_pinned` | **pass** |
| `config_fingerprint_recorded` | **pass** |
| `hooks_runtime_enabled` | **pass** |
| `synthetic_isolation` | **pass** |
| `event_SessionStart` | **pass** |
| `event_UserPromptSubmit` | **pass** |
| `event_PreToolUse` | **pass** |
| `event_PostToolUse` | **pass** |
| `event_Stop` | **pass** |
| `event_SessionEnd` | **pass** |
| `stable_event_linkage` | **pass** |
| `duplicate_event_observed` | **pass** |
| `hook_nonzero_exit_observed` | **pass** |
| `hook_timeout_observed` | **pass** |
| `restart_recovery_observed` | **pass** |
| `marker_context_injection` | **pass** |
| `failure_degrades_and_recovers` | **pass** |
| `redacted_receipts` | **pass** |
| `temporary_cleanup` | **pass** |

## 清理

- 临时根目录删除：`True`
- 全局配置修改：`False`
- 私人 transcript 读取：`False`
- 原始 ID 映射保存：`False`

Runner 只生成 capture；本结论由独立 evaluator 读取 capture 后产生。只有全部必需断言为 `pass`，G3 才为 `passed`；模型认证阻塞单独归类为 `blocked_environment`。
