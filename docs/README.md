# 文档索引（内部）

对外介绍见根目录 `README.md`。本页记录文档状态、已拍板决定与内部工作信息。

## 当前阅读入口

- 查看当前实现与验收结论：[S3-1 后端故障恢复](probes/2026-09-05-s3-1-openviking-recovery.md) → [S3 策略与纵向验收](probes/2026-09-06-s3-policy-shadow-g5-g6.md)。
- 复查测试记录：[验收报告索引](probes/README.md) → [S3 原始运行清单](../artifacts/probes/S3-RESULTS.md)。
- 理解实现约束：[v0.3 总设计](superpowers/specs/2026-09-05-sagacontext-v0.3-design.md) → [S1](superpowers/specs/2026-09-05-s1-data-closure-design.md) → [S2](superpowers/specs/2026-09-05-s2-continuous-maintenance-design.md) → [S3](superpowers/specs/2026-09-05-s3-admission-and-longitudinal-design.md)。
- 本地部署与操作：[OpenViking 部署记录](ops-openviking-local.md)。

## 全量文档目录

下表按阶段由新到旧排列。验收数字属于对应日期的运行快照；S0–S2 的限制、旧探针失败和早期草案不覆盖 S3 的当前结论。保留原文件名和位置，避免破坏既有引用。

| # | 文档 | 内容 | 状态 |
|---|---|---|---|
| 21 | [S3 RecallPolicy、Shadow、G5/G6 验收](probes/2026-09-06-s3-policy-shadow-g5-g6.md) | Ledger 正文复核、预算/省略、实际事件回放、删除/取代、三条真实 Codex 下一会话消费与清理恢复 | **完整合成纵向运行 69/69 通过**；不是生产服务或通用语义抽取验收 |
| 20 | [S3-1 OpenViking 适配器与真实故障恢复](probes/2026-09-05-s3-1-openviking-recovery.md) | Ledger → outbox → 真实 OpenViking；P1–P6、重复写、检索与临时数据清理 | **首次真实验收 22/22 通过**；后续阶段单独验收 |
| 19 | [S3-0 / G3 Codex 宿主事件准入](probes/2026-09-05-s3-g3-codex-host-terra.md) | 使用 gpt-5.6-terra 重跑四场景；旧报告和 fixture 保留作历史记录 | **19/19 必需断言通过；G3 passed；仅限合成探针，不授权真实记忆注入** |
| 18 | [S3-0 / G1 OpenViking 后端准入](probes/2026-09-05-s3-g1-openviking.md) | 固定镜像、隔离 projection、identity/locator、索引、删除、重启、错误分类与清理 | **17/17 必需断言通过；G1 passed**；G3 也已通过，S3-1 见 #20 |
| 17 | [S3 准入探针与真实纵向闭环设计](superpowers/specs/2026-09-05-s3-admission-and-longitudinal-design.md) | OpenViking + 本机 Codex CLI 候选、G1/G3 独立判定、Shadow/G6 边界与分级授权 | **已按用户指令顺序实施；S3-1 至 S3-5 合成验收见 #20/#21** |
| 16 | [S2 持续维护验收](probes/2026-09-05-s2-acceptance.md) | Schema v2、J1-J4、B1-B5、R1-R4、A1-A2、C1、P1-P6 与 G2 故障时序的具名本地验收 | **86 项测试通过；S2 本地退出条件满足**；不代表真实宿主或后端已启用 |
| 15 | [S2 持续维护设计](superpowers/specs/2026-09-05-s2-continuous-maintenance-design.md) | EventJournal、固定 batch、proposal/review、原子 `commit_batch`、task checkpoint、可控故障 projector、lease fencing 与 G2 验收矩阵 | **已批准并实现**；仅限本地合成数据和测试后端，不启用真实宿主或后端 |
| 14 | [S1 数据收口验收](probes/2026-09-05-s1-acceptance.md) | 入口隔离、I01–I11、G4、旧库不变与固定命令的具名验收结果 | **53 项测试通过；S1 本地退出条件满足** |
| 13 | [S1 数据收口设计](superpowers/specs/2026-09-05-s1-data-closure-design.md) | Ledger 唯一权威入口、应用组合层、daemon/CLI 契约、禁用宿主写入、删除语义、G4 与 I01–I11 验收 | **已批准并实现**；本阶段门槛保留，当前进展见 #20/#21 |
| 12 | [S0 本机能力探针](probes/2026-09-05-s0-local.md) | OpenViking 与 Codex CLI 的本机前置检查、通过状态和执行边界 | 历史 S0 记录；最新 G1/G3 结果分别见 #18/#19 |
| 11 | [技术实现方案 v0.3](superpowers/specs/2026-09-05-sagacontext-v0.3-design.md) | 权威账本/检索投影/宿主边界、身份与记忆模型、持续对账、并发恢复、遗忘安全、代码和数据迁移、G1–G6 门槛与分阶段实施 | **当前分阶段实现基线**；S1–S3 验收报告见上方入口 |
| 10 | [调研报告审查意见](10-调研报告审查意见-2026-09-05.md) | 固定源码证据、九项发现、R1–R6 完成度、六个实施准入探针 | **审查完成：部分通过，需补证后再冻结设计** |
| 09 | [个性化记忆策略层调研报告](09-个性化记忆策略层-调研报告.md) | 后端与宿主对比、权威账本与 SPI 草案、演化场景、策略及评测建议 | **已提交，审查意见见 10；尚非冻结设计依据** |
| 08 | [个性化记忆策略层：调研目标与交付要求](08-个性化记忆策略层-调研目标与交付要求.md) | 后端解耦、项目记忆持续维护、证据与安全、宿主适配、个性化策略、评测六个工作包，以及场景、证据与回传模板 | **调研任务书**，报告见 09、审查见 10；不代表已通过的新设计 |
| 07 | [技术实现方案 M1](07-技术实现方案-M1.md) | 运行时布局与 config、hooks.json 与 shim、SQLite DDL 与核心模型、记忆文件与 yaml 模板示例、七个 hook 事件的处理时序、召回/候选识别/对账/任务接续的算法与伪码、OpenViking/LLM/embedding 接口契约、降级矩阵、测试方案、性能预算、M2–M4 要点、Phase 0 清单 | **草案** |
| 06 | [技术设计与实现路线](06-技术设计与实现路线.md) | 硬约束（AGPL 边界、无 embedding 端点、文件格式、hook payload）、16 条技术方向决策 TD-01..16、关键模块设计、Phase 0–4 实现路线、仓库布局、风险 | **草案** |
| 05 | [分层模型与对账循环](05-分层模型与对账循环.md) | 六层模型、项目层与任务层类型、通用对账循环（锚定抽取）、注入预算、评测扩展、对 03/04 的修订清单、四个待决问题的决议 | **当前定位** |
| 03 | [重新定位-个性化记忆](03-重新定位-个性化记忆.md) | OpenViking 个性化机制现状、四层增量（P1 画像 / P2 演化 / P3 反馈学习 / P4 遵守）、个性化域竞品与基准 | 有效，项目层部分被 05 修订 |
| 04 | [设计规格 v0.2](04-设计规格-v0.2.md) | 八个记忆类型的字段级 schema、对账循环（候选识别 / 锚定抽取 / 写入策略 / 演化状态机 / 任务生命周期）、注入预算、Compliance、评测、接口、里程碑、已决事项与待验证清单 | **草案待审阅**，通过后 M0 冻结 |
| 04-v0.1 | [设计规格 v0.1](04-设计规格-v0.1.md) | 只覆盖用户层与偏好层 | 已被 v0.2 取代 |
| 02 | [审核意见](02-审核意见.md) | 对 v0 报告的源码级事实核查、遗漏竞品（claude-mem / dreviho 附录 A 对照）、对"主创新点"与主基准的挑战 | 参考 |
| 01 | [调研与路线报告 v0](01-调研与路线报告-v0-已过时.md) | 最初的"Policy Gateway + Weighted RRF"路线 | **已过时**，仅保留 §2 证据边界、§9 隐私/许可证/故障策略、§10 命名 |

