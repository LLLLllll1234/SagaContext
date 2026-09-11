# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T154146Z-deepseek-v4-pro
- Dataset: real-judge-v3 (8ebf785c8c579140236521e0b8e93164741d75f9356bd18b26924e7732dc8565)
- Cases: 14
- Repeats: 3
- Model: deepseek-v4-pro
- Prompt contract: openai-judge-prompt-v4
- Response schema: delta-v3
- Converter: delta-to-proposal-v2
- Endpoint fingerprint: unconfigured
- Request timeout: 300s
- Max attempts per observation: 3
- Attempts used: 42/42 observations recorded
- Token usage: unavailable
- Cost: unavailable
- Judge calls successful: 0/42
- Acceptance: blocked

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | n/a | n/a | n/a | n/a | n/a |
| 2 | n/a | n/a | n/a | n/a | n/a |
| 3 | n/a | n/a | n/a | n/a | n/a |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v3-smoke-new-decision | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-smoke-confirm-convention | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-smoke-refine-gotcha | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-smoke-supersede-taste | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-smoke-conflict-project-map | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-smoke-no-change | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-duplicate-exact-convention | duplicate_expression | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-duplicate-paraphrase-taste | duplicate_expression | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-reversal-explicit | preference_reversal | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-reversal-temporary | preference_reversal | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-insufficient-unknown-path | insufficient_information | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-insufficient-unverified-gotcha | insufficient_information | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-irrelevant-chatter | irrelevant_content | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v3-irrelevant-transient-observation | irrelevant_content | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |

## Call failures

| Repeat | Case | Error | HTTP status | Detail | Timeout phase | Latency (ms) |
|---:|---|---|---:|---|---|---:|
| 1 | v3-smoke-new-decision | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-smoke-confirm-convention | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-smoke-refine-gotcha | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-smoke-supersede-taste | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-smoke-conflict-project-map | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-smoke-no-change | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-duplicate-exact-convention | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-duplicate-paraphrase-taste | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-reversal-explicit | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-reversal-temporary | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-insufficient-unknown-path | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-insufficient-unverified-gotcha | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-irrelevant-chatter | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 1 | v3-irrelevant-transient-observation | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-smoke-new-decision | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-smoke-confirm-convention | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-smoke-refine-gotcha | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-smoke-supersede-taste | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-smoke-conflict-project-map | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-smoke-no-change | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-duplicate-exact-convention | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-duplicate-paraphrase-taste | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-reversal-explicit | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-reversal-temporary | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-insufficient-unknown-path | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-insufficient-unverified-gotcha | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-irrelevant-chatter | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 2 | v3-irrelevant-transient-observation | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-smoke-new-decision | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-smoke-confirm-convention | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-smoke-refine-gotcha | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-smoke-supersede-taste | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-smoke-conflict-project-map | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-smoke-no-change | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-duplicate-exact-convention | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-duplicate-paraphrase-taste | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-reversal-explicit | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-reversal-temporary | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-insufficient-unknown-path | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-insufficient-unverified-gotcha | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-irrelevant-chatter | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
| 3 | v3-irrelevant-transient-observation | judge_configuration_error | - | missing llm configuration | unavailable | 0 |
