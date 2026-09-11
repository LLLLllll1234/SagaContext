# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T101729Z-13d9659d
- Dataset: real-judge-v3 (8ebf785c8c579140236521e0b8e93164741d75f9356bd18b26924e7732dc8565)
- Cases: 1
- Repeats: 3
- Model: deepseek-v4-flash
- Prompt contract: openai-judge-prompt-v4
- Response schema: delta-v3
- Converter: delta-to-proposal-v2
- Endpoint fingerprint: sha256:a34e2a4708ed1c61008a151688838dcf1c44d4e7f08054633e72ba7c0b16cfc1
- Request timeout: 60s
- Max attempts per observation: 1
- Token usage: unavailable
- Cost: unavailable
- Judge calls successful: 2/3
- Acceptance: blocked

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | n/a | n/a | n/a | n/a | n/a |
| 2 | 1/1 | 1/1 | 1/1 | 1/1 | n/a |
| 3 | 1/1 | 1/1 | 1/1 | 1/1 | n/a |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v3-smoke-confirm-convention | smoke | judge_schema_error | 2/2 | 2/2 | 2/2 | 2/2 | n/a | 2/2 |

## Call failures

| Repeat | Case | Error | HTTP status | Detail | Timeout phase | Latency (ms) |
|---:|---|---|---:|---|---|---:|
| 1 | v3-smoke-confirm-convention | judge_schema_error | 200 | invalid delta schema at 0.layer | unavailable | 3639 |
