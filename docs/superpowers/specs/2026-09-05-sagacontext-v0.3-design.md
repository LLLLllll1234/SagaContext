# SagaContext 技术实现方案 v0.3

**日期：** 2026-09-05  
**状态：** `[DESIGN]` 已批准为分阶段实现基线；不是已实现结果，不据此宣布后端或宿主兼容性已验证。  
**实现基线：** 本次读取的仓库 HEAD `e69705f`；不以提交标题替代功能验收。  
**范围：** 核心架构、数据模型、持续维护、接口、现有代码改造、验证与发布门槛。  
**前置材料：** 文档 08 任务书、09 调研报告、10 审查意见。本方案吸收 10 的纠错，不继承 09 中未证实的结论。

## 0. 执行结论与阅读导航

**定位：SagaContext 是面向个人开发者的记忆策略层，而不是某个记忆产品的专属插件，也不是新建一套向量数据库。**

推荐采用：**本地权威账本 + 可替换检索投影 + 薄宿主适配器 + 证据驱动的持续对账。**

- 我们拥有：项目/任务身份、作用域、记忆内容及修订、证据、冲突与遗忘、召回策略、评测。
- 后端提供：内容的检索投影、索引与搜索；不能决定我们的事实版本、用户权限和生命周期。
- 宿主提供：经过能力探测的事件与注入位置；不要求所有宿主有相同 hooks。
- 模型提供：候选发现与相对旧记忆的增量建议；不能直接写库、扩大作用域、批准规则或宣布验证成功。
- 第一版：单用户、单设备、一个宿主、一个真实后端，跑通偏好、项目知识、任务接续的完整闭环。
- 第二后端先做同一套语义契约验证，不要求一开始双后端在线服务。

| 要解决的问题 | 阅读章节 |
|---|---|
| 为什么这样分层、哪些不做 | §1–3 |
| 用户、项目、任务和记忆怎么建模 | §4–5 |
| 新任务如何恢复、会话中如何继续维护 | §6–8 |
| 并发、崩溃、索引落后怎么办 | §9–10 |
| 后端与宿主怎么接、怎么降级 | §11–12 |
| 旧代码和旧数据如何迁移 | §13–14 |
| 个性化收益如何验证、何时能上线 | §15–18 |

本文除“当前代码观察”和明确指向审查证据的内容外，均为设计建议。所有起始参数标为设计默认值；性能与收益在固定环境实测前保持 `[VERIFY]`，不写虚构百分比或工期承诺。

## 1. 目标、边界与首版验收故事

### 1.1 三个产品闭环

1. **上下文恢复：** 用户开始任务时得到与自己、当前项目及目标相关的知识，而不是全部历史摘要。
2. **项目知识维护：** 工作中新证据能关联到已有决策、模块地图或故障经验，形成可追溯的补全与取代。
3. **个性化适配：** 当前用户的显式偏好、项目约定和任务阶段影响选取与呈现；后续再验证学习参数是否比静态规则更好。

项目事实不因个人偏好而改变。个性化的是知识选择、适用视图和工作方式，不是“为用户定制事实真相”。

### 1.2 第一版验收故事

在一个任务中验证并修复测试挂起问题，形成包含适用环境、症状、验证步骤的项目记忆。新会话开始另一项相关任务时，按文件与目标召回该经验；随后新的针对性验证说明旧方案过时，系统产生新修订。再开会话只把有效修订作为当前事实使用，历史方案仅在明确询问历史时出现。

同时验证：项目 A 的约定不进入 B；同分支的两个任务不串进度；崩溃重放不丢记忆、不重复加证据。

### 1.3 范围限制

- 单设备单用户，一份受本机用户权限保护的账本；不支持多台机器各自写同一份同步数据库。
- 一个 daemon 管理所有写入，CLI 通过服务请求变更，不直接并行修改数据库。
- 第一真实后端候选是 OpenViking；是否启用取决于 G1。Mem0 为第二适配候选，不能预设其满足所有接口。
- 第一宿主选择本机能通过 G3 的一个明确形态和版本，不以产品名预设 hooks 能力。两个候选都未通过时，只做事件回放与显式导入，不能宣称自动接入完成。
- 团队共享、在线强化学习、自动执行记忆内命令、多设备漫游、复杂图检索不进入首版。
- 已有 Compliance 实现保留源码，但新链路默认关闭记忆生成的强阻断和命令执行；通过独立权限设计后才能启用。

## 2. 关键架构决策与备选

| 决策 | 推荐 | 备选与不用的原因 |
|---|---|---|
| D01 权威状态 | 本地 SQLite 事务账本 | 后端权威需依赖后端条件写入/修订语义；当前证据不足，不作公共前提 |
| D02 检索职责 | 外部后端为可重建投影 | 自建完整检索引擎扩大范围；本地只做精确目录/主题查询和测试替身 |
| D03 更新时机 | 每轮持久化证据，阶段性语义对账 | 每轮完整 LLM 对账成本待验证；仅退出时处理有证据丢失风险 |
| D04 并发 | 单 daemon、短本地事务、异步投影 | 不用远端普通 write 模拟 CAS；也不引入分布式事务 |
| D05 个性化 | 可解释静态策略 + 显式个人配置 | 在线学习延后，用时间切分实验判断是否有增益 |
| D06 生命周期 | 稳定逻辑 ID + 单调修订号 + 读取复核 | 不以日期后缀文件名或后端 URI 定义事实身份 |
| D07 迁移 | 新账本旁路建设、显式导入、受控切换 | 不把旧后端中全部内容自动认作已验证事实 |

**成本边界：** 本地权威账本意味着我们承担备份、修订、删除与恢复职责；这不是“免费缓存”。但无需实现向量算法、通用云存储、分布式共识或完整多租户平台。

## 3. 模块与不可破坏的约束

### 3.1 模块职责

沿用仓库已有的 Python 3.11+、FastAPI、Pydantic、SQLite 和异步 HTTP 调用方向；本方案不要求新增 Redis、Kafka 或独立工作流服务。依赖的准确版本锁定与安装验证在实施时完成，不把当前宽版本声明视为可复现环境。

