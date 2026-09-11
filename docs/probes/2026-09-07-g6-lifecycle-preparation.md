# S3-5/G6 跨会话验收准备

**状态：准备已实现；未执行真实宿主注入。当前验收阶段仍停在 S3-4。**

已生成的待授权清单：[plan.json](../../artifacts/probes/g6-lifecycle-prepared/plan.json)。
本地三链路证据：[local-verification.json](../../artifacts/probes/g6-lifecycle-prepared/local-verification.json)。
两者用途不同：清单绑定未来执行配置，本地证据明确使用测试替身，不是 live G6 artifact。

## 交付范围

- `scripts/g6_lifecycle.py`：生成具体计划，检查 v6 两模型正式/Shadow artifact、G5
  前置证据及源码摘要；包含新增、取代、删除三条链路。
- `scripts/g6_lifecycle_hook.py`：仅处理指定 workspace 的四类事件，SessionStart
  重新读取 Ledger 并运行 RecallPolicy，不直接使用过期 bundle 文本。
- `scripts/verify_openviking_projector.py --g6-plan ... --execute-plan-digest ...`：
  复用真实后端隔离、G5 复核和清理；未提供匹配摘要时，在配置读取和远端访问前拒绝执行。
- BatchWorker 修复 Judge/Ledger 的 supersede 语义连接：同一事务退役旧记忆并创建后继，
  两者共享 proposal/evidence 来源；后继写入失败时整个事务回滚。

设计与详细边界见 [G6 lifecycle 设计](../superpowers/specs/2026-09-07-g6-lifecycle-design.md)。

## 三条链路与判定

| 链路 | 会话与输入 | 必须通过的证据 |
|---|---|---|
| 新增后使用 | 空记忆对照 → 声明 pytest → 新会话查询 | 对照返回 null；事件到真实 Judge、proposal、Ledger、投影完整；新会话返回 pytest |
| 取代后使用 | 独立会话改为 python -m unittest → 新会话查询 | 旧记忆 retired、后继 active；旧 hit 被拒绝；注入仅含后继；答案使用新命令 |
| 删除后不可见 | 显式 forget → 索引/缓存旧 hit → 新会话查询 | 注入为空、答案为 null；投影删除；Ledger 重开后仍不可召回 |

六次宿主调用使用独立临时 CODEX_HOME 和不同 ephemeral session。计划中的四类事件为
`SessionStart / UserPromptSubmit / Stop / SessionEnd`；没有新增普通 workspace 的 hooks。
模型生成最终回答是否消费记忆与 hook 是否输出 bundle 分开断言。

这里的候选由 runner 从固定 synthetic 事件显式选择。通过后可证明这组受控链路，
不能宣称任意会话的自动候选提取或线上任务质量已验证。

## 执行配置清单

| 配置 | 固定范围 |
|---|---|
| Workspace / Ledger | plan.json 中的绝对临时路径，仅此次 run 使用 |
| OpenViking | 已准入 sidecar；计划内的临时用户和独立 namespace |
| Judge | deepseek-v4-pro，prompt-v6 / delta-v3 / converter-v2；沿用已冻结 v4 验收基线 |
| 消费宿主 | codex-cli 0.153.4，gpt-5.6-terra；沿用已验证的宿主配置，不与 Judge 配置混用 |
| 写入 | 仅临时 Ledger 和测试 namespace；包括一次新增、一次原子取代及删除 |
| 召回/注入 | 仅上述 workspace 的六个 synthetic 会话，SessionStart 注入 |
| 关闭项 | 正常 hooks、私人 transcript/记忆导入、正常 workspace 自动写入/注入 |
| 中止 | plan.json 同目录创建 STOP；runner 停止并进入清理 |
| 产物 | artifacts/probes/<计划 run_id>/s3-1.json，保留失败和清理状态 |

宿主临时 home 复用已有最小化配置复制逻辑，可能临时复制宿主认证文件；文件仅在临时目录
存在，完成或异常后清理，不进入报告。Judge 密钥只从当前进程环境/本地 SagaContext
配置读取，计划与 artifact 不含密钥或 endpoint。G6 不会自动从对话历史提取旧密钥。

## 操作入口

生成新计划只写本地产物，不启动宿主、不创建 Ledger、不调用 LLM 或后端：

```sh
PYTHONPATH=src .venv/bin/python scripts/g6_lifecycle.py --output artifacts/probes/g6-lifecycle-prepared
```

真实执行需在用户授权具体计划后，由操作方传入同一计划和摘要：

```sh
PYTHONPATH=src .venv/bin/python scripts/verify_openviking_projector.py \
  --g6-plan artifacts/probes/g6-lifecycle-prepared/plan.json \
  --execute-plan-digest 2385a210a17e63df51130276cb595801fae37ab35f46a5e03123dabb09369e08
```

摘要用于绑定授权内容，不是授权本身。源码、计划或前置 artifact 改变后必须重新生成计划。
未通过或中止的运行不覆盖；重新执行使用新计划和新 run ID。首次执行还会在隔离环境中
重验现有投影与 G5 断言，确认当前后端状态符合前置要求。

## 本地验证

本地测试使用真实 Ledger、BatchWorker、Projector、RecallPolicy 及 hook 处理器，
模型、宿主和后端使用测试替身。覆盖完整三链路、宿主回答错误、G5 阻断、STOP、workspace
越界、计划篡改、源码变化、缺少执行摘要、异常清理和取代原子回滚。
本地测试结果不计作新的 live G6 passed；真实注入执行仍待单独授权。

本轮验证：全量 `169` 项测试通过；`compileall` 与 `git diff --check` 通过。新生命周期
测试共 `9` 项，完整本地链路产物中的 `61/61` 断言通过，临时资源已清理。仍有既存
Starlette/httpx 弃用警告。

## 待授权执行范围

计划 run ID：`g6-d47ea734a337419889e70f72a4884cd8`。

Workspace（执行时才创建）：
`/private/var/folders/06/5smjzqgx48d2n36yrjk2775m0000gn/T/sagacontext-g6-d47ea734a337419889e70f72a4884cd8/g6/workspace`。

授权内容应明确允许该计划的 synthetic 事件、临时 Ledger/namespace 写入、六个隔离会话的
下一会话召回与实际 context 注入，以及结束后的删除/清理。运行前检查宿主版本与本地配置，
只有全部链路断言和最终清理通过，才能将本次 G6 标记为 passed。正常 workspace 自动化
的启用不包含在此授权范围内。
