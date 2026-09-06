# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T071912Z-2d9d7b94
- Dataset: real-judge-v2 (254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce)
- Cases: 14
- Repeats: 3
- Model: gpt-5.6-sol
- Prompt contract: openai-judge-prompt-v3
- Response schema: delta-v2
- Converter: delta-to-proposal-v2
- Endpoint fingerprint: sha256:782c6aa2aefacca8bcbd5fa39f1dac5ac703307225589d08037f05e125dab689
- Request timeout: 60s
- Max attempts per observation: 1
- Token usage: unavailable
- Cost: unavailable
- Judge calls successful: 40/42
- Acceptance: blocked

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | 12/12 | 7/7 | 7/7 | 12/12 | 5/5 |
| 2 | 14/14 | 8/8 | 8/8 | 14/14 | 6/6 |
| 3 | 14/14 | 8/8 | 8/8 | 14/14 | 6/6 |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v2-smoke-new-decision | smoke | judge_timeout | 2/2 | 2/2 | 2/2 | 2/2 | n/a | 2/2 |
| v2-smoke-confirm-convention | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-smoke-refine-gotcha | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-smoke-supersede-taste | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-smoke-conflict-project-map | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-smoke-no-change | smoke | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v2-duplicate-exact-convention | duplicate_expression | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-duplicate-paraphrase-taste | duplicate_expression | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-reversal-explicit | preference_reversal | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-reversal-temporary | preference_reversal | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v2-insufficient-unknown-path | insufficient_information | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v2-insufficient-unverified-gotcha | insufficient_information | judge_timeout | 2/2 | n/a | n/a | 2/2 | 2/2 | 2/2 |
| v2-irrelevant-chatter | irrelevant_content | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v2-irrelevant-transient-observation | irrelevant_content | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |

## Call failures

| Repeat | Case | Error | Timeout phase | Latency (ms) |
|---:|---|---|---|---:|
| 1 | v2-smoke-new-decision | judge_timeout | read | 60804 |
| 1 | v2-insufficient-unverified-gotcha | judge_timeout | read | 60194 |