| 模块 | 输入 → 输出 | 不应承担 |
|---|---|---|
| HostAdapter | 原始事件/transcript → HostEvent；ContextBundle → 宿主响应 | 决定全局偏好、长期事实、跨项目授权 |
| IdentityResolver | 本机项目记录、位置、会话目标 → TaskContext | 把相同 ancestry/branch 当成自动共享授权 |
| EventJournal | 标准事件 → 持久 receipt、证据定位 | 对模型推断作真实性担保 |
| MemoryLedger | 校验后的提交 → 头版本、修订、证据、outbox | 调用远端模型或检索服务 |
| RecallPolicy | TaskContext + 权威候选 → ContextBundle | 信任后端返回的正文、状态与权限 |
| Reconciler | 固定批次证据 + 旧记忆 → DeltaProposal | 直接写远端、自动赋予执行权限 |
| Projector | outbox → 后端投影与同步状态 | 改写权威事实、推进对账游标 |
| BackendAdapter | 中立投影/查询 → 外部 API | 向核心暴露 URI 或要求后端原生 CAS |
| Evaluator | 固定轨迹与真实输出 → 指标及证据 | 将手填 observation 当真实 Agent 结果 |

### 3.2 不变量（后续测试直接引用）

- **I01：** 每条有效记忆只从本地账本的当前头版本读取；后端搜索结果仅用于定位候选。
- **I02：** 用户、项目、路径、任务范围必须在候选读取、增量提交、最终注入时再次校验。
- **I03：** 一次修订、证据关联、批次处理状态及 outbox 在同一本地事务中提交或一起回滚。
- **I04：** 远端模型、embedding、搜索与写入不得发生在持有数据库写事务期间。
- **I05：** 输入接收、语义处理、投影同步是三个独立进度；后者失败不能让前者丢失原始证据。
- **I06：** 同一来源证据重复送达只产生一次有效关联；“曾经召回/被 Agent 复述”不算新的独立确认。
- **I07：** 本地 CAS 冲突后重新基于最新头版本判定，不只提升版本号后覆盖旧字段。
- **I08：** 索引滞后允许少召回，但不允许把被删除、取代、未授权的内容作为当前事实注入。
- **I09：** 当前任务例外不自动推广成项目或全局规则；扩大范围需要明确的用户意图。
- **I10：** 检索置信度、使用收益、事实证据强度、执行权限是独立维度。
- **I11：** 业务退役与用户删除分别处理；删除后的源事件重放不能自动复活知识。
- **I12：** 故障状态可观测；未配置模型、写入失败和结果为空不能全部伪装成“成功”。

## 4. 用户、项目与任务身份

### 4.1 单用户并不等于没有 owner

初始化生成稳定 `owner_id`，由可信本地配置绑定。所有实体携带该 ID，服务端不信任普通请求任意指定 owner。`global` 仅表示该用户跨项目，不表示所有用户共享。

首版不做账号系统；若未来多用户，需要额外认证与权限模型，不能只开放现有监听地址。

### 4.2 项目身份为显式记录，不是一个 Git 哈希

`project_id` 为随机稳定 UUID；`ProjectLocation` 映射本机位置和项目关系。

1. 优先使用已有位置绑定或用户显式项目绑定。
2. 同一 Git common directory 的 worktree 可以按配置归属同一项目；仍保留独立 `workspace_id` 和环境指纹。
3. 不同 clone、fork 或不同路径只生成“可能关联”的建议；首版由用户确认共享。root commit、remote 和路径只作匹配信号，remote 需脱敏，不进入公共记忆正文。
4. monorepo 默认一个项目。只有显式指定的子项目根才建立独立项目，不因 cwd 从根进入 `src/` 就换身份；父级约定的继承关系需显式配置且默认只读继承。
5. 非 Git 项目也分配 UUID。移动目录需执行显式 rebind；默认不擅自写入用户仓库标记文件。
6. branch/commit、依赖版本与文件哈希用于判断任务和记忆适用环境，不构成独立授权依据。

路径规则以绑定项目根为基准，规范化相对路径、大小写策略及符号链接目标；无法证明路径位于允许根目录内时，不适用路径记忆。不能用字符串前缀测试代替路径归属。

### 4.3 任务生命周期

`task_id` 为 UUID，不由 `repo:branch:goal` 文本哈希生成。一个任务可关联多会话；同会话允许产生多个有边界的任务片段。

- SessionStart：只恢复项目骨架、全局显式偏好和少量待接续提示；没有首条目标时，不自动认领最近任务。
- 明确给出 task_id 或用户说“继续某任务”：绑定该任务。
- 第一条目标：结合目标、已触及对象、已有活跃绑定提出接续候选。首版有歧义则创建新任务或请求确认，不使用未经校准的固定相似度阈值自动合并。
- 切换任务：持久化当前任务检查点，解除当前会话绑定，再建立新绑定；不自动暂停其他会话仍在执行的旧任务。
- 任务状态：`active / paused / completed / abandoned`。长期无活动可标“可能闲置”，不能据此认定 completed；分支合并也不是所有任务完成的充分证据。
- 任务完成后，单独运行知识晋升，只有符合各类型门槛的内容进入项目记忆。任务 todo 不逐项复制成长久事实。

## 5. 核心数据模型与本地表

### 5.1 规范记忆模型

下表是字段契约，不是已发布 SDK。字段枚举将在 S1 实现为强类型校验。

| 实体 | 必要字段 | 语义 |
|---|---|---|
| MemoryHead | owner_id、memory_id、current_revision、type、scope、state、conflict_state | 当前有效状态及定位；不携带后端 URI |
| MemoryRevision | memory_id、revision、operation、payload_schema_version、payload、created_at、valid_from、expires_at、applicability、source_kind | 修订快照；除用户删除/脱敏外不原地改正文 |
| Scope | kind、project_id?、path_pattern?、task_id? | kind 为 `global / project / path / task`；关联字段由 kind 决定 |
| Evidence | evidence_id、source_event_id、claim_key、evidence_kind、locator、observed_at、verification?、redacted_excerpt? | 原始事件、被支持的命题及验证，不能仅存一个成功布尔值 |
| Verification | verifier_kind、claim_key、input_fingerprint、environment_fingerprint、expected、observed、outcome | outcome 为 pass/fail/inconclusive；exit code 只是 observed 的一项 |
| DeltaProposal | proposal_id、batch_id、operation、target_id?、expected_revision?、scope、payload_patch、evidence_ids、rationale | 模型建议；提交前确定性验证 |
| Conflict | conflict_id、target_id、base_revision、proposal_id、reason、resolution、resolved_by? | 冲突提案与审批结果；不直接覆盖旧事实 |
| BackendHit | memory_id、revision、generation、rank、score?、backend_locator | 非权威候选定位；score 不跨后端直接比较 |
| ContextBundle | bundle_id、policy_version、items、budget_usage、omission_reasons、ledger_sequence | 项目/任务范围内的注入快照及解释 |

