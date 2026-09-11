# G6 Lifecycle Preparation

Status: implementation/preparation requested by the user; live execution is
separately authorized against the generated plan. Normal automation stays off.

## Scope

Reuse the admitted S3 backend runner and its ephemeral user, namespace,
projection recovery, G5 checks and final cleanup. Add a separate lifecycle mode;
the historical ScriptedJudge G6 path and artifacts remain unchanged.

Preparation writes only a reviewable JSON plan. It fixes an absolute temporary
workspace and Ledger, unique backend user/namespace, four host events, model
versions, fixtures, budgets, prerequisite artifact hashes and Python source
digests. Execution requires that same plan and its digest. The digest binds the
configuration; it is not a substitute for user authorization. Changed code or
plan invalidates execution. Existing runtime/output directories are rejected.

Allowed execution effects are limited to synthetic test data and the named
temporary resources: source events, candidates, Judge calls, Ledger writes,
projections, recall and actual context injection into the next isolated session.
Each of six CLI calls has its own temporary minimal CODEX_HOME and ephemeral
session. No normal hooks or private transcript ingestion are installed.

## Three Chains

1. Control returns `{"command":null}` without memory. A source session declares
   `pytest`; a manually selected synthetic candidate is passed to the actual v6
   Judge, then BatchWorker, Ledger and Projector. A separate consuming session
   must return `{"command":"pytest"}` using the fresh recall bundle.
2. An independent source session explicitly replaces the command with
   `python -m unittest`. Judge must emit `supersede`. The previous identity is
   retired and a successor is created atomically. Mix stale and fresh hits and
   require that only the successor enters the next session; its task answer
   must use the new command.
3. The runner explicitly forgets the successor through Ledger. Before physical
   deletion finishes, pass stale hits to a new session: the hook must emit an
   empty bundle and the host must return `{"command":null}`. Verify physical
   projection deletion and rejection again after reopening the Ledger.

Fixtures and success criteria are fixed before live calls. Commands are answers
to synthetic questions; the runner does not ask the host to execute those commands.
Candidate selection is explicit fixture setup, not a claim of automatic extraction.

## Supersede Boundary

Ledger's existing `supersede` retires an identity. The Judge uses it for a
replacement body. BatchWorker must translate a replacement proposal into two
typed operations in the same commit: retire old + new successor. Both retain
the same proposal/evidence lineage. The successor ID is deterministic from the
proposal; CAS/transaction guards remain in force. A failure creating the
successor must roll back the retirement. Ledger's direct retirement API,
prompt-v6, converter and body comparator are unchanged.

## Injection Evidence

The new hook handles only SessionStart/UserPromptSubmit/Stop/SessionEnd from the
approved workspace. At SessionStart it reopens the isolated Ledger and runs
RecallPolicy on cached hits. The hook records the actual emitted bundle and a
hashed host session identity. The runner verifies four events, distinct session
identities, emitted bundle equality and task results separately. A hook receipt
alone proves emission, not consumption; only the task assertion completes that
part of the chain. Artifacts retain synthetic evidence and digests, not arbitrary
host response/error text or temporary provider/auth configuration.

## Limits And Cleanup

Host: six calls, 180 seconds each, no automatic host retry. Judge: at most three
attempts per batch, 300-second requests, 330-second worker leases. Non-retryable
schema/conversion/auth errors stop the chain. Search: at most 60 seconds. Bundle:
2000 units of existing conservative UTF-8 byte accounting. No commands or paths
are normalized to force a pass.

`STOP` beside the plan blocks execution and future host/Judge/projection steps;
the host process group is terminated during an active CLI call. An in-flight
synchronous Judge request may finish within its 300-second timeout before the
runner stops. Cleanup remains enabled after STOP. Runtime cleanup removes test
hooks, temporary homes/auth files and Ledger, including on exceptions. Existing
S3 cleanup deletes the temporary namespace and revokes its backend user; failed
cleanup makes the overall result fail. SIGKILL/machine failure cannot guarantee
finally blocks; recovery must target only the plan's exact resources.

## Verification Plan

- Run the lifecycle using actual local state machines with host/Judge/backend
  test doubles. Label the result as local verification, never G6 live evidence.
- Inject incorrect host consumption, missing G5, kill switch, scope mismatch,
  reused host session and successor-commit failure.
- Verify plan preparation makes no remote calls, rejects changed source/plan,
  and refuses execution without the matching digest before backend setup.
- Run repository tests, compileall and diff checks. Generate a prepared plan
  only after source changes are final, then request the scoped execution grant.
