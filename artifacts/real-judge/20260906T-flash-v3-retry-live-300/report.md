# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T102650Z-4a8e5b71
- Dataset: real-judge-v3 (8ebf785c8c579140236521e0b8e93164741d75f9356bd18b26924e7732dc8565)
- Cases: 14
- Repeats: 3
- Model: deepseek-v4-flash
- Prompt contract: openai-judge-prompt-v4
- Response schema: delta-v3
- Converter: delta-to-proposal-v2
- Endpoint fingerprint: sha256:a34e2a4708ed1c61008a151688838dcf1c44d4e7f08054633e72ba7c0b16cfc1
- Request timeout: 300s
- Max attempts per observation: 3
- Token usage: unavailable
- Cost: unavailable
- Judge calls successful: 38/42
- Acceptance: blocked

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | 13/13 | 7/7 | 7/7 | 13/13 | 6/6 |
| 2 | 13/13 | 7/7 | 7/7 | 13/13 | 6/6 |
| 3 | 12/12 | 6/6 | 6/6 | 12/12 | 6/6 |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v3-smoke-new-decision | smoke | judge_schema_error | 2/2 | 2/2 | 2/2 | 2/2 | n/a | 2/2 |
| v3-smoke-confirm-convention | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v3-smoke-refine-gotcha | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v3-smoke-supersede-taste | smoke | judge_schema_error | 2/2 | 2/2 | 2/2 | 2/2 | n/a | 2/2 |
| v3-smoke-conflict-project-map | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v3-smoke-no-change | smoke | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v3-duplicate-exact-convention | duplicate_expression | judge_schema_error | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v3-duplicate-paraphrase-taste | duplicate_expression | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v3-reversal-explicit | preference_reversal | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v3-reversal-temporary | preference_reversal | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v3-insufficient-unknown-path | insufficient_information | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v3-insufficient-unverified-gotcha | insufficient_information | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v3-irrelevant-chatter | irrelevant_content | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v3-irrelevant-transient-observation | irrelevant_content | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |

## Call failures

| Repeat | Case | Error | HTTP status | Detail | Timeout phase | Latency (ms) |
|---:|---|---|---:|---|---|---:|
| 1 | v3-smoke-new-decision | judge_schema_error | 200 | invalid delta schema at 0.layer | unavailable | 4057 |
| 2 | v3-duplicate-exact-convention | judge_schema_error | 200 | invalid delta schema at 0.layer | unavailable | 4604 |
| 3 | v3-smoke-supersede-taste | judge_schema_error | 200 | invalid delta schema at 0.layer | unavailable | 7386 |
| 3 | v3-duplicate-exact-convention | judge_schema_error | 200 | invalid delta schema at 0.layer | unavailable | 7830 |