作用域约束：项目知识必须有 project_id；task checkpoint 必须有 task_id；path 必须同时有 project_id；全局 scope 禁带路径或 task_id。一个项目记忆的“证据来源任务”与“仅适用某任务”是不同字段。

不同类型、不同 owner、不同 scope 的相似内容不得仅凭语义相似自动合并。

### 5.2 首版类型与晋升门槛

| 类型 | 内容 | 进入长期状态的最低要求 |
|---|---|---|
| profile / taste | 明确画像、解释偏好、技术偏好 | 用户明确声明；推断先留候选，不从少量行为确定敏感画像 |
| convention | 应/不应如何工作的约定 | 明确用户意图和适用范围；区分单次例外与持久规则 |
| decision | 决策、理由、影响、采用状态 | 已决定与已落地分开；用户确认决策不能自动证明代码已实现 |
| project_map | 模块责任、路径、入口、依赖 | 文件/符号定位与匹配版本；推断的职责标推断状态 |
| gotcha | 症状、触发条件、原因、修复或规避 | 区分 observed_issue 与 verified_fix；后者需要针对命题的验证 |
| task_checkpoint | goal、done、open、next、touched_paths、outcome | 与任务和会话关联；done 项不能只凭 Agent 自述 |

团队与 Agent 经验视图预留，不增加首版可自动写入类型。公共类型 schema 中不出现 OpenViking YAML 模板、Mem0 payload 特殊字段或宿主专用 URI。

### 5.3 修订语义

- 同一命题/适用范围的 refine 或 supersede：保留 memory_id，追加 revision；前一 revision 只可作为历史读取。
- 创建新命题、改变类型或范围：创建新 memory_id；如确实取代旧条目，记录明确关联 `(old_id, old_revision) → (new_id, new_revision)` 并在同一事务更新两条头状态。
- head state：`active / retired / deleted`；`conflict_state=unresolved` 独立表示争议。争议内容不作为无条件事实注入，可单独给出待确认提示。
- 有效期与环境适用性独立于 state。代码变化触发“需要复核”，不是所有旧 commit 证据都自动作废。
- 历史 revision 不放进普通搜索注入候选。历史查询须显式请求、带过时标签并经过同样的权限/删除检查。
- confirm 增加一条独立证据关联；不按每次调用机械增加计数。首版不把计数直接换算成统计置信概率。

### 5.4 SQLite 表与事务边界

采用版本化迁移、新账本文件和受控导入。下列是逻辑表分组，实际迁移脚本在 S1/S2 落地；不另引入消息队列或分布式锁。

| 表/表组 | 关键约束 | 作用 |
|---|---|---|
| projects、project_locations | location 唯一归属；owner/project 外键 | 稳定身份与本机位置 |
| tasks、sessions、task_bindings | binding 指定事件起止边界 | 同分支并行任务、会话内切换 |
| events、source_cursors | UNIQUE(owner, host, session, source_event_key) | 持久接收、源文件代际与字节进度 |
| candidates、proposals、batches | 每批固定候选 ID/事件上界；提案 receipt、状态与 lease | 对账去重、失败重试、批次隔离 |
| memories、revisions | PK(memory_id, revision)；头版本条件更新 | 权威事实与修订 |
| evidence、revision_evidence | UNIQUE(memory_id, revision, evidence_id, claim_key)；独立确认按来源事件/命题去重计算 | 来源与验证；不同修订可沿袭同一证据，但不增加独立确认数 |
| conflicts | 未决提案带 base_revision | 审批和 CAS 冲突 |
| outbox、projection_receipts、backend_generations | UNIQUE(backend, generation, memory_id, revision, action) | 可靠同步、远端 ID 映射、切换 |
| deletion_jobs、suppression_rules | 删除请求与再导入范围独立记录 | 删除进度、防复活 |
| traces、schema_migrations | trace 默认不存原始正文 | 可解释性与迁移记录 |

每次连接启用外键约束，所有 DB 写入通过单一 writer 执行。WAL、同步级别、busy_timeout、连接生命周期与断电恢复要在 G2 测定并记录；不能只配置 WAL 就宣称不会丢数据。事务确认后才返回事件 accepted。

候选状态独立为 `pending / processing / awaiting_review / settled / quarantined / retry`；缺失来源或类型不合法的 legacy 项留在候选层，不为了导入方便创建 active MemoryHead。独立确认数对 `(memory_id, source_event_id, claim_key)` 取唯一集合，不能直接统计跨修订关联行数。

## 6. 事件接收、证据与进度管理

### 6.1 中立事件契约

事件包含：`event_id / schema_version / host / host_version / session_id / workspace_id / event_kind / occurred_at / received_at / source_locator / payload / trust_class`。

业务 event_kind 为：`session_opened / user_message / tool_started / tool_finished / checkpoint_requested / compaction_observed / session_closed`。任务切换是策略层根据证据产生的领域事件，不假设宿主存在 TaskStart hook。

- 优先使用宿主稳定原生事件/工具调用 ID。
- transcript 无原生 ID 时用已登记的 session、source_generation、字节范围及子事件序号标识。不能只用文本哈希，否则不同轮次相同指令会被误去重。
- 文件截断、替换、压缩需建立新的 source_generation；持续追加保持代际不变。文件移动关联与代际识别必须有 fixture。
- hook 与 transcript 可能重复描述同一消息。使用原生 ID 或持久别名关联；无法可靠关联时 hook 内容仅作即时提示/待核对证据，不算第二次独立确认。
- parser_version 不属于事件身份；重新解析只能更新解析状态，不能创造第二条事实证据。

### 6.2 三种进度绝不能混用

1. **capture_cursor：** 原始片段及解析 receipt 已经持久化到 EventJournal 的位置。未完成行不推进；完整坏行需记录字节定位和 quarantine，不能静默消失。
2. **reconcile batch：** 固定事件集合、候选集合以及每项处理状态。模型失败、审核等待、no_change 分别记录；不以全会话 consumed 布尔值清空新到候选。
3. **projection progress：** 某 backend/generation 已确认投影的 revision，与上述两个进度独立。

capture_cursor 与对应接收记录同事务提交。对账从持久 journal 读取，而非再次从捕获游标后取 transcript，从根本上避免“看过但没保存给判定模型”的证据丢失。

