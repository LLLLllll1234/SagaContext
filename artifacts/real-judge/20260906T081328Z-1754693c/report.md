# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T081328Z-1754693c
- Dataset: real-judge-v2 (254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce)
- Cases: 1
- Repeats: 1
- Model: deepseek-v4-flash
- Prompt contract: openai-judge-prompt-v3
- Response schema: delta-v2
- Converter: delta-to-proposal-v2
- Endpoint fingerprint: sha256:a34e2a4708ed1c61008a151688838dcf1c44d4e7f08054633e72ba7c0b16cfc1
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
| v2-smoke-confirm-convention | smoke | judge_schema_error | n/a | n/a | n/a | n/a | n/a | n/a |

## Call failures

| Repeat | Case | Error | HTTP status | Detail | Timeout phase | Latency (ms) |
|---:|---|---|---:|---|---|---:|
| 1 | v2-smoke-confirm-convention | judge_schema_error | - | invalid delta schema at 0.confidence_hint | unavailable | 6516 |
