# 真实 Judge v2 语义验收记录

**日期：** 2026-09-06（Asia/Shanghai）  
**实现提交：** `c68d44b`  
**数据集：** `real-judge-v2` / `bench/cases/real_judge/cases-v2.yaml`  
**数据集 digest：** `254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce`

## 结论

第三方 OpenAI-compatible 云端中转可用于这组环境变量。本次使用进程级临时配置调用 `gpt-5.6-sol`，`/chat/completions`、Bearer 认证、`response_format=json_object`、`choices[0].message.content` 和 Delta schema 均已验证兼容；API key 未写入配置、仓库或 artifact。

真实语义验收未通过。正式三轮有 41/42 次调用成功，一次 `judge_timeout` 使总状态为 `blocked`；即使排除该基础设施失败，三轮 memory body 分别只有 `5/8`、`4/7`、`6/8`，均低于冻结的 90% 门槛。关系、证据、转换和忽略判断在所有适用且成功的调用上均正确。

## 数据审计

- 旧 `cases.yaml` 保留为 `real-judge-v1`，未改写旧 digest 和历史结果。
- v1 的 `real-conflict-project-map` 存在无输入依据的 `src/new.py`，v2 已在候选正文明确给出路径与未验证语气。
- v1 `real-no-change` 直接泄露了不写入结论，v2 改为自然的一次性请求。
- v2 含 6 个基础样本和 8 个困难样本，覆盖重复表达、偏好反转、信息不足和无关内容。
- 每个写入型标注字段都带 JSON Pointer 和 quote/结构值；加载时校验来源存在，同时保留人工语义审计边界。

## 评分契约

- relation、memory body、模型原始 Delta evidence、conversion fidelity 和 ignore 独立计分。
- `conversion_fidelity_correct` 只检查实际 Delta 到 proposal 的忠实转换；relation 错误不再必然导致转换指标失败。
- `body-schema-v1/body-normalizer-v1` 固定字段、值类型、枚举、相对路径、Unicode NFKC 和空白规则；不自动接受字段别名或模型判定的等义文本。
- 空 Delta 的 body/evidence 为 N/A；转换器自动填入的 candidate event IDs 不作为模型引用得分。
- 先按单 case 判定三轮稳定性，再报告分组与每轮全局指标；组内池化结果不能掩盖单例失败。

## 首轮实测

诊断和正式运行均固定 `temperature=0`、单 observation 最多一次 HTTP 请求、60 秒 timeout。完整 endpoint 按安全契约不写入 artifact；模型名可以记录。

命令主体：

    .venv/bin/python scripts/replay_real_judge.py --repeats 1 --timeout 60
    .venv/bin/python scripts/replay_real_judge.py --repeats 3 --timeout 60

环境变量只在运行进程中注入，未写入命令记录。

### 一轮兼容性诊断

- Artifact：`artifacts/real-judge/20260906T064739Z-f3a00255/`
- 调用成功：13/14；一例 `judge_timeout`。
- 成功调用的 relation `13/13`、evidence `7/7`、conversion fidelity `13/13`、ignore `6/6`。
- Memory body `4/7`，已证明“接口兼容”不等于“语义验收通过”。

### 三轮正式运行

- Artifact：`artifacts/real-judge/20260906T065104Z-db2e433e/`
- 模型：`gpt-5.6-sol`
- 数据集 digest：`254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce`

| 指标 | 结果 |
|---|---:|
| Case | 14 |
| 重复轮次 | 3 |
| Judge 调用成功 | 41/42 |
| `judge_timeout` | 1/42 |
| Relation | 41/41 |
| Memory body | 15/23 |
| Evidence | 23/23 |
| Conversion fidelity | 41/41 |
| Ignore | 18/18 |
| Acceptance | blocked |

三轮独立 body 结果为 `5/8`、`4/7`、`6/8`。耗时 P50 约 7.38 秒，P95 约 20.06 秒，最大值 60.21 秒；provider 响应未被采集 token 与费用字段，因此二者为 unavailable。

## 失败分析

- `v2-smoke-refine-gotcha` 只有 `1/3` 正文通过。两轮仅返回新增的 `applies_when`，没有按冻结正文契约合并 anchor 中的 `symptom` 与 `fix`。
- `v2-smoke-supersede-taste` 正文 `0/3`。模型输出 `JSON` 或 `concise JSON results`，没有保留冻结枚举 `json`。
- `v2-reversal-explicit` 正文 `0/3`。模型输出 `prose summaries`，没有保留冻结枚举 `prose`。
- `v2-duplicate-exact-convention` 第二轮在 60 秒超时；其余两轮关系与正文均正确。

这些失败不通过放宽 normalizer 或追加实测后标注来消除。下一版应在新的 prompt/schema 版本中明确 refine 输出完整合并正文，并用 provider 支持时的 JSON Schema 约束枚举；新版本必须生成独立 artifact，不能与本次结果合并。

## 产物契约修正

首轮实测发现 runner 产物虽记录 model 和 temperature，但遗漏冻结规格要求的 endpoint 指纹与 timeout。实测 artifact 保持不可变并明确保留该限制；随后实现已补充非敏感 `endpoint_fingerprint`、`request_timeout_seconds`、`max_attempts`，报告也显式标记 token usage 与 cost unavailable。指纹只保存规范化 endpoint 的 SHA-256，不保存明文 URL。

最初的无配置阻断 artifact 仍保留，用于证明缺失配置不会伪造成 `no_change`：

- `artifacts/real-judge/20260906T061907Z-cdfcb82c/replay.jsonl`
- `artifacts/real-judge/20260906T061907Z-cdfcb82c/report.md`

## 验证

- 定向契约测试：18/18 通过。
- 全量本地回归：131/131 通过。
- `python -m compileall` 与 `git diff --check` 通过。
- 敏感信息扫描未在两组新 artifact、代码或验收记录中发现 API key、Authorization header 或明文 endpoint。
- 全量回归有一条既有 Starlette/FastAPI `httpx` 弃用警告，不影响测试结果，与本改动无关。

## 待完成项

先发布新的 prompt/schema 版本修复完整正文和枚举约束，再用同一模型做独立三轮验收。`gpt-5.6-terra` 未混入本次结果；只有需要独立比较模型时才创建单独 run。完成 42/42 调用且所有冻结语义门槛通过之前，不进入自动写入阶段。
