# 真实 Judge 固定回放报告

**日期：** 2026-09-06（Asia/Shanghai）
**实现提交：** `d93ff54`
**数据集：** `bench/cases/real_judge/cases.yaml`
**数据集 digest：** `0c3e3d20afadfb64b681435196277212e26eeaa7611fd40d7a22f360aba1dab8`

## 结论

真实 Judge 适配器、同步 `ProposalJudge` 门面、错误分类传递、Delta 转换和固定回放 runner 已实现。当前工作区没有配置 `SAGACONTEXT_LLM_BASE_URL`、`SAGACONTEXT_LLM_API_KEY`、`SAGACONTEXT_LLM_MODEL`，因此本次回放的外部调用状态为 `blocked_configuration`，没有伪造模型语义结果。

这份报告证明 runner 能在配置缺失时逐例保留阻断原因；它不证明六类 relation 的模型判断质量，也不代表任意正常对话已经自动抽取。

## 固定样本

数据集固定 6 个单候选 batch，分别覆盖：

| Case | 关系 |
|---|---|
| `real-new-decision` | `new` |
| `real-confirm-convention` | `confirm` |
| `real-refine-gotcha` | `refine` |
| `real-supersede-taste` | `supersede` |
| `real-conflict-project-map` | `conflict` |
| `real-no-change` | `no_change` |

每例冻结候选、事件 ID、scope、anchor memory/revision、预期 payload 和 evidence IDs。`scope` 与 revision 不由模型提供；模型只返回 relation、key、fields 和 evidence 引用。

## 本次运行

命令：

    PYTHONPATH=src .venv/bin/python scripts/replay_real_judge.py \
      --jsonl /tmp/real-judge-replay.jsonl \
      --report /tmp/real-judge-report.md

结果：

| 指标 | 结果 |
|---|---:|
| Case 数 | 6 |
| Judge 调用成功 | 0/6 |
| `blocked_configuration` | 6/6 |
| Relation accuracy | 未计算（0/6，配置阻断） |
| Conversion accuracy | 未计算（0/6，配置阻断） |

每条 JSONL 结果保留 `case_digest`、Judge/prompt/schema/converter 版本、模型配置摘要、采样参数、耗时、状态和 `judge_configuration_error`。本次没有写入凭据、Authorization header、内部 URL 或私人正文。

## 已验证实现

- `OpenAIJudge` 对 timeout、429、408/5xx、401/403、配置、响应和 schema 错误分类；合法 `{"deltas": []}` 与空/坏响应分离。
- `OpenAIProposalJudge` 只在无运行中事件循环的同步线程使用 `asyncio.run`，每次 HTTP 调用创建独立异步客户端生命周期。
- `convert_deltas` 强制 candidate、memory type、anchor revision、scope、event evidence 和 operation 关联；非法或部分结果整批阻断。
- `BatchWorker` 读取 `JudgeError.retryable/class_name`：暂时性错误进入有限重试，认证/配置/响应/schema/转换/事件循环错误直接阻断，并在 Ledger `last_error_class` 保留具体分类。
- 本地测试：`123` 项全部通过。

## 下一次运行

配置 OpenAI-compatible endpoint 后，使用同一份冻结数据集重新运行脚本。只有真实 endpoint 返回并通过 schema/转换的结果才会进入 relation 与 conversion 计分；失败 case 保留，不会被成功率替代。
