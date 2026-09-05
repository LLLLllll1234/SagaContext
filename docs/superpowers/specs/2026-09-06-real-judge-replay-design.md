# 真实 Judge 适配器与语义验收设计

**日期：** 2026-09-06  
**状态：** 范围已于 2026-09-06 确认；四项审查意见已纳入，待确认修订规格
**范围：** OpenAI-compatible HTTP Judge、同步 ProposalJudge 门面、Delta 转换、困难样本与固定回放语义验收

## 1. 目标与非目标

本阶段把维护 worker 当前使用的 ScriptedJudge 替换为可调用真实 OpenAI-compatible HTTP 模型的适配器，并提供一组固定、合成或脱敏的回放样本，用真实 Judge 产生结构化结果后与预先冻结的标注比较。现有六个单候选样本是调用链路冒烟，不单独构成语义验收。

必须达到：

- 同步入口实现现有 ProposalJudge 契约，不修改 BatchWorker 的同步调用方式；
- 复用现有异步 HTTP 请求契约，单次调用使用单独的异步客户端和事件循环生命周期；
- 区分超时、限流、暂时性服务错误、认证失败、配置错误、响应格式错误和 schema/转换校验错误；
- 明确完成 Delta[] -> DeltaProposal[]，模型只提出候选变更，Ledger 仍是提交、CAS、冲突和证据关联的权威；
- 固定回放实际调用 Judge，将调用状态与语义质量分开，并独立评分关系判断、记忆正文、证据引用和转换结果；
- 新增重复表达、偏好反转、信息不足和无关内容困难样本，对不应写入的内容设置独立安全门槛；
- artifact 记录 Judge 版本、提示词/schema/转换器版本、采样参数、耗时和脱敏结构化结果。

不包含：正常会话自动化、常驻 worker、私人 transcript、OpenViking 写入、第二后端、线上效果结论、自动调参和凭据持久化。

## 2. 组件与边界

组件关系：

    BatchWorker (同步)
        -> ProposalJudge.judge(BatchInput)
        -> OpenAIProposalJudge (同步门面)
           -> asyncio.run(AsyncOpenAIJudge.judge(...))
           -> 错误分类
           -> Delta 校验与 DeltaProposal 转换
        -> BatchWorker 的有限重试/阻断
        -> Ledger commit_batch

    ReplayRunner
        -> 冻结 case + 标注
        -> OpenAIProposalJudge
        -> 关系/正文/证据/转换四维评分 + 忽略安全门槛
        -> JSONL artifact + Markdown report

同步门面只允许在当前线程没有运行中事件循环时调用。若检测到 asyncio.get_running_loop() 成功，立即抛出明确的 JudgeEventLoopError，不能嵌套 asyncio.run。异步 HTTP 客户端不得保存到跨调用或跨事件循环的共享状态中。

适配器不直接写 Ledger；它只返回经过验证的 DeltaProposal。BatchWorker 继续执行 target 校验、proposal 持久化、revision CAS、冲突转人工审核和提交。

## 3. HTTP Judge 契约

### 3.1 配置

沿用现有 [llm] 配置字段，并允许环境变量覆盖：

- SAGACONTEXT_LLM_BASE_URL
- SAGACONTEXT_LLM_API_KEY
- SAGACONTEXT_LLM_MODEL

缺少 base URL、model 或 key 时属于 judge_configuration_error，不调用网络。凭据和完整 endpoint 不写入 replay artifact；artifact 只记录 endpoint 的脱敏 host 摘要或配置 fingerprint。

### 3.2 请求

请求继续使用 /chat/completions、Bearer 认证和 JSON structured output。固定版本的 system prompt、user payload、JSON schema、temperature、timeout 和最大尝试次数组成 prompt_contract_version，写入结果元数据。输入只包含当前 batch 的脱敏 anchors、候选、事件摘要和必要的 scope/identity；不把 Ledger 写入权限交给模型。

### 3.3 响应

模型必须返回 JSON object，包含 deltas 数组。每个 Delta 必须包含 candidate_id、layer、type、relation、anchor_uri、key、fields、evidence_ids、strong_signal、confidence_hint 和 rationale。

candidate_id 是适配层必须校验的关联字段。relation 只允许 confirm/refine/supersede/new/conflict；no_change 不作为 Delta 项输出。

只有响应成功、JSON 可解析、整体通过 Pydantic schema 且转换成功的 deltas=[] 才表示无需更新。空 body、缺少 choices/content、JSON 解析失败、schema 失败或所有 Delta 被拒绝都属于失败，不能降级成 no_change。

首轮回放每个 batch 只放一个候选，避免在现有五类 relation 之外引入“部分候选未变化”的隐式语义。未来需要多候选混合结果时，必须扩展显式 response schema 后再实现。

## 4. 错误分类与重试

