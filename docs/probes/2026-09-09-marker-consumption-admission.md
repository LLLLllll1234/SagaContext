# Codex marker 消费与 Judge 准入复核

当前结论：Codex G3 **21/21 passed**；Judge 因缺少配置尚未开始语义验证，真实 workspace shadow 未启动，guarded 未授权。当前加载的 rollout 配置为 `off`，默认配置指向的正式 Ledger 尚不存在。

## Codex 实测

- 固定宿主：`codex-cli 0.153.4`；实测模型：`gpt-5.6-terra`。不代表其他模型或宿主版本通过。
- [原始脱敏 capture](../../artifacts/probes/20260909-marker-v5/capture.json)
- [独立 evaluator](../../artifacts/probes/20260909-marker-v5/evaluation.json) / [报告](../../artifacts/probes/20260909-marker-v5/report.md)
- Probe ID：`g3-20260909T145640Z-90a9500e`。
- Capture SHA-256：`aa18865e83f143615e8fd9621c4f9a5358012cdd4c2ecaefbef8cad29c534d0e`。

保持现有 `hookSpecificOutput.additionalContext` schema。未修改 schema 的单独基线复现已消费 marker；旧的未观察结果不能据此归因于 host schema 缺陷。本次官方 hooks 文档访问受到 HTTP 403/TLS 错误阻断，当前准入结论依据真实固定版本实验。

v5 每个场景生成独立随机 marker，prompt 和 synthetic.txt 不包含该 marker。只有最后一条模型消息去除首尾空白后与 marker 完全相等才算消费；工具或其他非模型消息包含 marker 会使增强断言不通过。保存预期 marker 与最终消息摘要，不保存完整模型输出或认证信息。

| 场景 | 实测结果 |
|---|---|
| 无上下文对照 | hook 返回空对象，最终消息为 `MISSING` |
| baseline + duplicate | 最终消息消费随机 marker |
| hook nonzero exit | host 正常退出并消费 marker |
| hook timeout | host 正常退出并消费 marker |
| restart recovery | 新的 CLI 进程正常执行并消费新 marker |

六类必需事件全部观察到；临时 home/workspace 清理完成。这里的 restart recovery 是故障场景之后启动新 CLI 进程，不能替代 Ledger/Projector 的进程崩溃恢复验收。此次使用临时 Git workspace，不是正常 workspace hooks 上线。

## Judge 阻断与执行入口

最新授权 Judge shadow 已完成：Pro/Flash 各 42 条 observation，`admission_errors=[]`；跨模型审计为 `42/42 semantic_equivalent`，差异为 0。两个临时 Ledger 均保持 `0→0`，未发生正式写入、召回或注入，临时目录和 Ledger 清理成功。证据：[shadow-manifest](../../artifacts/real-judge/20260910-authorized-shadow-01/shadow-manifest.json)、[cross-model-audit](../../artifacts/real-judge/20260910-authorized-shadow-01/cross-model-audit.md)。

新增配置预检在任何模型调用前执行；缺配置只写 preflight，不再生成整批失败 observation。此次授权运行的预检状态为 `ready`，模型请求正常完成。底层 replay 的错误分类保留兼容。

支持提供本机已授权 TOML 文件，避免在命令或聊天中传递密钥。提供路径后，在仓库根目录执行：

```sh
PYTHONPATH=src .venv/bin/python scripts/run_real_judge_shadow.py \
  --config /path/to/authorized-config.toml \
  --output-dir artifacts/real-judge/next-authorized-shadow
```

默认仍为 `deepseek-v4-pro`、`deepseek-v4-flash`，冻结 v4 数据集各 14 cases × 3 repeats，timeout 300s、最多 3 attempts。需检查两模型 admission_errors 以及跨模型语义审计，不能只以 HTTP 成功判定准入。

## 后续边界与验证

Judge 语义准入现已闭合，可以进入独立隔离 workspace shadow；该 shadow 仍必须绑定本仓库 identity 和独立 Ledger，只观察新事件与候选，导出审计后回滚清理。guarded 继续等待单独批准，不能从此次 Judge 成功推导出写入或召回授权。

## 已批准 workspace shadow（2026-09-10）

按用户批准的边界，在 `/Users/lqy0584/Downloads/SagaContext` 绑定 workspace identity，使用独立临时 Ledger 完成一次 shadow 生命周期。1 条新合成事件被接收，生成 1 条 candidate 和 1 个 `proposed` batch；正式 memory 为 0，召回与注入均关闭。rollback 完成后 residual candidate 为 0，临时 Ledger 与目录已清理。证据：[shadow-report.json](../../artifacts/real-judge/20260910-authorized-workspace-shadow/shadow-report.json)。该结果只证明批准范围内的隔离 shadow 能力，不启用 guarded 或正常会话自动化。

本轮验证：`222 passed, 96 subtests passed`；`compileall` 与 `git diff --check` 通过。新增测试覆盖精确最终消息、工具输出误判、随机 marker 摘要篡改、无注入对照、host version 漂移和 Judge 缺配置零调用。
