# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T065104Z-db2e433e
- Dataset: real-judge-v2 (254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce)
- Cases: 14
- Repeats: 3
- Judge calls successful: 41/42
- Acceptance: blocked

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | 14/14 | 5/8 | 8/8 | 14/14 | 6/6 |
| 2 | 13/13 | 4/7 | 7/7 | 13/13 | 6/6 |
| 3 | 14/14 | 6/8 | 8/8 | 14/14 | 6/6 |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v2-smoke-new-decision | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-smoke-confirm-convention | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-smoke-refine-gotcha | smoke | ok | 3/3 | 1/3 | 3/3 | 3/3 | n/a | 1/3 |
| v2-smoke-supersede-taste | smoke | ok | 3/3 | 0/3 | 3/3 | 3/3 | n/a | 0/3 |
| v2-smoke-conflict-project-map | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-smoke-no-change | smoke | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v2-duplicate-exact-convention | duplicate_expression | judge_timeout | 2/2 | 2/2 | 2/2 | 2/2 | n/a | 2/2 |
| v2-duplicate-paraphrase-taste | duplicate_expression | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v2-reversal-explicit | preference_reversal | ok | 3/3 | 0/3 | 3/3 | 3/3 | n/a | 0/3 |
| v2-reversal-temporary | preference_reversal | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v2-insufficient-unknown-path | insufficient_information | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v2-insufficient-unverified-gotcha | insufficient_information | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v2-irrelevant-chatter | irrelevant_content | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v2-irrelevant-transient-observation | irrelevant_content | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
