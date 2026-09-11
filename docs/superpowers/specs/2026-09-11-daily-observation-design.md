# 有限期日常观察与独立收尾

依据本会话已批准计划：只观察 SagaContext workspace 的新事件，最多 10 session、20 candidate、24 小时；逐条代理审核后写入，记录实际质量与消费证据；到期/异常停止，按同一 rollout 回滚。本设计不增加 workspace、配额、active 或历史导入。

## 实现选择

复用现有 Ledger 控制面、不可变 rollback plan 和认证 rollback。新增本地 observation 命令和独立 launchd 每分钟巡检。相比只依赖 daemon 线程，这能在 daemon 退出后继续收尾；相比新增特权服务，不增加另一套权限协议。观察行为由 activation 中签名绑定的 observation plan 标记限定，巡检只管理该标记且匹配 owner/workspace 的 rollout。

启动先验证配置、服务、固定宿主版本、现有四类 hooks 和已运行巡检，再解除 STOP 并认证 activation；activation 与 observation_opened receipt 在同一 SQLite 事务。失败恢复 STOP。24 小时硬 deadline 由已有事件/review/bundle 门禁执行；独立巡检每 60 秒清理，到期不依赖下一条事件。机器休眠/登出期间不能声称实时清理；唤醒/登录后继续，但运行期硬 deadline 不延长。

## 审核与回滚可逆性

现有 rollback 仅能可靠撤销本 rollout 独占创建的单一 revision。首轮观察因此仅允许 `new`、当前 project scope 的 proposal 审核通过；refine、replace、delete、跨 project/global scope 必须拒绝或保留待另行设计，不得写入后再声称可回滚。门禁放进 review/commit 同一事务，并且只对 observation plan 启用。普通既有验收不改变。

普通 daemon 继续调度 Judge/Projector。巡检不批准 proposal、不生成质量标签或消费回执。代理审核需读取 proposal 与原始事件依据，审核来源记为 user_delegated_operator；无法核对的候选拒绝。人类与代理观察仍区别于宿主实际消费证据。

## 巡检状态与台账

本机 `observations/<rollout_id>/latest.json` 保存脱敏计数、ID、pending batch ID、quota、deadline、质量分母、回滚目标与状态，不包含原始提示、正文或凭据，文件 0600。`before-cleanup.json` 和 `final.json` 记录收尾前后；Ledger observation receipts 保留开始/收尾/失败事实，重复巡检不产生重复完成回执。

检测 deadline、STOP、配置变 off、daemon/scheduler 不可用、超配、缺少审核的 commit、已标注错误召回、cleanup_required 时停止；先设置 STOP，再认证执行固定 plan 的 rollout rollback。第 10 个 session 进入 draining，已 reservation 会话仍可完成，全部 SessionEnd 且 batch/outbox 无在途后收尾；20 candidate 耗尽同样等待既有会话/审核完成或 deadline。配额耗尽不会开启新一轮。

一次清理失败记录 cleanup_required 和错误类别；下一次巡检使用当前本机 operator key、新 receipt 重试，复用持久化 rollback lease/step/compensation，禁用旧 key 兜底。身份/plan 校验失败不清理其他资源。凭据失效时保留 STOP 并告警，不伪造完成。

成功需 Ledger 残留全零且本 rollout projection locator 精确 inspect 不存在，写 observation_closed，再输出 final。没有新事件时样本数为零，禁止用合成事件填补。

## 实现与验收顺序

1. 实现 observation 管理、脱敏快照、独立巡检安装/状态与启动前置检查。
2. review 新增仅 observation 的可逆操作/scope 门禁。
3. 测试：零样本到期、停止、daemon 下线、重启继续、错误 key、补偿失败恢复、重复巡检、非 observation 隔离、quota draining、非法审核、快照无正文。
4. 全量回归与隔离真实巡检演练通过后安装本机巡检；保持 STOP 重启 daemon 载入门禁。
5. 开启一次最长 24 小时观察，记录 rollout/deadline/准入快照，真实样本自然到来。到期收尾和后续报告由持久巡检承担；逐条质量审核通过后续代理工作处理。

当前规格是已批准日常观察计划的工程细化；不重开重复批准流程。没有可用 writing-plans skill，以上顺序作为本轮实现计划。