### 6.3 同步与异步分工

- 同步：认证、schema 校验、项目定位、事件持久化、读取本地必要上下文，返回宿主允许的响应。
- 异步：工具结果关联、语义候选发现、对账、索引投影和清理。
- 关键后台任务必须先进入持久队列，再用 asyncio 唤醒 worker；不能仅靠 `create_task()` 保存待完成工作。
- 数据库不可用时不返回 accepted；宿主可以继续正常任务，但记忆服务需显式标 degraded。能否重试/补采取决于已验证的宿主与 transcript 能力，不保证所有未送达事件可恢复。

首版限制模型任务并发、单批事件/字节/输入 token、源读取根目录、重试次数和磁盘占用；数值在合成轨迹测量后配置。重试采用带上限的退避；达到上限转 blocked 并保留证据，不无限消耗模型预算。磁盘不足时停止接收并报告，不能一边返回 accepted 一边淘汰未处理事件。

## 7. 读路径：每个任务拿到自己的有效记忆

### 7.1 TaskContext

由可信 owner、project_id、workspace_id、task_id、目标、当前触及路径、环境指纹、任务阶段、宿主能力与预算构成。任务阶段首版仅用可解释状态 `orient / investigate / implement / verify`；不靠无证据的复杂意图分类。

### 7.2 召回流程

1. **构建允许范围：** 当前用户全局、项目、匹配路径、明确绑定的任务。无 project_id 时仅使用明确全局内容，不兜底搜索所有项目。
2. **读取本地必要集合：** 显式有效约定、当前任务 checkpoint、精确路径/模块相关记忆；避免 SessionStart 空 query 完全依赖向量搜索。
3. **向后端查候选：** 目标和当前路径可生成有界查询；后端范围过滤是效率与第一层隔离，不能替代账本校验。
4. **按 ID 回查权威账本：** 拒绝未知 ID、非当前 generation、revision 不匹配、已删除/退役、未授权、过期或不适用项。丢弃旧修订命中后，本地精确集合可补回同主题当前版本；不直接注入旧正文。
5. **去重与冲突处理：** memory_id 去重；同主题冲突不选择“分最高的即真相”，单独生成待确认提示。
6. **按策略装配：** 当前任务事实优先、明确约定保留槽位、路径经验及项目背景按相关性排序。超大候选不导致循环提前停止，继续尝试装入较小条目。
7. **最终复核：** 渲染前再次检查 head revision 与 ledger_sequence；发生变更则重读该项。返回后已进入宿主上下文的文本无法撤回，下一支持的事件发送取代提示，不承诺立即清除旧上下文。
8. **记录 trace：** 候选来源、过滤理由、适用范围、revision、预算、排序原因与注入结果；不因“被注入”增加事实证据。

### 7.3 首版个性化策略

个性化不等于在线学习：先用显式个人偏好、项目差异和任务阶段实现可解释适配。

- 用户设置：解释风格、背景详略、明确全局约定、项目例外。推断性个人属性默认不进入全局强规则。
- 同一条记忆：在相关文件的调查/修改阶段优先，在无关任务不注入；任务进度只对对应任务出现。
- 排序采用分层优先级与后端内部 rank，不固定跨引擎 `min_score=0.5`，不把不同分数空间相乘。
- 时间只影响相关性/复核需求；显式约定直到取代、删除或指定有效期，不因为“很久没用”自动成为错误。
- evidence_strength、usage_feedback、rank 分别保存。未引用不默认降权；显式用户纠正与经验证任务结果分别记信号。

**设计起始预算：** 沿用已有 2000 token 会话预算、600 token prompt 预算作为可调起点，不作为收益或性能承诺。任务块、约定、背景、信封和冲突提示全部计入同一预算；有条件时使用对应 tokenizer，否则使用标注为估算的保守上界。规则过多装不下时说明省略，不暗示所有约定已覆盖。

## 8. 写路径：以已有知识为锚持续对账

### 8.1 候选发现与触发

| 信号 | 同步工作 | 异步处理与风险 |
|---|---|---|
| 明确纠正/长期约定 | 持久化原文事件与范围线索 | 优先生成候选；“这次”默认为任务例外 |
| 工具失败 → 修改 → 针对性验证 | 记录关联调用与文件快照 | 形成 gotcha 候选；没有修复仍可保留带条件的 observed_issue |
| 模块探索及证据更新 | 记录文件/符号定位 | 阶段边界发现 project_map 候选；无需用户说“我们决定” |
| 任务阶段切换/用户显式要求记住 | 排入 checkpoint 请求 | 有界语义候选发现 + 对账 |
| 普通对话积累 | 只追加 journal | 达可配置事件/时间阈值才做有界扫描，不每轮完整 LLM 调用 |
| 压缩/结束/恢复扫描 | 记录事件或恢复任务 | 作为补充触发，不能成为唯一持久化机会 |

L0 正则是候选发现的快捷路径，不是模型对账的唯一准入条件。没有规则候选但有新工具证据/模块探索时，允许针对固定事件切片做低频语义发现。

各触发器只创建批次请求；相同 source 范围合并，按 session/task lease 避免重复对账。时间触发由 daemon 内部实现，本方案不创建任何应用级自动化任务。

### 8.2 对账步骤

1. 短事务领取 batch，冻结候选 ID、事件集合及输入上界；后续新事件不属于该批次。
2. 从 journal 读取证据，保留事件顺序、来源类型、claim/验证关系。压缩使用可定位摘录；全文证据在本地按需取，不将截断摘要当作完整证据。
3. 在允许范围内取“本轮注入过的 + 同主题/文件/实体的权威记录 + 有界后端补召回”的锚点，全部附 memory_id/revision；按类型覆盖而非 URI 字母序截断。
4. 模型输出结构化 DeltaProposal，operation 为 `new / confirm / refine / supersede / conflict / no_change`。LLM API 失败不转成空数组成功，返回可分类失败状态。
5. 确定性检查：schema、证据 ID 存在且可访问、范围不越权、目标类型匹配、删除抑制规则、预期 revision、各类型晋升条件。
6. new 提案查本地同类型同范围主题及候选相似项，发现可能重复则复核；不能仅由相似度阈值强行覆盖。未知 target_id 的非 new 操作拒绝/重判，不能自动降级成 new。
7. 合格提案组成原子提交计划；需用户确认的保存在 conflicts/candidates，不伪造已生效记忆。
8. commit 成功后更新该批次每个候选的结果。合法 no_change 也可结算；待确认有持久引用后才离开待处理集合；失败项保留重试。

