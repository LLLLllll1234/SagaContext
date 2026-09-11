# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T061907Z-cdfcb82c
- Dataset: real-judge-v2 (254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce)
- Cases: 14
- Repeats: 3
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
| v2-smoke-new-decision | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-smoke-confirm-convention | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-smoke-refine-gotcha | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-smoke-supersede-taste | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-smoke-conflict-project-map | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-smoke-no-change | smoke | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-duplicate-exact-convention | duplicate_expression | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-duplicate-paraphrase-taste | duplicate_expression | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-reversal-explicit | preference_reversal | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-reversal-temporary | preference_reversal | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-insufficient-unknown-path | insufficient_information | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-insufficient-unverified-gotcha | insufficient_information | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-irrelevant-chatter | irrelevant_content | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-irrelevant-transient-observation | irrelevant_content | judge_configuration_error | n/a | n/a | n/a | n/a | n/a | n/a |
