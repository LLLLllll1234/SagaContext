# SagaContext S2 持续维护设计

**日期：** 2026-09-05

**状态：** 设计已批准，尚未实现。

**上位设计：** `2026-09-05-sagacontext-v0.3-design.md` 的 S2 持续维护阶段。

**前置基线：** S1 数据收口已实现并通过 53 项测试，见 `../../probes/2026-09-05-s1-acceptance.md`。

## 1. 目标与边界

S2 在 S1 的权威 Ledger 上建立可恢复的本地持续维护闭环：

```text
EventJournal
  -> 固定 reconcile batch
  -> evidence 与冻结 anchor
  -> 结构化 DeltaProposal
  -> Ledger 原子 commit_batch
  -> task checkpoint 与 outbox
  -> 可控故障 projector
```

S2 必须证明：Ledger 提交后的投影失败不会丢任务、重复生效或回退版本；事件、候选、提案和批次在进程中断后可恢复；合法 `no_change`、Judge 失败、人工审批和并发新候选各自拥有可区分的持久状态。

本阶段仍不连接真实 OpenViking，不启用真实宿主 hooks、自动注入、生产常驻 worker 或私人 transcript。daemon 的 `POST /events` 继续固定返回 HTTP `501`。测试和显式本地域调用只使用合成数据、脱敏 fixture、`ScriptedJudge` 和 `InMemoryBackend`。

Ledger 继续是正文、版本、范围、证据和删除状态的唯一权威。projector 只能消费已提交 outbox；后端命中、locator、receipt 或 score 都不能成为权威正文或事实证明。所有后端调用必须发生在 Ledger 写事务之外。

## 2. 模块职责