提案在完成模型响应解析后持久化 proposal_id、输入指纹与 base_revision，再交给提交器；恢复时优先继续处理既有提案。需要因 CAS 冲突重判时生成有前后关联的新提案，不能把不同模型响应当作同一已提交操作重复执行。

### 8.3 事实验证与范围变化

- `exit_code=0` 是工具观察，不是任意 claim 的充分证明。verified_fix 必须保存针对该故障的验证器及环境；构建通过和运行健康分别判断。
- 用户明确说“我们决定采用 X”可证明决策意图，不能证明代码已经迁移完成。
- 当前 Prompt 可以覆盖当次软偏好，但不能绕过宿主安全限制，也不能自动修改长期项目事实。
- 模型推断的 scope 越大越需谨慎；缺少明确范围时默认当前任务候选，不写全局。
- 不按固定“出现两次就永久可信”规则提升事实；独立证据按 source/claim 去重，验证方法是否独立另行记录。

## 9. 本地原子提交、并发与检索投影

### 9.1 账本提交协议

外部调用结束后才开启短写事务：

1. 检查 batch/proposal 未结算，引用 evidence 仍存在且未被删除。
2. 检查所有被改写 head 的 expected_revision、owner、scope 与删除版本。任何冲突则整个关联提交回滚。
3. 插入新 revision 和证据关联；对 head 执行带旧 revision 条件的更新。新逻辑记忆用稳定 proposal receipt 防重复创建。
4. 同事务更新任务检查点/关联冲突、候选状态和 batch 结果。
5. 向已登记的 backend generation 写入 outbox。只保存 memory_id/revision/action，不复制可能以后要求删除的正文。
6. 提交事务后返回 `committed_pending_projection` 或 `committed_local_only`，分别表示已启用后端待同步或尚无可用后端；不能把它们写成已可搜索。

CAS 冲突时重新读取最新头与证据。纯确定性集合合并仅限 schema 明确允许的字段；其余重新判定，有限重试后转待处理，不无限覆盖。

### 9.2 Projection DTO 与版本化物化

投影仅包含白名单字段：`owner_id / memory_id / revision / generation / type / searchable_text / scope_filter_tags / payload_digest`。完整证据、原始 transcript、审批记录不外发。

首版优先使用按 `(generation, memory_id, revision)` 隔离的投影对象，而不是复用一个远端对象反复无条件覆盖。适配器可自行生成外部 locator；核心稳定 ID 与远端 UUID 分离。

- worker 按当前账本重新生成投影；outbox 里的旧修订若已失效，跳过写入并安排旧投影清理。
- 执行前、返回后复核删除与头版本；有变化则将刚写入对象加入清理任务，不把它登记为当前可用。
- 后端调用超时可能“已写成但没返回”。按 generation + memory_id + revision 发现已有对象再重试；若做不到可靠发现，允许可观测的重复投影并按稳定 ID 去重，但该适配器不得宣称 exactly-once，也不能通过远端删除完成门槛。
- 旧请求晚到只能生成旧修订对象；读取时 revision guard 拒绝它。旧投影需后台清理以避免挤占 Top-K；有限过召回不能保证补齐所有召回率损失。
- 同步进度分为 `pending / running / ready / retry / blocked / obsolete`，并区分请求 accepted 与真实 searchable；后端无法确认可见性时标 unknown，不提前写 ready。

### 9.3 崩溃与重放

| 中断位置 | 恢复行为 | 不得发生 |
|---|---|---|
| journal 提交前 | 无 accepted；由已验证的源补采/发送端重试 | 先推进 cursor 再丢正文 |
| 模型调用中 | lease 到期后重试固定批次；保存可用的提案 receipt | 清空整个 session 候选 |
| ledger commit 前 | 回滚全部相关修订与 outbox | 旧头失效、新头未创建 |
| ledger commit 后、远端写前 | outbox 重放 | 要求重新问模型才能恢复已确认修订 |
| 远端写成但响应丢失 | 查重/登记重复风险，读取按当前 revision 复核 | 盲目认定未写入或 exactly-once |
| 删除与远端写并发 | 本地先禁止读取，处理所有在途对象及清理确认 | 将软状态变化当作已远端擦除 |

## 10. 冲突审批、退役、删除与权限

### 10.1 冲突审批必须真正提交

审批请求携带 conflict_id、决策与请求 receipt。服务读取 base_revision 和最新 head；过期提案需重审。`accept_new` 创建修订并在同事务完成旧状态变更、冲突解决和 outbox；`accept_old` 保留当前头、记录拒绝理由；单次例外只写 task scope。

审计记录 resolved 不代表记忆已更新，只有 ledger commit 结果才能确认生效。

### 10.2 删除协议

1. 首先同事务将目标 head 标 deleted、阻断普通与历史注入、创建 suppression 和 deletion_job，递增删除状态版本。
2. 本地清理 revision 正文、证据摘录、派生候选、缓存与 trace 中的内容。共享源事件需精确脱敏；必要时清除整个片段并将依赖它的其他记忆标为证据不足。
3. worker 清理该记忆所有 generation 的已知及可发现投影，等待在途请求收敛并复核。未知超时写入无法确认清理时保持 `remote_pending`，不能返回“全部删除”。
4. suppression 保存最少的非正文信息：来源事件标识/受控摘要指纹、被禁止再导入的 scope/topic 和请求记录。对新的改写措辞不保证自动语义识别；命中主题歧义时暂停导入并询问，而非自动恢复。
5. 用户持有的原始 transcript、Git 历史及独立备份不由服务擅自改写。说明这些副本的删除边界；从旧备份恢复必须先应用最新删除清单或保持隔离，清单丢失不得自动导入上线。
6. `restore/relearn` 是新的显式授权操作，不是重放触发的自动取消 suppression。业务 retired 则仅停止普通注入，可保留审计历史，与 forget 分开。

对外状态至少区分 `blocked_from_read / local_redacted / remote_pending / completed / needs_action`。底层未提供足够清理能力的适配器，不满足完整 forget 的上线门槛。

### 10.3 安全边界

