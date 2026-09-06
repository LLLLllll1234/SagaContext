# Judge Pro/Flash 双模型验收与隔离 Shadow

**日期：** 2026-09-06（Asia/Shanghai；artifact ID 使用 UTC）

## 结论

冻结版本为：

- Prompt contract：`openai-judge-prompt-v4`
- Response schema：`delta-v3`
- Converter：`delta-to-proposal-v2`
- Dataset：`real-judge-v3`
- Dataset digest：`8ebf785c8c579140236521e0b8e93164741d75f9356bd18b26924e7732dc8565`
- Request timeout：`300s`
- Max attempts：`3`

Pro 与 Flash 的独立正式 artifact 均通过 `42/42`，准入校验分别检查了 3 个 repeat、每个
repeat 14 个 case、观察键唯一、冻结版本、超时/重试预算及 relation/body/evidence/conversion
指标。两模型结果没有合并，也没有互相补跑。

正式 artifact：

- Pro：[20260906T-pro-v3-retry-final](../../artifacts/real-judge/20260906T-pro-v3-retry-final/)
- Flash：[20260906T-flash-v3-key-fixed-final](../../artifacts/real-judge/20260906T-flash-v3-key-fixed-final/)

## 跨模型一致性

审计按 `repeat_index + case_id` 对齐四类 canonical digest：relation、body、evidence 和
proposal conversion。42 个对齐 observation 中 41 个四项完全一致；1 个 observation 只有
正文和 conversion digest 不同：

- Case：`v3-smoke-refine-gotcha`
- Repeat：3
- Pro：`async task is already closing`
- Flash：`when the async task is already closing`
- Relation：相同，`refine`
- Evidence：相同，`event-refine`
- 语义判定：两者都在冻结的 acceptable body 集合内，因此都通过；保留字面差异，不做输出合并

完整审计：[20260906T-dual-model-cross-audit.md](../../artifacts/real-judge/20260906T-dual-model-cross-audit.md)。

## 实际隔离 Shadow

Runner 为每个模型分别创建临时目录、临时 SQLite Ledger、临时 workspace 标识和独立
namespace 标识，只调用 Judge 与 converter。运行配置明确记录：`shadow_only=true`、
`formal_memory_write=false`、`recall_enabled=false`、`injection_enabled=false`。

本轮实际 shadow artifact：[20260906T151729Z-isolated-shadow](../../artifacts/real-judge/20260906T151729Z-isolated-shadow/)。

两模型均生成完整 42 条 observation，但当前执行环境没有 SagaContext LLM endpoint 或 key，
所以每条 observation 首次调用均为 `judge_configuration_error`，各自成功调用 `0/42`，
准入阻断。该失败没有降级为 `no_change`；临时 Ledger memory 数量均保持 `0 -> 0`，
临时 root 与 Ledger 均清理，且没有正式写入、RecallPolicy 或自动注入。

因此，本轮完成了 shadow runner 的隔离和失败路径验证，但没有把这次配置阻断称为真实模型
语义 shadow 通过。后续需在具备已授权 endpoint/configuration 的环境中重新运行同一 runner，
才能取得真实 shadow 成功率、重试率、延迟和提案分布。

## 最新复跑

在 2026-09-06 15:41 UTC 使用同一 runner 再次执行：[20260906T154145Z-isolated-shadow-rerun](../../artifacts/real-judge/20260906T154145Z-isolated-shadow-rerun/)。Pro/Flash 各生成 42 条 observation，均为首次尝试的 `judge_configuration_error`，成功调用均为 `0/42`，准入继续阻断。临时 Ledger 均保持 `0 -> 0`，资源 cleanup 全部通过；两模型对齐审计为 42/42 digest 相同，未产生可比较的模型语义输出。

## 准入边界

Pro 与 Flash 各自达到冻结的 `42/42` 后，才允许进入 shadow/admission 阶段；这不授权正常
会话自动写入或自动注入。有限重试与 `300s` timeout 保留。schema、认证、转换和 provider
错误继续为 error/blocked，不能转成 `no_change`。正常自动注入仍需单独审批和独立验收。

## 回归验证

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
PYTHONPATH=src .venv/bin/python -m compileall -q src scripts
git diff --check
```

结果：`147` 项测试通过，`compileall` 通过，`git diff --check` 通过。固定单测覆盖 `l0`
转换、重复 `fields.key`、敏感诊断不泄露和 42/42 独立准入形状校验。