```text
src/sagacontext/
|-- ledger/
|   |-- models.py          # CommitBatchPlan 等不可执行领域对象
|   |-- schema.py          # v2 migration
|   `-- service.py         # Ledger 原子事务与权威读取
|-- maintenance/
|   |-- models.py          # Event、Candidate、Batch、Proposal
|   |-- journal.py         # EventJournal、cursor、alias、quarantine
|   |-- batches.py         # 固定批次、lease、Judge 调度
|   `-- review.py          # conflict 人工决策
|-- projection/
|   `-- worker.py          # outbox 状态机、核验与 fencing
|-- backends/
|   |-- base.py            # BackendAdapter 契约
|   `-- memory.py          # 独立 state、客户端和故障注入
`-- application.py         # 显式组合服务，不启动后台 worker
```

模块权限边界：

- `EventJournal` 只持久化、定位和去重来源事件，不判断事实真假。
- `BatchService` 冻结输入并协调状态，不直接修改 MemoryHead。
- `ProposalJudge` 只接收结构化输入并返回结构化建议，无数据库、后端或文件写权限。
- `Ledger` 是唯一能提交 revision、evidence、MemoryHead 和 outbox 的组件。
- `Projector` 只能读取已提交 Ledger revision 并消费 outbox，不能改写权威事实。
- `BackendAdapter` 不接触 Ledger 连接或事务。
- 旧 `runner.py`、`reconcile.py`、`anchors.py` 和 `store.py` 不进入新运行路径，S2 也不顺手删除这些旧模块。

## 3. Schema v2 迁移边界

Schema v2 只升级现有 `ledger-v3.db`。S1 的 owner、project、workspace、task、session、memory、revision、evidence、suppression 和 outbox 数据在迁移后必须可读并保持原语义。

迁移通过单个原子迁移事务创建新表、索引和受控列变更，并在最后写入 `schema_migrations(version=2)`。任一 DDL、数据校验或版本登记失败，整个迁移回滚；重新打开数据库时只能看到完整 v1 或完整 v2，不能留下半套 v2 表。迁移前后的 S1 不变量和固定验收命令必须继续通过。

旧 `${SAGACONTEXT_HOME}/state.db` 仍然不得读取、修改、创建或迁移。S2 不改变 S1 对旧 Store 数据的隔离承诺。

## 4. EventJournal

### 4.1 中立事件

`events` 保存：

```text
event_id, owner_id, session_id, workspace_id,
host, host_version, schema_version, event_kind,
occurred_at, received_at, trust_class,
source_generation, source_event_key,
source_locator_json, payload_json,
parser_version, ingest_sequence
```

业务 `event_kind` 首版限定为：

```text
session_opened | user_message | tool_started | tool_finished |
checkpoint_requested | compaction_observed | session_closed
```

事件身份使用：

```text
UNIQUE(owner_id, host, session_id, source_generation, source_event_key)
```

`parser_version` 不参与事件身份。重新解析可更新解析状态，但不能生成第二份事实证据。

### 4.2 Source cursor

`source_cursors` 保存：

```text
owner_id, host, session_id, source_locator,
source_generation, byte_offset, source_fingerprint, updated_at
```

event 与相应 capture cursor 必须在同一事务提交后才返回 accepted。不完整尾行不入库且不推进 cursor。文件截断、替换或内容代际变化必须创建新的 `source_generation`，不能沿用旧字节位置。

### 4.3 Alias

`event_aliases` 使用一个复合主键，不再重复创建等价唯一索引：

```text
PRIMARY KEY(owner_id, host, session_id, source_generation, alias_event_key)
```

canonical event 必须属于相同 `owner_id + session_id + source_generation`。禁止跨 owner、session 或 generation 建立 alias。重复写入相同映射幂等；同一 alias 指向不同 canonical event 返回冲突。可确认同源的 hook/transcript 事件只计算一份独立 evidence；无法确认同源时不得把两者自动当作两次确认。

### 4.4 Quarantine

`event_quarantine` 的稳定唯一键为：

```text
UNIQUE(
  owner_id, source_generation, source_locator_digest,
  byte_start, byte_end, payload_digest
)
```

完整坏行先写 quarantine，再推进 cursor。同一坏行重放返回已有记录，不重复增加错误计数。相同字节范围的内容发生变化时由新 `payload_digest` 区分，但 source 代际检测应优先创建新 generation。

## 5. Candidate 与固定 Batch

### 5.1 数据模型

`candidates` 保存：

```text
candidate_id, owner_id, session_id, task_id,
kind, memory_type_hint, scope_hint_json,
topic_key, event_ids_json, status, result_ref,
active_batch_id, claim_token, created_sequence
```

Candidate 状态为：

```text
pending | processing | awaiting_review | settled | quarantined | retry
```

`batches` 保存：

```text
batch_id, owner_id, session_id, task_id,
event_upper_sequence, input_digest,
status, lease_owner, lease_token, lease_until,
attempt_count, next_attempt_at, last_error_class,
created_at, settled_at
```

Batch 状态为：

```text
pending | running | proposed | awaiting_review | review_committing |
retry | settled | blocked
```

关联表为：

```text
batch_events(batch_id, event_id)
batch_anchors(batch_id, memory_id, revision)
batch_candidates(batch_id, candidate_id, candidate_claim_token, released_at)
```

每个未释放 candidate 只能属于一个 batch。创建 batch 时以条件更新把 candidate 从 `pending/retry` 改为 `processing`，写入 `active_batch_id` 和新 `claim_token`。不得使用 session 级 `consumed=true` 批量清空候选。

### 5.2 固定输入

创建 batch 的短事务冻结：

- 当前 `event_upper_sequence` 与明确 event IDs；
- 当前可领取 candidate IDs；
- session、task 和 scope；
- anchor 的 `memory_id + revision`；
- policy、schema 和 Judge 版本。

batch 创建后到达的新事件或候选不属于该批次，保持 `pending` 等待后续 batch。提交时必须再次校验 batch lease，以及 candidate 的 `active_batch_id + claim_token + status`；任一不匹配则整个事务回滚。

### 5.3 Batch lease fencing

每次领取 batch 生成新的随机 `lease_token`。任何 proposal 持久化、状态迁移或 `commit_batch` 都必须匹配：

```text
batch_id + lease_owner + lease_token + 当前状态
```

lease 过期后的旧 worker 被 fenced，不能提交 proposal、结算 candidate 或修改 batch 状态。重新领取创建新的 attempt 和 lease token，不复用旧运行记录。

## 6. Anchor 与 Proposal

### 6.1 Anchor 选择

S2 不调用真实搜索后端。锚点只来自 Ledger：

1. candidate 显式引用的 target；
2. 同 owner、memory type、scope 和 topic key 的当前记录；
3. 当前 task checkpoint；
4. 本批事件明确引用的 memory ID。

锚点冻结到 `batch_anchors`。Judge 只能引用集合内的 `memory_id/revision`。未知 target 的非 `new` 操作直接拒绝，不能自动降级为 `new`。

### 6.2 ProposalJudge 与提案

S2 定义可注入的 `ProposalJudge` 协议，默认不配置真实 LLM。测试使用 `ScriptedJudge` 返回确定性结果或可分类错误。

`proposals` 保存：

```text
proposal_id, batch_id, predecessor_id,
operation, target_id, expected_revision,
memory_type, scope_json, payload_patch_json,
evidence_ids_json, rationale_redacted,
input_digest, output_digest, source_kind,
status, created_at
```

Proposal 状态为：

```text
proposed | committed | no_change | awaiting_review |
rejected | invalidated | superseded
```

operation 限定为：

```text
new | confirm | refine | supersede | conflict | no_change
```

Judge 失败不能伪装成空数组或 `no_change`，而是使 batch 进入 `retry/blocked`。合法 `no_change` 必须持久化 proposal 并精确结算 candidate。`conflict` 创建持久 conflict，candidate 和 proposal 进入 `awaiting_review`。

### 6.3 Proposal 恢复

恢复已有 proposal 前重新计算和核对：

- `input_digest`；
- batch 冻结的 event/candidate 集合；
- `batch_anchors` 中每个 `memory_id/revision`；
- proposal 当前状态。

全部一致且状态为 `proposed` 时，恢复继续确定性校验与提交，不再次调用 Judge。anchor head 已变化时，旧 proposal 标记 `invalidated`，通过有限重判生成带 `predecessor_id` 的 successor；不能直接提交。batch 输入摘要变化视为持久数据损坏，进入 `blocked`。已经提交、拒绝或失效的 proposal 不得再次提交。

`invalidated` 表示该 proposal 因输入、锚点或审批基线过期而失去提交资格；`superseded` 表示已经持久化了取代它的 successor proposal。创建 successor 与把 predecessor 标记为 `superseded` 必须在同一事务完成，不能留下无 successor 的 superseded 状态。

## 7. 不可执行的 CommitBatchPlan

`CommitBatchPlan` 是 `frozen`、`extra="forbid"` 的结构化领域对象，只允许：

```text
batch_id, proposal_ids, expected_heads,
memory_operations, evidence_links,
candidate_results, conflict_records, task_update
```

`memory_operations` 仅允许 `new/confirm/refine/supersede`。Plan 禁止携带 SQL、表名、shell、脚本、可调用对象、动态更新表达式、后端请求或任意路径写入。proposal 文本只能作为数据进入 payload，不能控制事务行为。Ledger 根据枚举字段执行固定 SQL。

## 8. 原子 commit_batch

新增 Ledger 内部方法：

```text
commit_batch(plan, lease_token) -> BatchCommitResult
```

它不能通过循环调用 S1 的公开 `commit()` 实现。单个 Ledger 写事务依次完成：

1. 校验 batch 的 `lease_owner + lease_token + status`；
2. 校验 candidate 的 `active_batch_id + claim_token + status`；
3. 复核所有 evidence、scope、suppression、target 和 expected revision；
4. 写 revision、MemoryHead 和 evidence 关联；
5. 创建 outbox；
6. 写 conflict 或 `no_change` 结果；
7. 更新 task checkpoint 与 `tasks.last_active`；
8. 精确结算本 batch candidate 和 batch。

任一自动 proposal 的 CAS 校验失败，本事务不写任何 memory revision、outbox 或 candidate 结果。旧 proposal 进入有限重判，避免部分提交后无法确定剩余 proposal 的语义。

## 9. Awaiting Review

Review 状态迁移为：

```text
running -> awaiting_review -> review_committing
                            -> awaiting_review