适配器定义结构化 JudgeError，至少包含 class_name、retryable、attempts、status_code（可选）和脱敏 detail：

| 分类 | 示例 | retryable | worker 行为 |
|---|---|---:|---|
| judge_timeout | connect/read timeout | 是 | 使用现有有限重试 |
| judge_rate_limited | HTTP 429 | 是 | 使用现有有限重试 |
| judge_service_unavailable | HTTP 408/5xx | 是 | 使用现有有限重试 |
| judge_authentication_error | HTTP 401/403 | 否 | 直接阻断 |
| judge_configuration_error | 缺失配置、非法 URL | 否 | 直接阻断 |
| judge_response_error | 缺 choices/content、非 JSON | 否 | 直接阻断 |
| judge_schema_error | Delta 字段、关系、类型非法 | 否 | 直接阻断 |
| judge_conversion_error | candidate/anchor/evidence 不可关联 | 否 | 直接阻断 |
| judge_event_loop_error | 当前线程已有 running loop | 否 | 直接阻断 |

适配器不做内部重试；一次同步调用最多对应一次底层 HTTP 请求。worker 继续使用 max_attempts 统一控制可重试错误。认证、配置和确定性的解析/校验错误不重试，避免两层重试和无意义的模型调用。

现有 OpenAIJudge 的“失败返回空列表”行为需要改为抛出上述分类错误；只有真正的合法空增量才返回空 list[Delta]。

## 5. Delta 校验与转换

转换前先建立冻结索引：

- candidate_id -> candidate context；
- memory_id -> anchor revision/uri；
- event_id -> allowed evidence。

逐条 Delta 执行以下检查，任一失败则整批转换失败，不返回部分 proposals：

1. candidate_id 必须存在于当前 batch，且首轮样本中只出现一次；
2. type 必须是 Ledger 允许的 memory type，并与候选的 memory_type_hint 一致；
3. relation=new 必须没有 anchor_uri；其他 relation 必须提供能精确命中当前 batch anchor 的 anchor_uri；
4. 命中 anchor 后将其 memory_id 写入 target_id，将冻结 revision 写入 expected_revision；模型不能自行提供 revision；
5. scope 从候选的冻结 scope_hint 继承，模型不能扩大作用域；
6. payload 由稳定的 key 与 fields 组成，拒绝非 JSON object、保留字段覆盖和超出大小上限的内容；
7. evidence_ids 只能引用当前候选/事件集合中允许的 evidence；首轮实现不接受模型虚构证据；
8. rationale 仅作为受限、脱敏 trace 字段，不进入 Ledger 权威正文；
9. relation 到 proposal operation 一一映射：new/confirm/refine/supersede/conflict 保持同名。

空 Delta 列表时，为当前唯一候选生成一个 operation=no_change 的 DeltaProposal，payload 为空、target/evidence 按候选冻结上下文填充，供 worker 正常落 proposal 并 settle。非空但全部 Delta 被拒绝时抛出 judge_conversion_error，不得生成 no-change proposal。

转换评分只测量“适配器是否忠实地把模型 Delta 转为 proposal”，不再将 proposal 的 operation 与人工关系标注比较。评分器直接根据实际 Delta 和冻结 batch context 校验以下不变式：operation 等于实际 relation、target/revision 由实际 anchor 解析、type/key/fields/evidence 原样传递、scope 从候选继承。空 Delta 只校验规定的 `no_change` 合成行为。“最终 proposal 是否匹配人工标注”另作为端到端组合指标报告，不作为独立 100% 转换门槛。

## 6. 固定回放数据与 runner

回放文件放在 `bench/cases/real_judge/`，输入与标注分离但由同一个 case ID 关联。现有 `cases.yaml` 固定为历史 `real-judge-v1`，不就地修改；首次真实语义验收使用新的 `cases-v2.yaml`。runner 必须显式记录 dataset ID、schema version 和 digest，禁止以新文件覆盖旧 artifact。

每个 v2 case 固定：

- 一个合成或脱敏候选、事件摘要和 anchors；
- 预期 relation：`new/confirm/refine/supersede/conflict`，或者显式 `no_change`；
- `should_ignore`，用于单独标记信息不足、无关内容等不应写入的候选；
- 预期记忆正文，以符合冻结 body schema 的 `key + fields` 结构表示；
- 预期 evidence IDs；
- 预期 target、revision、memory type 和 scope，供端到端组合评分；
- `field_evidence`：为每个预期正文字段记录 candidate 或 anchor 中的具体 JSON Pointer，以及该位置的精确 quote 或结构值；不得存在无输入依据的标准值。

### 6.1 v1 标注依据审计

