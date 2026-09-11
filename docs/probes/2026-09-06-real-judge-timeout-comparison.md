# 真实 Judge timeout 独立运行对比

**日期：** 2026-09-06（Asia/Shanghai）
**模型：** `gpt-5.6-sol`
**数据集：** `real-judge-v2` / `254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce`
**结论：** 120 秒独立 run 正式通过

## 结论

在保持模型、数据集、prompt、response schema、converter、`temperature=0` 和单 observation 一次请求不变的前提下，分别执行了 60、90、120 秒 timeout 的独立三轮 run。每个 run 都单独保留 42 个 observation，没有跨 run 合并或补跑失败样本。

120 秒 run 达到冻结的正式 Judge 验收门槛：调用 `42/42` 成功，relation、memory body、evidence、conversion fidelity 和 ignore 全部满分，Acceptance 为 `passed`。

这证明这一次固定配置的独立 run 通过，不证明 120 秒必然比 90 秒更快或更可靠。每个 timeout 只有一个 42-observation run，provider 与模型延迟存在运行间波动；120 秒 run 的较低 P95 不能单独归因于 timeout 参数。

## 前置单 case 诊断

先用 60 秒 timeout 对上一轮超时且涉及正文的 `v2-smoke-new-decision` 单独调用一次：

- Run ID：`20260906T075704Z-db54b285`
- 延迟：4791 ms
- 结果：`judge_schema_error`
- Validation path：`0.confidence_hint`

该请求没有超时，但模型返回的 `confidence_hint` 不符合 Delta schema。单 case 结果只用于定位，不计入任何三轮正式 run。

## 独立运行结果

| Timeout | Run ID | 调用成功 | Timeout | 全体 P95 | 成功调用 P95 | 最大延迟 | 其他错误 | Body | Acceptance |
|---:|---|---:|---:|---:|---:|---:|---|---:|---|
| 60s | `20260906T071912Z-2d9d7b94` | 40/42 | 2/42 (4.76%) | 53239 ms | 45909 ms | 60804 ms | 0 | 23/23 | `blocked` |
| 90s | `20260906T075747Z-d2dc4629` | 40/42 | 0/42 (0%) | 43190 ms | 43870 ms | 47537 ms | 2 conversion | 21/22 | `blocked` |
| 120s | `20260906T080509Z-1fd29f78` | 42/42 | 0/42 (0%) | 21882 ms | 21882 ms | 25646 ms | 0 | 24/24 | `passed` |

90 秒 run 的两次失败不是超时：一次 Delta 引用了未知 candidate，另一次引用了冻结上下文中不存在的 anchor。该 run 另有一次 refine 自由文本未命中冻结 acceptable body，因此即使没有 timeout 也不能通过。

120 秒 run 的三轮均为：relation `14/14`、body `8/8`、evidence `8/8`、conversion fidelity `14/14`、ignore `6/6`。14 个 case 的三轮稳定性门槛也全部满足。

## Artifact

- 单 case：`artifacts/real-judge/20260906T075704Z-db54b285/`
- 60 秒：`artifacts/real-judge/20260906T071912Z-2d9d7b94/`
- 90 秒：`artifacts/real-judge/20260906T075747Z-d2dc4629/`
- 120 秒：`artifacts/real-judge/20260906T080509Z-1fd29f78/`

三个正式 JSONL 各为 42 行、每个 repeat 各 14 条；模型、数据集 digest、prompt 和 converter 在各 run 内一致。完整 endpoint、认证信息和异常正文未写入产物。

## 启用边界

Judge 正式语义门槛已由 120 秒独立 run 满足，但这不自动证明自然事件到候选、anchor 召回、正常会话误写入防护或常驻运行可靠性。正常会话自动写入、常驻 worker 和自动注入继续保持关闭，后续启用需经过各自的受控入口与闭环验收。
