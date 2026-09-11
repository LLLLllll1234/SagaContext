# 2026-09-11 有限期日常观察

已按授权实现日常观察台账与独立到期收尾机制。首轮仅 SagaContext workspace 的新事件，10 session、20 candidate、最长 24 小时，不启用 active，不导入历史 transcript，不用合成事件凑样本。

## 实现与边界

- `bin/sagacontext-observe start --hours 24` 先检查固定 Codex 版本、hooks 功能及四类已安装 hooks、Judge/backend 配置、真实 backend 连接、daemon scheduler 和近期成功的巡检，然后创建带固定 observation plan 的认证 activation。
- activation 与 observation_opened receipt 同事务，失败恢复 STOP；不会覆盖已有 live rollout，也不会自动续期。
- 独立 LaunchAgent `com.sagacontext.observation` 每 60 秒执行一次，启动/登录时也运行。它使用本机当前 operator token 文件，凭据不进入进程参数或 plist。
- Ledger hard deadline 继续在事件、review、bundle 等路径执行。巡检不依赖 daemon 存活；到期、STOP、daemon 不可用、已标注错误召回、处理失败或超配时冻结并认证回滚。机器休眠/登出时不能执行清理，恢复后补做；不延长 deadline。
- 第 10 个 session 进入 draining，第 11 个拒绝；已接收会话、待审 batch 和 outbox 可以完成。既有会话全部结束且工作清空后收尾，最晚受 deadline 限制。candidate 配额达到后同样等待已有工作完成，不生成下一轮。
- 审核事务对 observation rollout **仅允许本项目 scope 的 new proposal**，使首轮数据可被现有独占创建回滚撤销。refine/supersede/conflict、跨项目/global scope 等不能批准；仍可拒绝。
- 巡检不会自动审核、标注有效性或伪造消费回执。质量指标仅按已标注分母计算；无依据为 unknown/null。
- 失败收尾保留 cleanup_required，下次巡检以新 current-key receipt 重试。成功必须完成持久化残留校验及本 rollout locator 精确远端 absence 检查，再保存关闭回执。

## 台账与操作

本机 `~/.sagacontext/observations/<rollout_id>/`：

- `latest.json`：脱敏计数、可标注 ID、待审 batch ID、quota/deadline、质量和清理状态。
- `before-cleanup.json`：首次收尾前快照。
- `final.json`：清理后快照及 closure 结果。

文件权限 0600，不包含原提示、记忆正文或凭据。正常窗口中的这些文件留在本机，公开报告只导出必要的脱敏统计。`watchdog.json` 记录最近巡检时间及错误状态；`observation-watchdog.log` 只输出摘要和错误类别。

```sh
bin/sagacontext-observe status
bin/sagacontext-daemon-control pending
bin/sagacontext-daemon-control report --rollout-id ROLLOUT_ID
bin/sagacontext-daemon-control review --batch-id BATCH_ID --decision approve
bin/sagacontext-daemon-control annotate --rollout-id ROLLOUT_ID --observation-file /absolute/path/label.json
bin/sagacontext-observe finish --rollout-id ROLLOUT_ID
```

紧急停止可使用 `bin/sagacontext-daemon-control stop`；巡检仍会处理已登记 observation 的清理。`finish` 是主动结束及清理当前 observation；不自动再开启。安装巡检：`bin/sagacontext-observe install-watchdog`。巡检只管理 observation plan 完全匹配且已登记的 rollout，不替其他受控运行执行回滚。

代理逐条审核时必须核对本 rollout 的新事件证据、proposal scope、是否持久有效及是否重复/冲突。缺乏依据的内容不批准；写入记忆不是下条会话实际消费的证据。若不能获得当前会话的独立消费证据，保持 unknown，不搜索/导入历史 transcript。

## 验证

新增 18 项测试：零事件到期、待审不自动提交、非本项目/非 new 审核原子拒绝、凭据错误先冻结、删除失败后进程重启恢复、非 observation 隔离、session/candidate quota draining、outbox 等待、错误召回标注停止、最终导出失败恢复、重复巡检、独立 CLI 进程在 daemon 不可用时清理，以及 plist 不携带凭据。

全量 **264 tests、96 subtests** 通过；compileall、git diff --check 通过，保留 2 个既存弃用警告。本机 launchd 首次真实巡检退出码 0，成功写入心跳；daemon 在 STOP 下重启以加载审核门禁。

## 观察状态

已启动 rollout `5d6b4431-4db3-423e-9b21-e2723fdb174a`，模式 guarded，截止 **2026-09-12 14:39:10 北京时间**（UTC 06:39:10）。启动时 0 个 session、0 个 candidate、0 个真实事件，没有预填测试数据。

[activation artifact](../../artifacts/probes/20260911-daily-observation/activation.json) 的 6 项检查全部通过：runtime guarded、STOP 解除、冻结 plan 一致、10/20 配额固定、无预置事件、独立巡检已读取本 rollout。该文件是启动快照；后续状态以本机 Ledger 和 latest.json 为准。

Codex 当前线程已建立每 30 分钟的代理审核 heartbeat（ID `sagacontext`），仅管理本 rollout；只在有审核结果、异常、完成或需要用户行动时通知，没有变化保持安静。窗口关闭后停止此 heartbeat。它不会续期、重启 rollout、扩大范围或制造消费证据。独立每分钟 launchd 巡检负责停止/收尾，避免把清理依赖于模型是否可用。

质量结论必须等待真实事件；本实现和先前受控闭环不构成日常质量已达标证据。使用已接入 hooks 的 Codex 在本仓库处理实际工作即可；本次 API 会话没有产生已观测的原生 hook 事件，不把当前讨论算作采样。
