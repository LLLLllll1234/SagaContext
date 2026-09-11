# Judge Contract Repair Verification

Date: 2026-09-07. Implementation starts at commit `27e6afd`.

## Result

Local implementation and regression verification passed. No new provider
acceptance or authorized shadow was performed in this run: the current
SagaContext environment/configuration has neither an LLM base URL nor API key.
The historical Pro shadow remains blocked. Prompt changes reduce type confusion
but cannot establish provider reliability without a fresh run.

## Changes

- `openai-judge-prompt-v5` distinguishes memory type from relation and explicitly
  requires `type=project_map, relation=conflict` for project-map conflicts.
- Converter `delta-to-proposal-v2` still rejects invalid types without retry,
  repair or `no_change` fallback; raw erroneous Deltas remain available.
- Dataset `real-judge-v4` freezes `body-normalizer-v2` and the field-scoped
  `condition-clause-v1` comparator. Digest:
  `c07572973024b3b5227ee09b4fa2540c8eae6e313cc9da7cb65d714c31a05138`.
- Reference-based grammar comparison accepts optional `when`, `the` and `is`
  in `gotcha.applies_when`. Subject and predicate words remain fixed. Existing
  complete-body and other-field comparisons still apply. Original inputs and
  historical v2/v3 datasets/artifacts remain unchanged.
- Current admission verifies case/repeat identity, versions, case digests,
  scoring metadata, attempts and independently recomputed conversion/scores.
  Reports use the same 42/42 gate; blocked CLI runs return a nonzero exit code.
- Audit separately reports raw digest equality and validated field-rule
  equivalence. Equal malformed/incorrect outputs do not count as equivalent.

## Evidence

The regression test reads the two actual Pro refine failures from
`20260906T154914Z-deepseek-authorized-shadow/deepseek-v4-pro/replay.jsonl`.
Both pass v4 field scoring and still fail v3 frozen scoring. This is an offline
comparison-rule regression test, not a new model result or relabelled artifact.

Tests also cover all eight grammatical combinations, changes in negation,
aspect/state, modality, subject and condition scope, omitted/changed anchor
facts, unsupported annotations, wrong normalizer versions, exact commands,
conflict type failure without retry, missing scores, mixed runs/models,
tampered outputs with forged scores, and zero-write shadow cleanup.

Validation: 160 repository tests passed, including 47 Judge-focused tests;
`compileall` and `git diff --check` passed. One existing Starlette/httpx
deprecation warning remains unrelated to this change.

## Outstanding Live Verification

After configuring the previously authorized LLM provider locally, use the v4
defaults in `scripts/replay_real_judge.py` for independent Pro/Flash acceptance
and `scripts/run_real_judge_shadow.py` for isolated shadow. Use fresh output
directories; do not combine runs or discard failures. Timeout remains 300s and
maximum attempts remains three, with retries only for existing transient errors.

Each model must independently pass all 42 observations. The shadow runner now
writes its cross-model audit automatically for the default model pair. Formal
memory writes, recall and automatic injection remain disabled; this repair does
not enable them.