review_committing -> settled | awaiting_review
```

进入 `awaiting_review` 时，batch 释放 worker lease并清空 `lease_owner/lease_token/lease_until`；conflict 和 proposal 已持久化；candidate 继续由该 batch 冻结，其他 batch 不得领取。后续新证据必须创建新 candidate，不能复用未决 candidate。

`conflicts` 保存：

```text
conflict_id, batch_id, candidate_id, proposal_id,
target_id, base_revision, reason, status,
resolution, resolved_by, created_at, resolved_at
```

`review_receipts` 保存：

```text
owner_id, receipt, request_digest, conflict_id,
decision, result_json, created_at
PRIMARY KEY(owner_id, receipt)
```

人工请求携带 `conflict_id + decision + receipt`，receipt 在 owner 范围内唯一且与规范化请求摘要绑定：

- `accept_old`：创建 `source_kind=human_review` 的 successor proposal，原子结算 candidate 和 batch，不写新 revision。
- `accept_new`：重新读取 head，创建人工 successor proposal；batch 用新 lease token 进入 `review_committing` 后原子提交。
- `defer`：维持 `awaiting_review`。
- head 已变化：返回 `stale_review`，旧 proposal 进入 `invalidated`，刷新材料后必须再次确认。

`review_committing` 是明确的 batch 状态；`invalidated/superseded` 是明确的 proposal 状态。重复相同人工请求从 `review_receipts` 返回既有结果；复用 receipt 改变 conflict 或决定必须拒绝。人工 reset 不复用旧 proposal 或 attempt。

## 10. Task Checkpoint

checkpoint 是 `task` scope 的普通权威记忆，payload 固定为：

```text
goal, done, open, next, touched_paths, outcome
```

`done/outcome` 必须引用支持它的事件或 Verification；Agent 自述不能单独证明完成。`session_closed` 只能触发 checkpoint batch，不能自动完成 task。同项目、同分支的不同 task 各自维护 checkpoint。checkpoint revision、任务时间、evidence、candidate 结算和 outbox 必须在同一事务提交或回滚。

## 11. Projector 状态机

### 11.1 状态定义

```text
pending/retry -> running -> confirmed
                         -> unknown
                         -> retry
                         -> obsolete
                         -> blocked
