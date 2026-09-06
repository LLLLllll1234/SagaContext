# Judge Contract And Semantic Gate Repair

Status: approved by the user on 2026-09-07; implementation in progress.

## Problem

The authorized v3 shadow rejected two complete gotcha refinements solely because
`applies_when` used grammatical omissions outside the finite body allowlist.
A separate Pro observation confused relation `conflict` with memory type
`project_map`. Reports also use a historical percentage gate that is weaker than
the 42-observation admission gate.

## Frozen Contract

- Prompt: `openai-judge-prompt-v5`; response schema remains `delta-v3`.
- Converter remains `delta-to-proposal-v2`: wrong types remain non-retryable errors.
- Dataset: `real-judge-v4`; body normalizer: `body-normalizer-v2`.
- Retain all v2/v3 datasets and historical artifacts without modification.

## Field Comparison

Only explicitly annotated `gotcha.applies_when` fields may use
`condition-clause-v1`. Its reference is an evidence-backed clause of the form
`subject is predicate`. It admits optional leading `when`, optional leading
`the`, and optional copula `is`, keeping the entire subject and predicate intact.
Matching is anchored to the whole field after existing NFKC/whitespace handling.
There is no word similarity threshold, LLM evaluator, global stop-word deletion,
or output rewriting. Existing full-body requirements and exact comparison of
all other fields remain in effect. Negation, aspect, state, extra qualifiers,
commands, paths and enums do not receive these equivalences.

The v4 dataset freezes the reference, field rule and evidence before new model
calls. Grammar variants are tested as a family; previous false negatives and
adversarial semantic changes are regression cases. Old datasets keep v1 scoring.

## Type Contract

Prompt v5 explicitly separates operation/relation from memory type, lists valid
types and gives a conflict example with unchanged candidate type. The converter
continues to reject mismatches, preserving the raw Delta in its diagnostic trace;
no repair, no retry and no conversion to `no_change` is added.

## Admission And Evidence

The current gate validates the exact case/repeat matrix, frozen contract and
scoring versions, case digests, independent run/model identity, applicable score
presence and recomputed scores. Missing scores are failures, not N/A passes.
Legacy v3 admission is available only when explicitly selected. Current reports
and runner exit status use the same gate. Cross-model audit distinguishes raw
digest differences from equivalence under the frozen comparison rules.

## Validation

1. Test grammar closure, semantic counterexamples, complete-body preservation,
   immutable legacy digests, conflict rejection and adversarial admission data.
2. Run all repository tests, compileall and diff checks.
3. With the previously authorized configuration available, run independent
   Pro/Flash 14-case, three-repeat acceptance and then isolated shadow; preserve
   every attempt and use only existing bounded transient-error retries.
4. Record fresh artifacts and audit. Every shadow must keep temporary Ledger
   counts at zero and clean up resources. Formal write, recall and injection
   stay disabled. Missing configuration is reported as an outstanding live
   verification requirement, never represented as model acceptance.
