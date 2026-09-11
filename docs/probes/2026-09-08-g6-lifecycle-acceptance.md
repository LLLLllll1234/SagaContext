# S3-5/G6 真实隔离纵向验收

**日期：** 2026-09-08  
**运行 ID：** `g6-d47ea734a337419889e70f72a4884cd8`  
**状态：** `passed`

本次使用已授权的具体 plan，在临时 workspace、临时 SQLite Ledger、临时 OpenViking
用户/namespace 和六个独立 Codex ephemeral session 中运行。输入全部为固定 synthetic
fixture；没有导入私人 transcript 或私人记忆。使用 Judge：`deepseek-v4-pro`，
`openai-judge-prompt-v6` / `delta-v3` / `delta-to-proposal-v2`；宿主为固定
`codex-cli 0.153.4` / `gpt-5.6-terra`。

## 结果

- `103/103` assertions passed。
- 前置 G5、v6 Judge admission、workspace identity、四类事件、独立 session、Judge
  attempt、Ledger proposal、projection、RecallPolicy bundle、实际 SessionStart 注入和
  host task result 均通过。
- 两次 Judge batch 均首次调用成功，无 retry、schema error 或 conversion error。
- 正常 hooks、私人数据导入、正常 workspace 写入/召回/注入均未启用。

## 三条链路

| 链路 | 关键证据 | 结果 |
|---|---|---|
| 新增后使用 | source 声明 `pytest`；下一独立 session 只从新 bundle 得到该命令 | `{"command":"pytest"}` |
| 取代后使用 | Judge `supersede`；旧 memory retired；后继 memory active；混合 stale/fresh hits 只保留后继 | `{"command":"python -m unittest"}` |
| 删除后不可见 | Ledger `forget`；stale hits 皆 `inactive`；projection 删除；Ledger 重开后仍无 bundle | `{"command":null}` |

每个注入 receipt 与该次 RecallPolicy 新鲜组装的 bundle 完全一致。Hook 是否输出 bundle
与宿主是否消费 bundle 分开判定；六个 session 使用不同的哈希 identity。删除前后的缓存、
旧 revision 和重开 Ledger 均不能使内容重新出现。

## 清理与边界

`s3-1.json` 中记录的 `runtime_removed`、`hooks_removed`、`ledger_removed` 和后端
namespace/user cleanup 全部通过。artifact 只保留 synthetic payload、摘要 digest、
proposal/投影/召回结构和固定结果；未写入 endpoint、认证头、API key、token 或密码。

本次关闭 S3-5/G6 的隔离验收，不代表已开启正常会话自动化。后续如需启用正常 workspace
hooks、自动写入或自动注入，必须另行指定范围并保留 kill switch、审计和回滚。

## Artifact

[完整 G6 artifact](../../artifacts/probes/g6-d47ea734a337419889e70f72a4884cd8/s3-1.json)

验证：全量 `169` 项测试通过；`compileall` 与 `git diff --check` 通过。保留一条既存
Starlette/httpx 弃用警告，与本次运行无关。
