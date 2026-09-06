# Real Judge Semantic Acceptance Report

> This report measures a frozen synthetic/de-identified replay set. It is not an arbitrary-session quality metric.

- Run ID: 20260906T170250Z-deepseek-v4-flash
- Dataset: real-judge-v4 (c07572973024b3b5227ee09b4fa2540c8eae6e313cc9da7cb65d714c31a05138)
- Cases: 14
- Repeats: 3
- Model: deepseek-v4-flash
- Prompt contract: openai-judge-prompt-v6
- Response schema: delta-v3
- Converter: delta-to-proposal-v2
- Body normalizer: body-normalizer-v2
- Endpoint fingerprint: sha256:a34e2a4708ed1c61008a151688838dcf1c44d4e7f08054633e72ba7c0b16cfc1
- Request timeout: 300s
- Max attempts per observation: 3
- Attempts used: 42/42 observations recorded
- Token usage: unavailable
- Cost: unavailable
- Judge calls successful: 42/42
- Acceptance: passed

## Per-repeat metrics

| Repeat | Relation | Body | Evidence | Conversion fidelity | Ignore |
|---:|---:|---:|---:|---:|---:|
| 1 | 14/14 | 8/8 | 8/8 | 14/14 | 6/6 |
| 2 | 14/14 | 8/8 | 8/8 | 14/14 | 6/6 |
| 3 | 14/14 | 8/8 | 8/8 | 14/14 | 6/6 |

## Per-case stability

| Case | Group | Status | Relation | Body | Evidence | Conversion fidelity | Ignore | Proposal semantic |
|---|---|---|---:|---:|---:|---:|---:|---:|
| v4-smoke-new-decision | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v4-smoke-confirm-convention | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v4-smoke-refine-gotcha | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v4-smoke-supersede-taste | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v4-smoke-conflict-project-map | smoke | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v4-smoke-no-change | smoke | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v4-duplicate-exact-convention | duplicate_expression | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v4-duplicate-paraphrase-taste | duplicate_expression | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v4-reversal-explicit | preference_reversal | ok | 3/3 | 3/3 | 3/3 | 3/3 | n/a | 3/3 |
| v4-reversal-temporary | preference_reversal | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v4-insufficient-unknown-path | insufficient_information | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v4-insufficient-unverified-gotcha | insufficient_information | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v4-irrelevant-chatter | irrelevant_content | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
| v4-irrelevant-transient-observation | irrelevant_content | ok | 3/3 | n/a | n/a | 3/3 | 3/3 | 3/3 |