- 优先本机 socket；若用 loopback HTTP，使用本机凭据认证、限制请求大小和来源，不仅依赖绑定地址。凭据不写入 trace 或记忆。
- transcript 与项目文件读取限制在显式许可根目录；事件 payload 提供的路径不能直接绕过范围校验。
- 内容在入 journal、入模型、入投影前各做必要的最小化/脱敏；模式匹配无法保证检出全部秘密，敏感目录和来源默认排除。
- 记忆正文永远是数据。格式转义不等于 prompt injection 防护；工具授权继续由宿主和独立执行策略控制。
- 不根据记忆自动运行 lint/bash/脚本。未来 command 规则需用户批准的 allowlist、参数化执行与受限环境，另立设计。
- 本地文件权限和磁盘加密责任明确；首版不承诺应用层端到端加密或多用户隔离。

## 11. 后端中立接口与适配契约

### 11.1 将权威接口与搜索接口分开

| 接口 | 语义与结果 |
|---|---|
| Ledger.get_current(ids, context) | 返回当前有效、授权的规范记忆；唯一权威读取 |
| Ledger.commit(plan, expected_heads, receipt) | 原子提交；返回 committed / conflict / rejected 与新修订 |
| Ledger.read_history(id, context) | 显式历史读取，依然受删除与权限限制 |
| Backend.capabilities() | 声明已验证的能力、版本与限制，不靠产品名猜测 |
| Backend.search(query, filters, cursor, limit) | 返回 BackendHit 与后端分页状态，不返回可直接注入的权威实体 |
| Backend.materialize(projection, operation_key) | 返回 external locator、accepted 状态与可见性信息；幂等能力见声明 |
| Backend.locate_projection(identity) | 用于未知响应恢复和重复检测；能力不足需标记限制 |
| Backend.remove_projection(locators) | 返回清理请求/确认状态；不得等同 ledger 删除 |

可选能力：原生条件写入、批量写入、精确 metadata 过滤、全文/语义搜索、查询标签、namespace 清空与枚举、索引可见性检测。核心不要求 Dense + Lexical 同时存在，也不调用原生 CAS 才能成立。

**最小生产适配门槛：** 能把搜索结果映射回稳定 ID/revision/generation；能隔离 SagaContext 自主管理区；能枚举/定位并清除其管理投影或以等价受控 generation 清理完成删除。其他原生记忆不能被顺便清空。

### 11.2 两个候选后端的验证清单

**OpenViking `[VERIFY]`：** 按锁定版本验证原始内容写入、处理模式、真实请求/响应、标签与目录过滤、读取和删除接口、分页/枚举及索引可见性；`memfile` 编解码仅保留在适配器。审查已指出普通写接口没有原生 CAS 前提，本方案不再依赖它。绕过 Session 抽取不代表没有语义处理或成本。

**Mem0 `[VERIFY]`：** 验证指定自托管配置下 `infer=False` 的完整行为；确认内部分配 UUID 与稳定 ID metadata 映射、重复创建恢复、过滤/分页、更新和删除语义。不得把待实现的 `write_raw(id=...)` 当作已存在的公开 API，也不默认用户可提供向量而跳过其 embedding。

真实后端失败时先保留本地精确读取与记录能力；InMemory/SQLite 测试后端只验证契约，不包装成真实语义检索效果。许可问题按组件与集成方式单列确认，HTTP 不作为法律保证。

### 11.3 后端替换与原生记忆共存

- 先停止旧 generation 接受新查询切换，建立新 generation 和账本一致性快照水位；重建期间仍由旧后端服务读取。
- 快照后的账本增量继续进入新 generation outbox；追平到校验水位并通过稳定 ID/范围/删除检查后，原子切换 active generation。
- 切换只更新检索路由，账本不变。需要回退时先确认旧 generation 已追平，否则用本地降级，不让旧索引绕过版本校验。
- 后端现有记忆默认不自动托管。首版只支持用户指定集合的显式导入；只读联邦参考作为后续可选能力，不能把整个后端内容默认导入全局记忆。
- SagaContext 投影与后端原生自动写区分开，避免两套生命周期同时修改同一条记录；不能擅自修改用户原生插件配置。

## 12. 宿主能力协商与服务接口

### 12.1 HostCapabilities

保存 `host_name / host_form / executable_version / adapter_version / config_fingerprint / verified_events / injection_modes / timeout_behavior / transcript_support / probe_date`。CLI、桌面、扩展分别记录。

- G3 验证哪种事件就启用哪种事件；不能因插件只注册四项而判宿主只支持四项。
- 缺少工具事件：从经过 fixture 验证的 transcript 增量补采；补采也没有则只存用户显式信息和未验证候选，不声称能够自动沉淀 verified_fix。
- 缺少注入 hook：可提供显式工具/规则文件接入，但标为 manual/agent-driven，不保证模型每次主动调用。
- 缺少结束信号：依赖已持久化事件与可读取源做恢复。闲置超时只触发补采检查，不能自动把任务标完成。

### 12.2 服务契约（拟实现）

| 操作 | 关键行为 |
|---|---|
| 接收 events | 认证、去重、持久化 receipt；支持说明是否已附带 ContextBundle |
| recall | 输入可信 TaskContext，返回 bundle 与省略原因；不能任意搜索其他 owner |
| checkpoint | 请求创建固定批次，返回 durable job_id，不同步等待完整 LLM 对账 |
| memories/show/history | 从 Ledger 读取，不直接从后端拿权威正文 |
| conflicts/review | 幂等审批并执行 ledger commit，返回实际生效状态 |
| forget/status | 创建删除任务并查询分阶段状态 |
| doctor/trace | 检查 schema、后端能力、宿主版本、积压和降级；默认不打印密钥/正文 |

这些是领域操作而非已冻结 URL。具体 URL、hook JSON 和 CLI 参数在探针通过后固定并生成契约 fixture。避免为未核对的厂商 API 编造可运行命令。

## 13. 现有代码改造清单

以下“现状”来自本次读取；“改造”为待实施。所有路径均位于当前仓库，不自动移动未涉及模块。

