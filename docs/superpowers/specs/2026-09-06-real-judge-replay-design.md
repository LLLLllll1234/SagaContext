# 真实 Judge 适配器与固定回放评测设计

**日期：** 2026-09-06  
**状态：** 待用户审阅；架构方向已确认  
**范围：** OpenAI-compatible HTTP Judge、同步 ProposalJudge 门面、Delta 转换、固定回放报告

## 1. 目标与非目标

本阶段把维护 worker 当前使用的 ScriptedJudge 替换为可调用真实 OpenAI-compatible HTTP 模型的适配器，并提供一组固定、合成或脱敏的回放样本，用真实 Judge 产生结构化结果后与预先冻结的标注比较。

必须达到：

- 同步入口实现现有 ProposalJudge 契约，不修改 BatchWorker 的同步调用方式；
- 复用现有异步 HTTP 请求契约，单次调用使用单独的异步客户端和事件循环生命周期；
- 区分超时、限流、暂时性服务错误、认证失败、配置错误、响应格式错误和 schema/转换校验错误；
- 明确完成 Delta[] -> DeltaProposal[]，模型只提出候选变更，Ledger 仍是提交、CAS、冲突和证据关联的权威；
- 固定回放实际调用 Judge，分别统计调用状态、关系判断正确性和转换正确性，并保留失败案例；
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
        -> 关系/转换双重评分
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

## 6. 固定回放数据与 runner

回放文件放在 bench/cases/real_judge/，输入与标注分离但由同一个 case ID 关联。每个 case 固定：

- 一个合成或脱敏候选、事件摘要和 anchors；
- 预期 relation（新增、确认、补全/refine、推翻/supersede、冲突、无需更新）；
- 预期 target/revision/evidence 关联；
- 允许的 payload 字段和转换结果。

运行前计算 case/annotation digest，运行期间拒绝隐式修改。CLI 或脚本显式要求 LLM 配置存在；没有配置时报告 blocked_configuration，不伪造通过。

每个结果 JSONL 至少保存 case_id、judge_version、prompt_contract_version、schema_version、converter_version、model 摘要、sampling、latency_ms、status、error_class、response_digest、actual_deltas、actual_proposals、expected_relation、relation_correct 和 conversion_correct。

不得写入 API key、Authorization header、完整内部 URL、私人正文或未脱敏 transcript。失败案例保留错误分类和脱敏输入/输出摘要。

报告至少分开显示：

- Judge 调用成功率和错误分类计数；
- relation accuracy：实际 relation 与冻结标注的匹配；
- conversion accuracy：target、revision、scope、evidence、payload 和 operation 全部匹配；
- 按六类场景的逐例结果和失败案例；
- 每例耗时及总 token/费用（只有 provider 返回且可安全记录时才展示，否则标记 unavailable）。

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

### 固定回放退出条件

- 至少六个冻结 case 覆盖 new/confirm/refine/supersede/conflict/no_change；
- 每个 case 真实调用 OpenAI-compatible endpoint，或在无配置时明确 blocked_configuration，不能用 fixture observation 冒充；
- 每个 case 都有 relation 与 conversion 两项结果；
- 失败案例完整保留并能从 case digest 复现；
- 本地现有 111 项回归测试继续通过；新增测试全部通过；
- 报告明确标注模型、提示词、schema、转换器和采样参数，且不泄露秘密。

## 8. 后续阶段边界

本设计完成后，下一步仍需要单独设计和批准正常会话事件入口、常驻 worker、自动召回注入和真实运行监控。本阶段的回放通过不能直接升级为“任意正常对话可自动抽取”，也不能授权导入私人 transcript。
