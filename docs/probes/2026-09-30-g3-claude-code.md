# 2026-09-30 G3-CC：Claude Code 宿主事件准入

**结论：只采集口径 22/22 passed；完整口径 21/23 inconclusive（注入未被模型消费）。** 按已拍板（2026-09-30），Claude Code 以只采集身份进入观察映射，SessionStart 不注入。

## 固定环境

- 宿主：`2.1.285 (Claude Code)`，冻结路径 `~/.local/share/claude/versions/2.1.285`；模型别名 `sonnet`。
- Probe ID：`g3cc-20260930T090830Z-afd7463a`。
- 隔离：临时 Git 仓库、临时 `CLAUDE_CONFIG_DIR`、`--setting-sources project,local`，只加载探针 hook。provider 变量只传给子进程，不写入 artifact。
- Capture（含第二次交互冒烟）SHA-256：`ba03e8fedfe877dc2c0937efaca3fbbeee600f7d9d7e4b74f163177642e2677e`。artifact 只保存在本地（`artifacts/probes/20260930-g3-claude-code/`），不提交。

## 结果

| 项目 | 结果 |
|---|---|
| 六类必需事件、稳定关联（session / prompt_id / tool_use_id）、重复事件 | pass |
| hook 非零退出、hook 超时（1 秒超时，2.5 秒 sleep） | pass：宿主退出码 0，PostToolUse 照常触发；超时被记为 `cancelled` |
| `--resume` 后 session_id 连续，SessionStart `source=resume` | pass |
| `startup` / `resume` 两种 source 已观察到 | pass；`compact` / `clear` 在 `-p` 模式下没有探测 |
| hook 子进程继承 `SKILL_EVOLUTION_REVIEW=1` | pass，skill-evolution 排除机制有效 |
| 脱敏回执、临时目录清理、版本与路径固定 | pass |
| 交互冒烟 | pass（第二次）：见下文 |
| SessionStart `additionalContext` 被模型消费 | **未观察到**（五个场景均回复 `MISSING`） |
| `restart_recovery` 完整口径 | 依赖注入 marker，完整口径不通过；只采集口径下只检查 resume 连续性 |

## 注入未消费的诊断

- 宿主接收了注入内容：debug 日志中有 `provided additionalContext`，transcript 中有 `hook_additional_context` attachment。
- 模型没有使用：换 opus、把内容写成项目事实、改从 UserPromptSubmit 注入、改用 stream-json 输入，结果都一样。
- 同一环境中，CLAUDE.md 和 `--append-system-prompt` 的内容模型可见。
- 原因没有区分出来：可能是本机第三方 provider（`ANTHROPIC_BASE_URL`）转发时丢弃，也可能是模型把这类内容当作不可信而忽略。需要用官方端点做对照。
- 相关观察（来源未核实）：隔离子会话中，模型提到上下文里有一段 "[Development Workflow Policy]" 文本，不是探针注入的。

## 交互冒烟

**第一次**：认证缺失导致需要登录（`--setting-sources` 排除了存放 provider 变量的用户设置）。修复启动方式后重跑，记录到一次异常：回复约 35 秒后出现一个新 session_id 的 SessionStart，`source` 不在已知值内（当时 recorder 只记为 `other`），payload 多出 `seconds_since_last_response`、`context_tokens`、`prompt_cache_likely_expired` 等字段；随后 SessionEnd 只发给原 session。

**第二次**：recorder 已改为保存未知 source 的原始值；回复后等待约 12 分钟再 `/exit`。只有一个 session，事件依次为 SessionStart(startup) → UserPromptSubmit → PreToolUse ×2 → PostToolUse → Stop → Notification（空闲约 60 秒）→ SessionEnd(`prompt_input_exit`)。**没有再出现第二个 SessionStart。**

第一次的异常没有复现，触发条件未知，原始 source 值也没有保存下来。现有防护：

- daemon 对 claude-code 的非 `startup`/`resume` SessionStart 一律拒绝（`session_start_source_not_allowed`），这类事件不会开 session，也不会注入。
- 如果之后的 prompt 挂在新 session_id 上，它会作为新 session 进入并占用配额，而且可能永远收不到 SessionEnd。巡检仍以 deadline 兜底收尾，不会遗留数据，但可能提前耗尽 10 个 session 的配额。

观察窗口期间，被拒绝的 SessionStart 会留下 `event_rejection` 回执，窗口报告按 reason 统计这类事件。如果再次出现，就用这些回执决定处理方式。

## 未覆盖

- `compact` / `clear` 的 SessionStart 形状（需要交互模式下手动触发）。
- 第一次冒烟中未知 source 的原始值和触发条件。
- 注入失败的根因。