| v1 case | 审计结论 | v2 处理 |
|---|---|---|
| `real-new-decision` | `pytest` 由 candidate 明确提供 | 保留语义，补 `field_evidence` |
| `real-confirm-convention` | 关系和正文同时由 candidate 与 anchor 支持 | 保留语义，补 `field_evidence` |
| `real-refine-gotcha` | 新的 `applies_when` 由 candidate 提供，其余字段由 anchor 提供 | 保留语义，显式标注合并来源 |
| `real-supersede-taste` | `json` 取代 `prose` 由 candidate 明确提供 | 保留语义，补 `field_evidence` |
| `real-conflict-project-map` | 标注中 `src/new.py` 未出现在任何输入 | v1 仅作历史反例；v2 候选显式给出该路径和未确认措辞 |
| `real-no-change` | 无虚构正文，但输入直接泄露“should not update”标签 | v1 仅作链路冒烟；v2 改为不带判定结论的自然一次性请求 |

v2 发布前执行全量人工审计和结构校验。结构校验证明 JSON Pointer 存在、类型合法且 quote/结构值确实出现在所指输入中；它仍不能替代“该来源在语义上支持标注”的人工判断。审计结果与 dataset digest 一起冻结。

### 6.2 样本矩阵

数据集至少包含 14 个单候选 case：

| 分组 | 最少数量 | 主要验证 |
|---|---:|---|
| 现有六类冒烟 | 6 | `new/confirm/refine/supersede/conflict/no_change` 各一例 |
| 重复表达 | 2 | 字面重复和语义改写均不得新建重复记忆 |
| 偏好反转 | 2 | 强时序措辞可 `supersede`，弱或临时措辞不得误覆盖 |
| 信息不足 | 2 | 无法得出稳定正文时必须 `no_change` |
| 无关内容 | 2 | 闲聊、一次性指令或无持久价值内容必须 `no_change` |

困难样本不仅替换同义词，还要改变否定、时间范围、确定性和持久性措辞。每个样本仍只有一个候选，避免把“候选间分配”混入本阶段。

### 6.3 正文 schema 与规范化契约

v2 固定 `body-schema-v1` 和 `body-normalizer-v1`。评分器不接受任意字段别名，也不使用另一个 LLM 判断等义：

| memory type | 允许字段 | 值类型 |
|---|---|---|
| `decision` | `key`, `command` | 规范命令 |
| `convention` | `key`, `command` | 规范命令 |
| `gotcha` | `key`, `symptom`, `fix`, `applies_when` | 短文本 |
| `taste` | `key`, `format` | 冻结枚举 |
| `project_map` | `key`, `path` | POSIX 相对路径 |

schema 为每个 case 进一步冻结 required/optional 字段；实际输出出现未允许字段、缺失 required 字段或类型错误时，`memory_body_correct=false`。`body-normalizer-v1` 只做 Unicode NFKC、字符串首尾空白删除、连续空白折叠和 object key 排序；不小写化命令或路径，不自动改名字段。

枚举、命令和路径在规范化后必须精确匹配。自由短文本由 case 预先冻结有限 `acceptable_bodies`，命中任一完整结构即通过；可接受答案必须通过 `field_evidence` 审计，首次实测后不因模型新表达而追加。

### 6.4 评分适用范围

每次调用产生五个独立分项和一个组合诊断结果：

1. `relation_correct`：所有成功调用适用，只比较实际 Delta relation 与标注关系；合法空 Delta 映射为 `no_change`。
2. `memory_body_correct`：只对预期非空 Delta 的 case 适用；读取模型 Delta 的 `key + fields`，按 `body-schema-v1/body-normalizer-v1` 比较。预期 `no_change` 时为 N/A。
3. `evidence_correct`：只对预期非空 Delta 的 case 适用；读取模型原始 Delta 的 evidence ID 集合并精确比较。不读取 proposal，因为空 Delta 时转换器自动填入的 candidate event IDs 不是模型引用。预期 `no_change` 时为 N/A。
4. `conversion_fidelity_correct`：所有成功调用适用；按第 5 节不变式检查 proposal 是否忠实转换实际 Delta，不与人工 relation/body/evidence 标注重复计分。
5. `ignore_correct`：只对 `should_ignore=true` 的 case 适用，必须返回合法空 Delta；`should_ignore=false` 时为 N/A，不进入分母。
6. `proposal_semantic_correct`：端到端诊断项，只有所有适用的 relation/body/evidence 指标以及 proposal target/revision/type/scope 都正确时才为 true；不用它替代分项指标。

另行报告调用成功率、错误分类、耗时、token 和费用。配置失败、网络失败或 schema 失败不记为语义错误，但整次验收不能因此判定通过。

### 6.5 冻结与重复运行

运行前计算 case/annotation digest，运行期间拒绝隐式修改。验收固定模型名、endpoint 配置指纹、prompt/schema/converter 版本、`temperature=0`、timeout 和样本顺序，对全部 case 独立运行 3 次。任何 prompt、schema、标注或模型变更都生成新版本和新 artifact，不覆盖旧结果，不允许只选最好一轮报告。

