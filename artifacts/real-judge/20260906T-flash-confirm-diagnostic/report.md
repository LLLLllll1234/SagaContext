# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T130001Z-c81fe6a5
- Dataset: real-judge-v3 (8ebf785c8c579140236521e0b8e93164741d75f9356bd18b26924e7732dc8565)
- Cases: 1
- Repeats: 5
- Model: deepseek-v4-flash
- Prompt contract: openai-judge-prompt-v4
- Response schema: delta-v3
- Converter: delta-to-proposal-v2
- Endpoint fingerprint: sha256:a34e2a4708ed1c61008a151688838dcf1c44d4e7f08054633e72ba7c0b16cfc1
- Request timeout: 60s
- Max attempts per observation: 1
- Token usage: unavailable
- Cost: unavailable
- Judge calls successful: 3/5
- Acceptance: blocked

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | n/a | n/a | n/a | n/a | n/a |
| 2 | 1/1 | 1/1 | 1/1 | 1/1 | n/a |
| 3 | n/a | n/a | n/a | n/a | n/a |
| 4 | 1/1 | 1/1 | 1/1 | 1/1 | n/a |
| 5 | 1/1 | 1/1 | 1/1 | 1/1 | n/a |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v3-smoke-confirm-convention | smoke | judge_error | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |

## Call failures

| Repeat | Case | Error | HTTP status | Detail | Timeout phase | Latency (ms) |
|---:|---|---|---:|---|---|---:|
| 1 | v3-smoke-confirm-convention | judge_error | - | AttributeError | unavailable | 3405 |
| 3 | v3-smoke-confirm-convention | judge_error | - | AttributeError | unavailable | 3341 |
