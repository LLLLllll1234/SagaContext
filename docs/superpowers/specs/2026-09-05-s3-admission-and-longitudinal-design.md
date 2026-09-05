# S3 准入探针与真实纵向闭环设计

**日期：** 2026-09-05
**状态：** 设计已批准；G1 17/17、G3 terra 19/19 已通过独立准入，用户已要求按本设计顺序实施。S3-1 首次真实后端故障恢复 22/22 通过，见 `docs/probes/2026-09-05-s3-1-openviking-recovery.md`。文内授权条款保留设计阶段的门槛定义，不表示当前仍未获实施授权。
**上位设计：** `2026-09-05-sagacontext-v0.3-design.md`；S2 验收见 `docs/probes/2026-09-05-s2-acceptance.md`。

**实施更新（2026-09-06）：** 已按用户指令完成 S3-1 至 S3-5 的隔离合成验收，完整运行 69/69 通过。范围、实际输入、任务结果及限制见 `docs/probes/2026-09-06-s3-policy-shadow-g5-g6.md`；未启用正常会话自动化。

## 1. 目标与非目标

S3 的目标是验证一组真实 Backend/Host 组合能在不改变 Ledger 权威边界的前提下完成：真实投影、精确定位、索引可见性、受控清理，以及至少三条跨会话纵向链路。

本阶段不默认启用正常会话 hooks，不扫描私人 transcript，不把 health check 当作后端契约通过，不启动生产常驻 worker，也不把 Shadow 结果描述为 G6 真实闭环。

第一组候选固定为：

| 组件 | 待复核基线 | 边界 |
|---|---|---|
| Backend | OpenViking 独立 sidecar，部署记录观测版本 `v0.4.17.1` | 正式探针必须记录不可变镜像 digest；不依赖 `latest` |
| Host | 本机 Codex CLI | 以探针运行时实际可执行文件和版本为准，不外推到桌面端 |
| 数据 | 合成、脱敏、隔离 namespace | G1/G3 通过前不得导入私人记忆或 transcript |

设计编写时仅有健康检查与认证受阻 fixture；现有 G1/G3 独立准入均已通过，原始历史失败记录保留，最新证据见文档索引。

## 2. 准入状态机

每个门槛独立记录，不因另一门槛通过而自动通过：

```text
not_run -> running -> passed
                 -> blocked_environment
                 -> failed_contract
                 -> inconclusive
```

- `blocked_environment`：例如模型认证、Docker/网络或凭据条件阻断；不得解释为能力不支持。
- `failed_contract`：环境可执行，但探针断言失败；必须修复、降级或重新评审组合。
- `inconclusive`：证据不完整，不能进入真实适配编码。
- 任一 G1/G3 非 `passed`，系统继续停留在 S2；S2 本地 backend double 和显式调用保持可用。

每次探针运行固定 `probe_id`、候选版本、配置 fingerprint、代码 revision、开始/结束时间、结果状态和脱敏 artifact 清单。重复运行创建新 probe record，不覆盖历史证据。

## 3. G1 OpenViking 后端探针

### 3.1 前置固定

正式运行前保存：

- 实际镜像 digest、容器版本、API 版本和部署配置 fingerprint；
- 独立测试 namespace、generation、认证方式摘要；
- SagaContext adapter 版本和请求 schema 版本；
- 不保存 root key、请求正文中的秘密或私人内容。

当前 `docker-compose.yml` 使用浮动 `latest`，只能作为部署便利配置；G1 留证前必须解析并记录实际 digest，若无法固定则状态为 `inconclusive`。

### 3.2 合成 projection fixture

固定至少两个 memory：同一 `memory_id` 的 revision 1/2，以及不同 generation 的同形 fixture。每个 payload 从指定 Ledger revision 规范化生成，保存：

```text
owner_id, memory_id, revision, generation,
operation_key, projection_identity, payload_digest,
namespace, expected_locator
```

后端可以不提供原生 CAS。G1 只要求 SagaContext 能通过 metadata、locator 或受控管理区完成稳定身份映射；Ledger 的 revision CAS 和 S2 projector 的 obsolete/fencing 语义仍是本地权威。

### 3.3 必须验证的序列

1. 写入 revision 1，记录原始请求摘要、响应摘要和 locator。
2. 用 `operation_key` 或等价完整 identity 精确 locate，校验 `memory_id/revision/generation/payload_digest`。
3. 重复 materialize，确认重复请求不会产生第二个有效投影，或记录后端的明确非幂等限制并阻断生产适配。
4. 写入 revision 2，确认读取过滤不会把 revision 1 当作当前有效结果；不要求后端原生版本淘汰。
5. 等待索引可见性，记录轮询次数、延迟上界和最终命中 identity；查询超时必须分类为 `visibility_timeout`。
6. 删除 SagaContext 自主管理区中的明确 locator，核对删除确认、延迟可见性和重启后状态。
7. 重启 sidecar 后重新 locate 已写 projection，区分后端状态保留与后端数据清空。
8. 注入认证失败、服务不可用、请求超时和返回结构变化，验证 adapter 分类，不把未知结果直接当失败或成功。

