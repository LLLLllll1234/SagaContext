# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T075704Z-db54b285
- Dataset: real-judge-v2 (254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce)
- Cases: 1
- Repeats: 1
- Model: gpt-5.6-sol
- Prompt contract: openai-judge-prompt-v3
- Response schema: delta-v2
- Converter: delta-to-proposal-v2
- Endpoint fingerprint: sha256:782c6aa2aefacca8bcbd5fa39f1dac5ac703307225589d08037f05e125dab689
- Request timeout: 60s
- Max attempts per observation: 1
- Token usage: unavailable
- Cost: unavailable
- Judge calls successful: 0/1
- Acceptance: blocked

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | n/a | n/a | n/a | n/a | n/a |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v2-smoke-new-decision | smoke | judge_schema_error | n/a | n/a | n/a | n/a | n/a | n/a |

## Call failures

| Repeat | Case | Error | HTTP status | Detail | Timeout phase | Latency (ms) |
|---:|---|---|---:|---|---|---:|
| 1 | v2-smoke-new-decision | judge_schema_error | - | invalid delta schema at 0.confidence_hint | unavailable | 4791 |
