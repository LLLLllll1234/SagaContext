# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T064739Z-f3a00255
- Dataset: real-judge-v2 (254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce)
- Cases: 14
- Repeats: 1
- Judge calls successful: 13/14
- Acceptance: blocked

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | 13/13 | 4/7 | 7/7 | 13/13 | 6/6 |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v2-smoke-new-decision | smoke | ok | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v2-smoke-confirm-convention | smoke | ok | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v2-smoke-refine-gotcha | smoke | ok | 1/1 | 0/1 | 1/1 | 1/1 | n/a | 0/1 |
| v2-smoke-supersede-taste | smoke | ok | 1/1 | 0/1 | 1/1 | 1/1 | n/a | 0/1 |
| v2-smoke-conflict-project-map | smoke | ok | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v2-smoke-no-change | smoke | ok | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-duplicate-exact-convention | duplicate_expression | judge_timeout | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-duplicate-paraphrase-taste | duplicate_expression | ok | 1/1 | 1/1 | 1/1 | 1/1 | n/a | 1/1 |
| v2-reversal-explicit | preference_reversal | ok | 1/1 | 0/1 | 1/1 | 1/1 | n/a | 0/1 |
| v2-reversal-temporary | preference_reversal | ok | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-insufficient-unknown-path | insufficient_information | ok | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-insufficient-unverified-gotcha | insufficient_information | ok | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-irrelevant-chatter | irrelevant_content | ok | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
| v2-irrelevant-transient-observation | irrelevant_content | ok | 1/1 | n/a | n/a | 1/1 | 1/1 | 1/1 |
