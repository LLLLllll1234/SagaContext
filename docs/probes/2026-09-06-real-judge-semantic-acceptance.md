# 真实 Judge v2 语义验收记录

**日期：** 2026-09-06（Asia/Shanghai）  
**实现提交：** `c68d44b`  
**数据集：** `real-judge-v2` / `bench/cases/real_judge/cases-v2.yaml`  
**数据集 digest：** `254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce`

## 结论

真实 Judge 语义验收的 v2 数据、评分和报告链路已实现，对应契约测试与全量回归均通过。当前本机未配置 Judge 的 base URL、API key 和 model，因此 14 个 case x 3 轮共 42 次观测全部为 `blocked_configuration`。

当前结果只证明数据审计、评分器、聚合器和阻断报告符合冻结契约；它不证明任何真实模型的关系判断或记忆生成质量。

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

## 本次运行

命令：

    PYTHONPATH=src .venv/bin/python scripts/replay_real_judge.py

不可覆盖 artifact：

- `artifacts/real-judge/20260906T061907Z-cdfcb82c/replay.jsonl`
- `artifacts/real-judge/20260906T061907Z-cdfcb82c/report.md`

| 指标 | 结果 |
|---|---:|
| Case | 14 |
| 重复轮次 | 3 |
| Judge 调用成功 | 0/42 |
| `blocked_configuration` | 42/42 |
| 语义指标 | N/A |
| Acceptance | blocked |

## 验证

- 定向契约测试：17/17 通过。
- 全量本地回归：130/130 通过。
- `python -m compileall` 通过；`git diff --check` 通过。
- 全量回归有一条现有 Starlette/FastAPI `httpx` 弃用警告，不影响测试结果，与本改动无关。

## 待完成项

在用户本机配置 `SAGACONTEXT_LLM_BASE_URL`、`SAGACONTEXT_LLM_API_KEY` 和 `SAGACONTEXT_LLM_MODEL` 后，使用同一 v2 digest 重跑。只有 42/42 调用成功并按冻结门槛完成分项评分，才能给出真实 Judge 语义验收结论。