unknown -> confirmed | obsolete | retry | blocked | unknown
```

状态语义：

| 状态 | 语义 |
|---|---|
| `pending` | Ledger commit 同事务创建，尚未领取 |
| `retry` | 已确认后端未生效的暂时错误，等待下一次尝试 |
| `running` | projector 已持有有效 lease |
| `unknown` | 调用可能已生效，但客户端失去确认；只能先核验 |
| `confirmed` | 后端对象已核验，receipt 已持久化 |
| `obsolete` | 指定 revision 已不是当前有效 head |
| `blocked` | 永久能力缺失、结果冲突、数据不一致或超过上限 |

明确发生在 `call_started_at` 前的失败进入 `retry`。调用开始后的超时、进程中断或响应丢失进入 `unknown`，不得由普通 lease 重领直接再次 materialize。

### 11.2 投影身份

```text
projection_identity = (backend, generation, memory_id, revision)
operation_key = hash(action, backend, generation, memory_id, revision, target_locator?)
```

`materialize` 的 `target_locator` 为空。`delete` 必须指定待清理 locator；多个 locator 分别创建 cleanup outbox。generation 始终参与身份，不能跨 generation 查重或确认。S2 的 delete 仅表示清理测试投影，不表示真实后端 forget 已完成。

`payload_digest` 从指定 `(memory_id, revision)` 的规范化 Projection 计算，不包含时间戳、attempt、lease 或其他运行字段。locate 返回结果必须同时校验 operation identity、generation 和 digest。

### 11.3 持久恢复状态

outbox 增加：

```text
status, lease_owner, lease_token, lease_until,
attempt_count, next_attempt_at, last_error_class,
unknown_reason, confirmed_receipt_id, updated_at
```

`projection_attempts` 保存：

```text
attempt_id, outbox_id, operation_key, attempt_no,
started_at, call_started_at, call_finished_at,
result_status, error_class, error_detail_redacted,
observed_locator, lease_owner, lease_token
```

并具有：

```text
UNIQUE(outbox_id, attempt_no)
```

`projection_receipts` 保存：

```text
receipt_id, operation_key, action,
backend, generation, memory_id, revision,
backend_locator, payload_digest, confirmed_at
```

`operation_key` 使用唯一约束。重复确认返回既有 receipt，不能插入第二条 receipt。attempt 保留恢复轨迹；receipt 只记录已经核验的结果。错误详情只保存分类与脱敏摘要。

### 11.4 Projector lease fencing

领取在短事务中把到期的 `pending/retry` 条件更新为 `running`，生成全新 `lease_token` 和 attempt。所有完成、转 `unknown/retry/obsolete/blocked` 的写入都必须匹配：

```text
outbox_id + lease_owner + lease_token + status=running
```

旧 worker 在 lease 过期后被 fenced，不能修改 outbox、attempt 或 receipt。即使它已获得 locator，也只能由新 worker 通过 unknown 核验重新发现并登记。

配置必须满足：

```text
lease_duration > backend_timeout + local_completion_margin
```

S2 每次领取一个任务，后端调用期间不续租。lease 过期且 attempt 没有 `call_started_at` 时转 `retry`；已经开始调用则转 `unknown`。unknown 核验使用独立 `verification_timeout` 和错误分类，有限退避后转 `blocked`，不能无限停在 running。

自动终态中 `confirmed/obsolete` 不允许 reset。`blocked` 只允许显式 reset，清除阻塞原因后转 `retry` 并创建新 attempt；不得复用旧 attempt。

### 11.5 调用边界与 unknown 核验

执行顺序为：

```text
领取短事务
  -> 读取指定 Ledger revision
  -> 调用前复核当前 head
  -> 生成规范 Projection
  -> 事务外调用 BackendAdapter
  -> 调用后再次复核 head
  -> 完成短事务写 receipt + attempt + outbox 状态