| 当前文件 | 处理 | 目标 |
|---|---|---|
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/models.py:1` | 分离中立模型与后端记录 | MemoryHead/Revision、Scope、Evidence、Proposal、BackendHit；禁止 URI 成为核心 ID |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/store.py:5` | 保留 SQLite 技术，建立新账本与事务服务 | 不再由零散方法逐项 commit；迁移版本、外键、receipt、outbox |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/daemon.py:43` | 保留服务入口，抽薄路由 | 显式创建依赖、认证、durable jobs；不在导入时构造全局真实客户端 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/transcript.py:12` | 扩展解析与代际 fixture | 稳定事件、工具证据、分离游标；Edit attempt 不是执行成功证据 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/scope.py:7` | 替换 root-commit key 为项目注册关系 | 保留 Git 信号采集，不用它单独作权限身份 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/tasks.py:9` | 改造接续和绑定 | 不按分支/相同目标自动合并，不暂停其他活跃任务 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/capture.py:15` | 保留规则为快捷候选 | 增加阶段性语义发现与验证证据关联 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/anchors.py:8` | 注入 Ledger/Backend 接口 | 强范围过滤、ID/revision 锚点、同主题精确召回 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/reconcile.py:59` | 保留可测试演化思想，重写输出模型 | 不生成 viking URI；schema 化 patch、no_change、命题验证 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/runner.py:12` | 重构批次协调 | journal 输入、固定 batch、失败类型、事务提交、候选精确结算 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/writer.py:6` | 替换旧直接远端写路径 | LedgerCommit 与 Projector 分离，删除“只抬版本重试” |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/ov_client.py:5` | 收入 OpenViking 适配器 | 请求/响应按锁定版本 fixture 校验，不把当前封装当已验证契约 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/memfile.py:7` | 保留编解码 | 仅适配器或显式 legacy 导入使用 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/recall.py:6` | 重写过滤和装配顺序 | 所有层可候选，权威复核、失效过滤、同一总预算、跳过超大项 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/cli.py:38` | 审批/删除走服务事务 | 不只标记 pending 已处理；doctor 做真实能力检查 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/weights.py:8` | 暂不接自动更新 | 先记录反馈及 policy_version，不把未使用直接扣权重 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/compliance.py:1` | 保留并隔离 | 不把记忆命令默认执行；新链路默认关闭自动强制 |
| `/Users/lqy0584/Downloads/SagaContext/src/sagacontext/bench/adapters.py:9` | 保留 fixture 单测，新增真实运行适配 | 分开回放指标与端到端 Agent 指标 |
| `/Users/lqy0584/Downloads/SagaContext/tests/test_core.py:1` | 保留相关测试并重审旧断言 | “writer rebases version”不再作为正确 CAS 测试；补故障回归 |

按阶段新增 `ledger/`、`backends/`、`hosts/` 和 `policy/`，只在已有文件自然过大或职责确实分离时拆目录；不做全仓库无关格式化。新契约测试置于现有 tests 下的分组目录，不引入另一套测试框架。

## 14. 旧数据迁移与回滚

1. 记录旧 schema、代码版本与完整备份；新文件使用独立 `ledger-v3.db`，不在原 `state.db` 上就地尝试重构。停止服务或使用一致性备份机制，不能在活跃 WAL 写入时只复制主文件。
2. dry-run 读取用户授权的 legacy 集合，输出条目数、未知 scope、冲突 ID、缺失证据和待确认项。后端不可枚举时要求显式导出，不猜全量数据。
3. 建立旧 repo_key → 新 project_id 的确认映射；不会自动把相同根提交的 fork 合并。
4. legacy URI → memory_id 映射在导入 receipt 中稳定保存。旧 scope 不清、证据不足的条目 quarantine，不因为有 confidence 字段就当 verified。
5. 不继承旧 cursor 的“已处理”含义；按明确授权的源范围导入到新 journal，并应用删除/抑制清单，避免盲目重扫全部私人会话。
6. 新链路先 shadow：产生候选、提交到测试账本与测试 namespace，但不注入、不执行命令、不写原生记忆。通过 G1–G6 后受控切换。
7. 切换时停掉旧写入口，避免两种生命周期双写。发生问题优先降级到新账本只读；不得直接切回不认识新删除记录的旧服务并继续注入。
8. 回滚代码与回滚数据分别管理。新账本与删除清单保持权威；需要回旧 schema 时先显式导出兼容投影并说明无法表达的语义，否则保持停写/只读。

## 15. 评测设计与可观测性

### 15.1 三层验证分开

- **功能/安全：** I01–I12 与 T01–T12；确定性断言，失败就阻止相关功能上线。
- **记忆过程：** 标注原始证据、作用域、预期有效修订，测候选、召回、晋升和更新，不以输出包含某个词作为唯一判定。
- **实际任务：** 指定宿主真实完成同一任务，测约定遵守、项目事实使用、任务接续、结果正确性与成本。

基准名称不是前提：先使用合成及授权脱敏的纵向场景。公开基准只有在核对版本、下载入口、授权和任务适配后才加入，不能依据旧报告描述直接宣布适合作主基准。

### 15.2 比较组与消融

比较无记忆、宿主原生、后端原生策略、SagaContext 静态、后续学习策略。没有原生 auto-recall 的后端需显式定义其接入流程，不能虚构一个统一“裸后端”基线。

固定模型/宿主版本、任务、工具与预算、原生记忆开关；每组独立账本与后端 namespace。按用户/项目与时间切分学习和评测，未来事件不能影响历史任务。

消融分别移除锚定、任务/路径感知、证据门槛和个性化预算，**仅在隔离测试数据中**测试弱化作用域/过滤的危害，不能在真实数据上关闭安全边界做实验。

### 15.3 指标定义

| 指标 | 分母与判定 | 防止误读 |
|---|---|---|
| 必要记忆召回率 | 本任务标注的必要有效 memory/claim 数 | 只报零污染但完全不召回不算成功 |
| 跨 scope 误注入率 | 实际注入记录数；另报受影响 bundle 比例 | 无注入时标 N/A，并报告遗漏率 |
| 错误取代率 | 实际取代操作数；依据冻结 GT 判是否误删有效结论 | 先看不修坏，再看更新召回 |
| 更新正确率 | 应更新场景数；当前输出使用有效结论 | 允许解释“旧 A 已废弃、现用 B” |
| 证据有效覆盖率 | 需要 evidence 的注入 claim 数 | 同时检查定位与支持关系；非 Git 不强制 git_commit |
| 任务接续正确率 | 存在任务判定的标注场景数 | 新任务误绑定、并行任务误暂停单独计数 |
| 真实任务质量 | 相同任务的测试/判定器结果 | 区分模型本身能力与记忆增量 |
| 成本/时延 | capture/recall/reconcile/projection 各阶段 | 输入输出 token、embedding/模型调用、P50/P95、失败和积压分开报 |

所有比例同时带样本量与失败样例；小样本安全用例零失败不是总体零风险证明。学习策略只在预注册的比较中显示收益且安全项不退化时才能替代静态默认，否则保持关闭。

### 15.4 运行可观测性

最少 trace：`event_receipt / batch_input / proposal_validation / ledger_commit / projection_attempt / recall_decision / conflict_resolution / deletion_progress`。记录统一 trace_id、策略/schema/适配器版本、输入 ID、水位、错误类别、耗时和预算；默认不存正文与密钥。

告警/doctor 关注未解析事件、过期 lease、对账失败、投影积压、未知可见性、删除 pending 和宿主能力漂移。异常路径返回明确状态，用户可区分“没找到记忆”和“后端不可用”。

## 16. 实施阶段、依赖与准入探针

不同时搭建所有模块，不承诺未经估算的日程。每阶段有可运行的小交付；依赖跨过门槛后再冻结对应接口。

| 阶段 | 交付内容 | 退出条件 |
|---|---|---|
| S0 契约补证 | G1 后端旁路、G3 宿主事件；锁定版本与请求样本 | 确认一个真实后端和宿主的可用边界；未通过不开发依赖不存在能力的链路 |
| S1 权威内核 | 中立模型、项目/任务身份、Ledger、修订、删除状态、事务/outbox | 本地 I01–I11 核心断言通过；G4 身份与任务用例通过；测试后端可用 |
| S2 持续维护 | EventJournal、固定批次、证据、锚定对账、任务 checkpoint | 用可控故障后端完成 G2；no_change、LLM 失败和新候选并入边界正确 |
| S3 真实纵向闭环 | 第一真实 Backend/Host、RecallPolicy、投影和同步清理 | 在真实后端复验 G2，完成 G5 删除/取代、G6 三条实际跨会话链路；未通过删除不得广泛导入私人数据 |
| S4 可替换性与迁移 | 第二后端契约、generation 重建、legacy dry-run、受控切换 | 同一语义测试通过，核心状态机不因后端更换修改；不要求搜索排名相同 |
| S5 效果验证 | 真实 runner、静态基线、分项消融、反馈记录 | 产出可复现结果后决定是否投入学习策略；未证明收益不宣传最佳策略 |

S0 探针仅使用合成数据与测试 namespace。可与设计内核自测交错推进，但不能在核心依赖尚不确定时提前大规模搭建适配器。

### 16.1 G1–G6 的具体交付

| 探针 | 需要保存 | 关键通过条件 |
|---|---|---|
| G1 后端旁路 | 版本/配置摘要、请求响应、处理模式、ID 映射、索引可见性 | 规范内容可检索定位到 ID/revision，生命周期不被原生记忆流程重新接管 |
| G2 并发与崩溃 | 条件提交、outbox 重放、超时未知写入及故障注入日志 | 无修订覆盖、无批次证据丢失、旧投影不注入 |
| G3 宿主事件 | 可执行版本、形态、配置、事件样本、超时与退出行为 | 知道可用注入点和证据缺口，不跨形态外推 |
| G4 身份与任务 | fork/worktree/monorepo/移动目录/同分支多任务 fixture | 共享与隔离符合预先标注，不靠一个 root hash 兜底 |
| G5 取代与删除 | 删除各阶段、索引延迟、在途写、重扫 transcript、旧备份恢复测试 | 最新授权状态被读取；删除未完成时准确报告边界 |
| G6 实际闭环 | 原始脱敏事件、旧修订、Delta、提交、下一会话真实注入与任务结果 | 偏好、项目知识、任务接续各至少一条纵向链路可重放 |

12 个验收用例沿用任务书 T01–T12，并增加：T13 合法 no_change 不无限重试；T14 同批次新到候选不被误消费；T15 hook/transcript 双来源不重复确认；T16 未知写入响应后的远端清理；T17 旧上下文无法撤回时的更新提示；T18 宿主/后端版本漂移后的能力降级。

## 17. 审查问题到设计的闭合映射

| 审查发现 | 本方案响应 | 何时才算已解决 |
|---|---|---|
| F01 假设当实测 | §0、§15、§16 明确 DESIGN/VERIFY 与门槛 | 补原始验证记录，不仅改措辞 |
| F02 原生 CAS 误判 | §9 本地事务 CAS；§11 后端无强制 CAS | G2 并发与恢复通过 |
| F03 宿主事件错误 | §12 能力协商、不预设 Stop-only | G3 指定形态实测 |
| F04 账本/投影权威混淆 | §3、§7、§9、§11 接口与读取复核 | 旧投影晚到与切换故障测试通过 |
| F05 身份/任务混淆 | §4 显式项目记录和任务绑定 | G4 隔离/共享样例通过 |
| F06 exit code 当证明 | §5 Evidence/Verification、§8 类型晋升 | 不相关成功命令不能晋升 verified_fix |
| F07 删除/安全不足 | §10 删除状态、抑制、权限与执行隔离 | G5 含未知远端写入与重放场景通过 |
| F08 指标不能归因 | §15 三层评测、固定 GT、分项消融 | 真实 runner 与证据记录完成 |
| F09 竞品/许可过度概括 | §11 候选适配、许可单列验证 | 不借未经确认的“竞品缺失”论证收益 |

## 18. 仍需确认但不再扩大调研的问题

| 问题 | 当前默认 | 关闭条件/退路 |
|---|---|---|
| 第一真实后端 | OpenViking 候选 | G1 不通过则验证 Mem0；无可用后端时只做本地内核，不声称语义检索上线 |
| 第一真实宿主 | 本机能通过 G3 的明确版本/形态 | 能力不足降级为显式工具或回放，自动注入声明同步降级 |
| 投影可发现与删除 | 要求自主管理区可清理 | 不满足则只允许合成数据测试，不作个人数据生产后端 |
| 延迟、触发与 token 预算 | 沿用可调起点、按阶段测量 | 测量后调整，不提前承诺 3% 或固定毫秒 |
| 个人反馈是否值得学习 | 首版只记录、不自动调参 | 时间切分实验优于静态且安全不退化后另立学习设计 |
| 全设备漫游 | 首版不支持 | 需要统一服务或同步冲突设计，不能直接同步活跃 SQLite 文件 |

**本方案的批准含义：同意以此作为分阶段实现和最小补证的设计基线；不等于批准自动安装后端、修改宿主配置、传输私人会话、执行记忆命令，也不等于外部能力已经验证。下一步先执行 S0 的有限探针，再按门槛推进。**
