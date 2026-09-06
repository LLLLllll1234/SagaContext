# Real Judge v3 Schema And Dataset Design

**Status:** approved and implemented locally
**Baseline commit:** `a30287d`

## Scope

This change separates provider response validation from the internal semantic Delta model and creates `real-judge-v3` without changing `real-judge-v2`. It does not enable normal-session writes, a resident worker, or automatic injection.

## Wire schema

`WireDeltaV3` is the provider-facing Pydantic model. `strong_signal` and `confidence_hint` may be omitted or null. When present, `strong_signal` must be a boolean and `confidence_hint` must be a finite number in `[0, 1]`. The parser converts absent/null helper values to the internal defaults (`False`, `0.5`) while preserving the original helper values in JudgeTrace auxiliary metadata. Semantic scoring and `DeltaProposal` conversion do not read these helper values.

The response schema version is `delta-v3`; the prompt contract is `openai-judge-prompt-v4`. Validation failures retain only a safe location/detail and response digest, never the provider response body.

## Dataset v3

`bench/cases/real_judge/cases-v3.yaml` clones the v2 cases and input evidence, changes only the dataset id/schema/digest and the audited refine annotation. The v2 digest remains `254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce`; the v3 digest is `8ebf785c8c579140236521e0b8e93164741d75f9356bd18b26924e7732dc8565`.

The only refine case is `v3-smoke-refine-gotcha`. Its complete acceptable bodies include:

- `async task is already closing`
- `the async task is already closing`
- `when the async task is already closing`

All three preserve the anchored `key`, `symptom`, and `fix`. The `applies_when` annotation points to the exact candidate quote and records a semantic-equivalence explanation. Incomplete patch-like bodies remain rejected.

## Verification

- Schema contract tests cover omitted/null helper fields, strict boolean/numeric validation, finite/range checks, trace-only auxiliary metadata, and safe diagnostics.
- Dataset tests verify v2 digest stability, v3 id/schema/digest separation, pointer validation, and the audited refine variants.
- No production runtime entry was changed or enabled by this design.