```

调用后发现 head 已变化时，保留 locator 作为清理线索，原 outbox 进入 `obsolete`，并按能力显式创建 cleanup action；不得把新 head 标成已投影。

unknown 恢复依次执行：

1. 按 `operation_key` 精确定位；
2. 不支持时按完整 `projection_identity` 定位；
3. 找到且 identity、generation、digest 匹配时登记 receipt，再按当前 head 进入 `confirmed/obsolete`；
4. 找到但内容冲突时进入 `blocked`；
5. 明确未找到且后端声明查询完整可靠时进入 `retry`；
6. 后端不可用或不能证明未找到时保持 `unknown`，有限退避后进入 `blocked`。

## 12. 独立生命周期的故障后端

测试后端拆为：

- `InMemoryBackendState`：模拟独立远端，保存 projection、operation key、locator 和调用计数；客户端重启时继续存在。
- `InMemoryBackend`：可重建客户端包装器，注入写前失败、写后超时、返回后崩溃和核验超时等故障。

“重建客户端但保留 BackendState”模拟远端已生效而客户端失去确认。“客户端和 BackendState 同时清空”表示后端数据丢失，不得被描述为未知确认恢复；只有能力契约能可靠证明未找到时才允许重试，否则进入 `blocked`。

## 13. 内部接口与生产边界

S2 不新增生产 HTTP 接口，也不改变 S1 CLI 契约。内部接口为：

```text
EventJournal.append(event, cursor_update=None) -> EventReceipt
BatchService.request_checkpoint(...) -> BatchId
BatchWorker.run_once(judge) -> BatchRunResult
Ledger.commit_batch(plan, lease_token) -> BatchCommitResult
ReviewService.resolve(conflict_id, decision, receipt) -> ReviewResult
Projector.drain_once(backend, worker_id) -> ProjectionRunResult
```

这些接口只由测试和显式本地 Python 调用驱动。对外 URL、CLI 参数、后台 worker 和宿主映射推迟到 S3，在真实能力探针通过后冻结。

## 14. 验收矩阵

| 编号 | 必须证明 |
|---|---|
| J1 | event 与 cursor 同事务；提交失败不推进 cursor |
| J2 | partial line 不推进；坏行 quarantine 重放幂等 |
| J3 | alias 单映射，禁止跨 owner/session/generation |
| J4 | hook/transcript alias 不增加第二份独立 evidence |
| B1 | batch 冻结 event、candidate 和 anchor revision |
| B2 | batch 运行中新 candidate 不被旧 batch 结算 |
| B3 | batch/candidate 双 token fencing 拒绝旧 worker |
| B4 | Judge 失败不伪装为 `no_change` |
| B5 | 合法 `no_change` 一次结算，不无限重试 |
| R1 | proposal 持久化后崩溃，恢复不再次调用 Judge |
| R2 | input digest 或 anchor 变化使旧 proposal 失效 |
| R3 | 未知 target 的非 `new` 操作不自动创建记忆 |
| R4 | awaiting review 释放 lease，但 candidate 不被重领 |
| A1 | revision、evidence、outbox、candidate 和 batch 原子提交 |
| A2 | CAS 冲突不产生部分 revision |
| C1 | checkpoint 与 task/evidence/outbox 一起提交或回滚 |
| P1 | Ledger 提交后、projector 领取前崩溃，重启仍可消费 |
| P2 | 后端写入后失去确认，重建客户端 locate 且不重复写 |
| P3 | 新 revision 已投影后旧任务迟到，旧 revision 只能 obsolete |
| P4 | projector lease token 阻止旧 worker 迟到回写 |
| P5 | unknown 核验超时有限重试，最终状态可见为 blocked |
| P6 | operation key、attempt 和 receipt 唯一约束有效 |
| S1 | 原 53 项测试继续通过，`/events` 仍为无副作用 501 |

三组最低 G2 时序必须单独具名：

1. Ledger 已提交、projector 尚未执行即崩溃，重启后仍消费一次。
2. 后端已写入、receipt 尚未持久化即崩溃或超时，新客户端共享原 BackendState，恢复先 locate 且不重复 materialize。
3. revision 2 已投影后 revision 1 worker 迟到，revision 1 标记 obsolete，不能重新成为有效投影。

测试报告需列出 J1-J4、B1-B5、R1-R4、A1-A2、C1、P1-P6 和 S1 回归的具名测试，不能只报告测试总数。

## 15. 实施拆分

1. **Schema v2 与领域模型。** 先写迁移失败、S1 数据保留、旧 `state.db` 不变和不可执行 Plan 的测试，再实现原子 migration。
2. **EventJournal 与固定 batch。** 完成 J1-J4、B1-B3，不连接 Judge。
3. **Proposal、review 与原子 commit_batch。** 使用 `ScriptedJudge` 完成 B4-B5、R1-R4、A1-A2、C1。
4. **Projector 与故障后端。** 引入共享 `InMemoryBackendState`、客户端故障点和 lease fencing，完成 P1-P6。
5. **纵向验收与文档。** 复跑 S1，执行完整 S2 矩阵，记录故障轨迹和仍属 S3 的边界。

每个阶段先写失败测试，再实现满足当前验收的最小代码。不得并行重构旧运行时，不得借 S2 启用真实后端、宿主事件或私人数据。

## 16. S2 退出条件

只有满足以下条件，才能宣称 S2 完成：

- J1-J4、B1-B5、R1-R4、A1-A2、C1、P1-P6 全部通过；
- 三组最低 G2 故障时序有可重复的确定性测试和脱敏 trace；
- S1 固定测试、编译、依赖检查和 `git diff --check` 继续通过；
- daemon `/events` 仍不接收宿主数据；
- 没有真实后端调用、自动 worker、hooks 配置修改或私人 transcript 导入；
- 设计建议、测试能力和真实生产能力在文档中保持明确区分。

S2 通过只证明本地状态机、原子性和故障恢复契约成立，不代表 OpenViking、真实宿主注入、检索效果、远端删除或生产可靠性已经验证。这些能力继续受 S0 探针和 S3 真实纵向闭环门槛约束。