### 3.4 G1 通过条件

G1 只有在以下证据全部齐备后通过：

- 稳定 locator 与完整 identity 可双向核验；
- revision/generation 过滤和旧 revision 隔离成立；
- 索引可见性有明确上界或被明确标记为不可保证；
- 自主管理区可精确枚举/定位/清理，不触碰原生记忆；
- sidecar 重启后的状态语义已验证；
- 错误、未知结果和后端不可用均有可观测分类。

若只有写入和 health check 通过，仍为 `inconclusive`，不得实现真实 projector。

## 4. G3 Codex CLI 探针

### 4.1 证据边界

静态 feature flag、源码声明事件和官方文档只能证明“声明或可能存在”，不能证明本机运行时实际触发。当前 fixture 的 `model_authentication_failed` 只能标记环境阻断，不能推出 hooks 不支持。

认证可用后，使用临时 Git 仓库、仓库级 hooks 配置和合成 prompt 重跑探针。不得读取已有 transcript，不修改用户全局配置，不使用正常项目数据。

G3 探针执行授权可以单独包含一次**探针专用合成 marker 注入**，用于判断 hook 返回的固定 marker 是否实际进入该临时 CLI 会话的 Agent context。该授权只在本次 probe 的临时仓库和临时进程内有效，并且必须满足：

- marker 是探针代码中预先固定的无语义常量，不包含 Ledger memory、用户内容、路径、凭据或动态检索结果；
- hook 只能返回该 marker 和最小协议字段，不连接 SagaContext daemon、Ledger 或 OpenViking；
- 仅核对 Agent 是否原样观察到 marker，不执行 marker 中的命令或指令；
- 临时配置、事件日志和进程随 probe 结束销毁，仅保留下文定义的脱敏 artifact。

这属于 G3 能力探针，不属于 S3-3 Shadow 的拟注入 bundle，也不属于 S3-5/G6 的真实记忆注入。未明确授权合成 marker 注入时，可以执行获批的事件采集子集，但注入断言必须记为 `not_observed`，G3 不得判为 `passed`。

### 4.2 观测项

只保存字段名不足以复核身份关联和重复事件。每个实际测试事件必须生成可关联但不暴露正文的规范记录：

```text
probe_id, occurrence_no, hook_event_name,
monotonic_offset_ms, payload_shape_digest,
session_ref, workspace_ref, task_ref, tool_call_ref,
source_event_ref, duplicate_group_ref,
exit_class, timeout_class, payload_keys, safe_enums
```

- fixture 明确生成的合成 ID 可以保存原值；任何非预期 ID 只映射为本次 probe 内稳定的顺序引用，如 `session-1`，不得保存原值；
- 同一原值在同一次 probe 内必须映射到同一引用，跨 probe 不要求稳定，避免形成长期可关联标识；
- `payload_shape_digest` 只覆盖字段路径、类型、允许保存的枚举和上述引用，不包含 prompt、工具参数、输出正文、路径或 transcript 内容；
- `duplicate_group_ref` 根据规范化事件身份计算，用来区分同类事件的合法多次发生与同一源事件重放；同时保存 occurrence 顺序，不能仅靠事件类型计数；
- 保存 hook recorder receipt 或等价写入结果，使“观察到两次”与“同一事件被幂等接收两次”能够分别复核。

观测范围包括：

- `SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop`、`SessionEnd`；
- 若实现需要，再单独验证 `PreCompact`、`PostCompact`、`Interrupt` 和 permission 事件；
- hook 超时、退出码、异常退出、重启恢复和重复事件；
- 输入 payload 是否含 session/task/workspace 标识，是否能安全映射到 EventJournal；
- 注入候选是否只是 dry-run，还是实际进入下一轮 Agent context。

只有实际观测到的事件才能进入 `verified_events`。未观测事件保持 `not_observed`，不得通过静态文档补齐。

### 4.3 G3 通过条件

`g3-probe-v1` 的旧 runner 以 Codex 子进程退出码 `0` 生成 `probe_result.status=passed`。这个 `passed` 只表示该次子进程正常结束，不能作为 G3 准入结论；即使旧 runner 输出 `passed`，缺少必需事件、重复、超时、恢复或注入断言时，G3 仍为 `inconclusive`。

G3 必须由独立的准入评估步骤读取 probe artifact，并逐项输出断言结果。runner 负责采集，评估器负责判定，两者状态不得共用一个布尔值。G3 通过至少要求：

- 固定 CLI 可执行版本和配置 fingerprint；
- 至少一条可重复的事件 payload 记录；
- 事件重复、超时、退出和重启恢复均有证据；
- 能明确区分事件采集、候选生成和 context 注入；
- 在明确的 G3 marker 授权下，固定合成 marker 的注入断言通过；
- 失败时可以安全降级为显式工具或回放。

