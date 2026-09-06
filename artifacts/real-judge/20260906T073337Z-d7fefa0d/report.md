# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T073337Z-d7fefa0d
- Dataset: real-judge-v2 (254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce)
- Cases: 14
- Repeats: 3
- Model: deepseek-v4-pro
- Prompt contract: openai-judge-prompt-v3
- Response schema: delta-v2
- Converter: delta-to-proposal-v2
- Endpoint fingerprint: sha256:a34e2a4708ed1c61008a151688838dcf1c44d4e7f08054633e72ba7c0b16cfc1
- Request timeout: 60s
- Max attempts per observation: 1
- Token usage: unavailable
- Cost: unavailable
- Judge calls successful: 11/42
- Acceptance: blocked

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | 10/10 | 4/4 | 4/4 | 10/10 | 6/6 |
| 2 | 1/1 | 1/1 | 1/1 | 1/1 | n/a |
| 3 | n/a | n/a | n/a | n/a | n/a |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v2-smoke-new-decision | smoke | judge_schema_error,judge_service_unavailable | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v2-smoke-confirm-convention | smoke | judge_schema_error,judge_service_unavailable | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-smoke-refine-gotcha | smoke | judge_service_unavailable | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v2-smoke-supersede-taste | smoke | judge_service_unavailable | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v2-smoke-conflict-project-map | smoke | judge_service_unavailable | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v2-smoke-no-change | smoke | judge_service_unavailable | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-duplicate-exact-convention | duplicate_expression | judge_schema_error,judge_service_unavailable | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-duplicate-paraphrase-taste | duplicate_expression | judge_service_unavailable | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v2-reversal-explicit | preference_reversal | judge_schema_error,judge_service_unavailable | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-reversal-temporary | preference_reversal | judge_service_unavailable | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-insufficient-unknown-path | insufficient_information | judge_service_unavailable | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-insufficient-unverified-gotcha | insufficient_information | judge_service_unavailable | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-irrelevant-chatter | irrelevant_content | judge_service_unavailable | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-irrelevant-transient-observation | irrelevant_content | judge_service_unavailable | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |

## Call failures

| Repeat | Case | Error | Timeout phase | Latency (ms) |
|---:|---|---|---|---:|
| 1 | v2-smoke-new-decision | judge_schema_error | unavailable | 17459 |
| 1 | v2-smoke-confirm-convention | judge_schema_error | unavailable | 8309 |
| 1 | v2-duplicate-exact-convention | judge_schema_error | unavailable | 63347 |
| 1 | v2-reversal-explicit | judge_schema_error | unavailable | 14909 |
| 2 | v2-smoke-confirm-convention | judge_service_unavailable | unavailable | 30691 |
| 2 | v2-smoke-refine-gotcha | judge_service_unavailable | unavailable | 10 |
| 2 | v2-smoke-supersede-taste | judge_service_unavailable | unavailable | 8 |
| 2 | v2-smoke-conflict-project-map | judge_service_unavailable | unavailable | 6 |
| 2 | v2-smoke-no-change | judge_service_unavailable | unavailable | 7 |
| 2 | v2-duplicate-exact-convention | judge_service_unavailable | unavailable | 7 |
| 2 | v2-duplicate-paraphrase-taste | judge_service_unavailable | unavailable | 7 |
| 2 | v2-reversal-explicit | judge_service_unavailable | unavailable | 8 |
| 2 | v2-reversal-temporary | judge_service_unavailable | unavailable | 21 |
| 2 | v2-insufficient-unknown-path | judge_service_unavailable | unavailable | 11 |
| 2 | v2-insufficient-unverified-gotcha | judge_service_unavailable | unavailable | 7 |
| 2 | v2-irrelevant-chatter | judge_service_unavailable | unavailable | 6 |
| 2 | v2-irrelevant-transient-observation | judge_service_unavailable | unavailable | 6 |
| 3 | v2-smoke-new-decision | judge_service_unavailable | unavailable | 6 |
| 3 | v2-smoke-confirm-convention | judge_service_unavailable | unavailable | 7 |
| 3 | v2-smoke-refine-gotcha | judge_service_unavailable | unavailable | 6 |
| 3 | v2-smoke-supersede-taste | judge_service_unavailable | unavailable | 6 |
| 3 | v2-smoke-conflict-project-map | judge_service_unavailable | unavailable | 6 |
| 3 | v2-smoke-no-change | judge_service_unavailable | unavailable | 26 |
| 3 | v2-duplicate-exact-convention | judge_service_unavailable | unavailable | 7 |
| 3 | v2-duplicate-paraphrase-taste | judge_service_unavailable | unavailable | 6 |
| 3 | v2-reversal-explicit | judge_service_unavailable | unavailable | 6 |
| 3 | v2-reversal-temporary | judge_service_unavailable | unavailable | 6 |
| 3 | v2-insufficient-unknown-path | judge_service_unavailable | unavailable | 5 |
| 3 | v2-insufficient-unverified-gotcha | judge_service_unavailable | unavailable | 6 |
| 3 | v2-irrelevant-chatter | judge_service_unavailable | unavailable | 5 |
| 3 | v2-irrelevant-transient-observation | judge_service_unavailable | unavailable | 6 |