凭据只通过环境变量或用户本机的 `~/.sagacontext/config.toml` 提供，不写入仓库。CLI 或脚本显式要求 LLM 配置存在；没有配置时报告 `blocked_configuration`，不伪造通过。

每个结果 JSONL 至少保存 run ID、repeat index、case ID、上述版本与配置指纹、sampling、latency、status、error class、response digest、actual deltas/proposals、全部冻结标注和六项评分结果。不适用的指标写为 `null`，不得写为 true 或 false。

不得写入 API key、Authorization header、完整内部 URL、私人正文或未脱敏 transcript。失败案例保留错误分类和脱敏输入/输出摘要。

报告至少分开显示：

- Judge 调用成功率和错误分类计数；
- relation、memory body、evidence、conversion fidelity 和 ignore accuracy，分子分母明确，N/A 不进入分母；
- proposal semantic correctness 仅作端到端组合诊断；
- 按六类冒烟与四类困难样本分组的逐例、逐次结果；
- 跨 3 次重复运行的稳定性与所有失败案例；
- 每例耗时及总 token/费用（只有 provider 返回且可安全记录时才展示，否则标记 unavailable）。

聚合先按单个 case 处理三次重复，再汇总分组和全局，禁止直接把同组的六个 observation 合并后掩盖某个不稳定 case。对 relation/body/evidence，单 case 在适用时至少 2/3 才算 case-pass；对 ignore 和 conversion fidelity，单 case 必须 3/3。每一轮的全局准确率仍独立报告，不只报告三轮池化值。

报告不得把调用成功率当作语义质量，也不得把合成回放结果描述为任意正常对话的自动抽取效果。

## 7. 测试与退出条件

### 单元/契约测试

- running event loop 明确报错，普通同步线程可执行；
- HTTP 401/403、429、408/5xx、timeout、坏 JSON、坏 schema 分别映射到预期错误类；
- 合法 [] 生成 no-change；空 body、全拒绝和部分非法输出不生成 no-change；
- 每种 relation 的 Delta 转换及 target/revision/evidence 校验；
- 未知 candidate、anchor、evidence、memory type、scope 扩大和 revision 伪造均阻断；
- worker 对 retryable 错误有限重试，对确定性错误一次阻断；
- request 版本、schema、converter version 和 digest 稳定写入 artifact。
- dataset v1/v2 可独立加载且 digest 不混用；`field_evidence` 的 JSON Pointer 不存在时拒绝数据集；
- `body-schema-v1/body-normalizer-v1` 对空白、Unicode、字段别名、未知字段、命令大小写和可接受自由文本的处理符合契约；
- relation 错误但 Delta 被忠实转换时，`relation_correct=false` 且 `conversion_fidelity_correct=true`；
- 空 Delta 时 evidence 和 body 为 N/A，proposal 自动填入的 event IDs 不会反向变成模型证据得分；
- 聚合器先判定单 case 的 2/3 或 3/3，并保留每轮独立结果。

### 固定回放退出条件

- 至少 14 个冻结 case，覆盖六类冒烟和四类困难语义；
- `real-judge-v1` 作为历史数据保留，通过标注依据审计的 `real-judge-v2` 使用新 dataset ID、schema version 和 digest；
- 每个 case 真实调用同一 OpenAI-compatible endpoint 3 次，不能用 fixture observation 冒充；
- 每个 observation 都有 relation、memory body、evidence、conversion fidelity、ignore 和 proposal semantic 六项结果，不适用项为 N/A；
- 失败案例完整保留并能从 case digest 复现；
- 调用成功率必须为 100%，否则整次验收状态为阻断，不计算或粉饰语义通过；
- `[TARGET]` 每个 case 的 relation、memory body 和 evidence 在适用时至少 2/3；每个 ignore 和 conversion fidelity case 必须 3/3；
- `[TARGET]` 每一轮单独计算的 relation 和 memory body 全局准确率均不低于 90%，evidence、conversion fidelity 和 ignore 在各自适用集合上均为 100%；
- 困难分组只有在组内每个 case 都达到上述单 case 门槛时才通过，不使用组内池化 `4/6` 替代单例判定；
- 上述门槛是运行前冻结的验收目标，不是已验证结果；首轮实测后即使未达标也保留原始 artifact，不修改标注来追求通过；
- 当前全量回归测试继续通过；新增测试全部通过；
- 报告明确标注模型、提示词、schema、转换器和采样参数，且不泄露秘密。

## 8. 后续阶段边界

本设计完成后，下一步仍需要单独设计和批准正常会话事件入口、常驻 worker、自动召回注入和真实运行监控。本阶段的回放通过不能直接升级为“任意正常对话可自动抽取”，也不能授权导入私人 transcript。