准入记录必须列出每项断言、对应 artifact 引用和 `pass/fail/not_observed`；只有全部必需断言为 `pass` 时，门槛状态才能写为 `passed`。

如果认证仍阻断，状态保持 `blocked_environment`，不改写为 hooks 不支持。

## 5. Shadow、真实注入与 G6

### 5.1 S3-3 Shadow

Shadow 允许：合成事件进入 EventJournal、生成 candidate、执行固定 batch、写入隔离 Ledger、投影到测试 namespace，并记录“拟注入的 ContextBundle”。

Shadow 禁止：修改真实 Agent context、读取私人 transcript、启用正常会话 hooks、执行记忆命令或把后端搜索结果直接当权威正文。

Shadow 通过只证明候选、Ledger、projection 和恢复链路可观测，不证明 Agent 实际消费了记忆。

### 5.2 S3-5 真实纵向准入

真实注入必须另有明确批准，并使用最小授权、可撤销的合成/脱敏会话。每条链路保存：

```text
source event -> candidate -> proposal -> ledger revision
-> outbox/receipt -> recall decision -> actual next-session input
-> task result -> cleanup/recovery trace
```

至少完成三条链路：

1. 偏好：下一会话实际遵守一条用户明确偏好；
2. 项目：下一会话使用一条项目事实并能回溯 revision/evidence；
3. task checkpoint：新会话恢复目标、done/open/next，并完成预先定义的任务结果断言。

G6 不能由 Shadow、后端 search 命中或 Agent 输出包含关键词单独证明；必须有下一会话实际消费证据和任务结果证据。

## 6. S3 探针与真实适配阶段

设计批准与探针执行授权是两个独立门槛。取得设计批准后仍不得执行探针；只有用户再次明确授权具体探针、允许的外部状态变化和凭据使用范围后，才进入 S3-0。G1/G3 均通过后，才进入真实适配编码：

1. **S3-0 准入探针：** 在单独执行授权下运行 G1/G3，产出不可变版本、脱敏 artifact 和独立门槛判定。
2. **S3-1 后端适配：** 实现 `OpenVikingBackendAdapter` 和真实 namespace projector，只采用 G1 已验证契约，并在真实后端复验 P1-P6。
3. **S3-2 RecallPolicy：** 实现 Ledger 权威复核、scope/owner/revision 过滤、预算和省略原因。
4. **S3-3 Host shadow：** 采集已验证事件并记录拟注入 bundle，不修改真实 context。
5. **S3-4 删除/取代：** 完成 G5，包括索引延迟、在途写、重扫和恢复；通过后再申请真实注入授权。
6. **S3-5 真实纵向：** 在单独真实注入授权下完成三条 G6 链路和回归矩阵。

不得并行引入第二后端、自动反馈调参或生产常驻 worker；这些属于 S4/S5 或后续独立设计。

## 7. 退出矩阵

| 阶段 | 必须通过 | 未通过时 |
|---|---|---|
| S3-0 | G1 与 G3 的版本、配置、artifact 和独立准入断言全部通过 | 保持 S2；区分环境阻断与契约失败，不写真实 adapter |
| S3-1 | 真实 OpenViking adapter 通过契约测试并复验 P1-P6 | 禁用真实 projector，退回 S2 测试后端 |
| S3-2 | RecallPolicy 完成 Ledger 权威复核、范围过滤、预算和省略原因测试 | 不生成可供宿主使用的 ContextBundle |
| S3-3 | Host shadow 完成事件到拟注入 bundle 的脱敏轨迹和人工核对 | 不进入真实 context，不把 Shadow 计作 G6 |
| S3-4 | G5 删除/取代、索引延迟、在途写、重扫和恢复通过 | 禁止真实注入和私人数据导入 |
| S3-5 | 三条 G6 链路、回滚和清理证据 | 回退到 shadow/S2，不宣称真实闭环 |

## 8. 本文批准含义

本文存在三次独立授权，不得合并解释：

1. **设计批准：** 只允许定稿和提交本文，不允许启动服务、发送 OpenViking 请求、运行 Codex hooks 或使用凭据。
2. **探针执行授权：** 用户明确指定允许执行 G1、G3 或两者，并确认测试 namespace、外部状态变化和凭据边界后，才允许运行对应隔离探针。批准 G1 不自动批准 G3，反之亦然。G3 授权还必须明确是否包含临时会话内的固定合成 marker 注入；只有明确包含时，探针才可执行该注入。该 marker 授权不允许注入记忆正文、检索结果或任何私人数据，也不延伸到正常会话。
3. **真实适配编码批准：** G1/G3 artifact 经独立评估达到 `passed`，并由用户审阅后另行批准实现计划；探针通过本身不自动授权编码。

真实记忆注入还需要 S3-4/G5 通过后的第四次单独授权。G3 专用合成 marker 不计作真实记忆注入；除此之外，任何较早批准都不授权正常会话 hooks、私人 transcript、私人记忆导入或真实 context 注入。
