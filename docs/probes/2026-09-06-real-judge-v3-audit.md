# Real Judge v3 Schema And Refine Audit

**Date:** 2026-09-06
**Auditor:** independent `gpt-6-astra` high-reasoning read-only audit
**Dataset:** `real-judge-v2` source cases; v2 remains frozen

## Schema audit

The shared DeepSeek failure was `0.confidence_hint`. The new wire schema treats `strong_signal` and `confidence_hint` as optional trace helpers. Missing/null values are defaulted only when constructing the internal Delta; non-boolean `strong_signal`, non-numeric `confidence_hint`, non-finite values, and values outside `[0, 1]` are rejected. Semantic scoring excludes both fields.

## Refine audit

There is exactly one refine case: `v2-smoke-refine-gotcha`.

| Item | Audited result |
|---|---|
| Expected body | `key=async-cancel`, `symptom=task hangs`, `fix=cancel task`, `applies_when=task is already closing` |
| Existing v2 acceptable body | Same complete body with `applies_when=async task is already closing` |
| Complete audited variants | `async task is already closing`; `the async task is already closing`; `when the async task is already closing` |
| Incomplete variants | Outputs omitting anchored `symptom`/`fix`; always reject because Ledger stores a replacement body |
| Semantic decision | All three complete variants are equivalent to the candidate-supported condition; inclusion is based on evidence and audit, not model frequency |

Evidence for `key`, `symptom`, and `fix` remains the anchor value pointers:

- `/batch/judge_anchors/0/payload/key` -> `async-cancel`
- `/batch/judge_anchors/0/payload/symptom` -> `task hangs`
- `/batch/judge_anchors/0/payload/fix` -> `cancel task`

Evidence for `applies_when` is the exact candidate quote at `/batch/judge_candidates/0/text`: `The known cancellation gotcha also applies when the async task is already closing.` The same quote is present in `/batch/summary`.

## v3 decision

`real-judge-v3` adds the two new complete forms (`the ...`, `when the ...`) and retains the existing article-less form. It does not broaden normalization to arbitrary paraphrases, and it does not alter the v2 file, digest, or artifacts.

## Residual risk

Phrase-level equivalence remains manually adjudicated. The scorer therefore keeps exact body matching, exact key/symptom/fix checks, exact candidate quote evidence, and a narrow v3 acceptable set. No future expression is accepted merely because a model emitted it.