## 历史决定（2026-09-04，已由 v0.3 取代）

以下仅保留历史讨论，不应覆盖文档 10 的纠错或文档 11 的当前实现基线；相关外部能力仍须先完成相应探针。

- OpenViking 作引擎：独立 AGPL sidecar，只经 `memory.custom_templates_dir` + HTTP/MCP + 上游插件 hook 事件对接，不改服务端代码。
- 项目层与偏好层并列为一等层；再加任务、团队/peer、Agent 经验三层。
- 写回以会话末为主（Stop 阈值 / SessionEnd / PreCompact），会话中只召回、只缓冲候选；增量体现在"以已有记忆为锚"，不是每轮写盘。
- 评测覆盖偏好层与项目层。
- 团队层走 `viking://resources` + group ACL，不用 peer 命名空间；记忆由 SagaContext 直接按 OpenViking 文件格式写入，不经上游抽取。
- 技术：Python 3.11 daemon + sh/curl shim；只走 OpenViking HTTP，不 import 其 AGPL 代码；本地状态单 SQLite；对账引擎纯函数化可录制回放（详见 06）。
- 分期：**M1** 用户/偏好/项目/任务四层本体 + 对账循环 + 基准 v0 → **M2** 遵守（P4）→ **M3** 反馈学习召回（P3）+ Agent 经验层 → **M4** 团队/peer 层 + 加固。

## 源码核对

外部仓库本地 checkout 位于 `~/Documents/Codex/2026-09-03/wo-x/work/research/`（OpenViking `a838014`、codex、memU、memsearch、mem0、letta-code、LongMemEval-V2、openviking-hooks/dreviho `d93ee15`、claude-mem `be44b6c`），未纳入本仓库。

## 命名

`sagacontext`（repo / Python 包）· `sagactl`（CLI）· `sagacontext-daemon`。已做 GitHub / PyPI / npm 初查无同名；未做商标与 crates.io 检查。
