# S3-0 / G3 Codex 宿主事件准入

**日期：** 2026-09-05
**Probe ID：** `g3-20260905T124544Z-bd2b4ed7`
**结论：** `blocked_environment`；5/19 必需断言为 `pass`，其余 14 项为 `not_observed`

## 1. 固定环境与隔离边界

- Codex CLI：`0.150.0-alpha.8`；运行时 feature flag：`hooks stable true`。
- 模型：`gpt-5.6-luna`；配置 fingerprint：`sha256:9f252e8a99b19ac754ba0b163a604765b20cbc7f32bc2dcdb94c8c3fbf8571d0`。
- 使用临时 Git 仓库、仓库级 hooks、合成文件、合成 prompt 和固定 marker `G3_SESSION_START_CONTEXT`。
- 为避免加载用户全局 hooks、插件和项目数据，runner 生成临时最小 `CODEX_HOME`，只在进程内复制当前模型 provider 配置和认证材料；临时目录随后删除。
- capture 不保存 provider 地址、认证材料、prompt、工具参数/响应、路径、transcript 正文或原始宿主 ID。

## 2. 实测结果

| 场景 | Codex 退出码 | 等待时间 | 分类 | Hook receipt |
|---|---:|---:|---|---:|
| 正常执行与重复 handler | 1 | 16.541s | `model_authentication_failed` | 0 |
| hook 非零退出 | 1 | 14.851s | `model_authentication_failed` | 0 |
| hook 超时 | 1 | 14.215s | `model_authentication_failed` | 0 |
| 新进程恢复 | 1 | 14.176s | `model_authentication_failed` | 0 |

四个隔离 CLI 进程都在首个 hook receipt 前被模型认证拒绝。由于没有运行时事件样本，`SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop`、`SessionEnd`，以及重复、异常退出、hook 超时、重启恢复、marker 注入和稳定身份关联均为 `not_observed`。静态 feature flag、官方文档和源码声明没有被用于补齐这些事件。

这次结果只证明当前 CLI 认证环境阻断了探针。它不能推出 hooks 不支持，也不是 `failed_contract`。

## 3. Runner 与独立门禁

- Capture runner：[`scripts/probe_codex_host.py`](../../scripts/probe_codex_host.py)
- 脱敏 recorder：[`scripts/g3_hook_recorder.py`](../../scripts/g3_hook_recorder.py)
- 独立 evaluator：[`scripts/evaluate_codex_host_probe.py`](../../scripts/evaluate_codex_host_probe.py)
- Capture fixture：[`tests/fixtures/hosts/codex-cli-0.150.0-alpha.8.json`](../../tests/fixtures/hosts/codex-cli-0.150.0-alpha.8.json)
- 门禁 fixture：[`tests/fixtures/hosts/codex-cli-0.150.0-alpha.8-g3-evaluation.json`](../../tests/fixtures/hosts/codex-cli-0.150.0-alpha.8-g3-evaluation.json)
- 完整人读/机器 artifact：[`artifacts/probes/g3-20260905T124544Z-bd2b4ed7/README.md`](../../artifacts/probes/g3-20260905T124544Z-bd2b4ed7/README.md)

runner 只采集场景、事件 receipt、耗时和错误摘要；evaluator 单独读取 capture，输出每项 `pass/fail/not_observed`。只有 19 项必需断言全部为 `pass`，G3 才能写为 `passed`。认证失败优先归类为 `blocked_environment`。

## 4. 清理与后续边界

- 临时仓库与临时 `CODEX_HOME` 已删除。
- 用户全局配置未修改；未读取私人 transcript；未保存原始 ID 映射。
- G3 未通过，继续禁止自动宿主接入、正常会话 hooks、私人 transcript 采集和真实记忆注入。
- 需要先更新本机 Codex CLI 使用的模型认证，再原样重跑 capture 命令和独立 evaluator；不能复用本次静态声明作为运行时证据。
