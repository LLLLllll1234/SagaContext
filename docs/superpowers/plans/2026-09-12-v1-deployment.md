# V1 Deployment Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to execute the approved implementation. Publication to main is already authorized.

**Goal:** Ship the read-only workspace console and reversible source-checkout installer on remote main as V1.

**Architecture:** Keep the runtime's authorization and ingestion boundaries intact. A standard-library setup entrypoint orchestrates dependency installation, protected configuration, owned hooks and local services; a typed GET endpoint describes deployment configuration without network probes or domain mutations.

**Tech Stack:** Python 3.11+, uv, FastAPI, React/TypeScript, pytest, Vitest, Playwright, launchd/systemd user services.

**Spec:** User-approved deployment scope, captured below.

## Constraints

- New configuration uses loopback, mode off and worker disabled. Existing configuration is preserved. Installer-managed startup requires these settings.
- No secret values, internal endpoints, transcript content or absolute user paths in the deployment DTO.
- Dependencies use uv.lock. Packaged console assets work without Node; rebuilding is explicit.
- Hooks preserve unrelated handlers and settings, use Codex 0.153.4 and quoted commands, and are reversible.
- Dry runs do not create files or run modifying commands. Invalid inputs fail before mutation.
- Native Windows runtime is unsupported; the PowerShell wrapper delegates explicitly to WSL.
- Tests use isolated temporary homes/projects; no real hooks, external services or operator approvals are changed.
- Preserve the existing v1.0.0 tag. Push verified commits to main without force.

## Task 1: Installer And Lifecycle

Files: scripts/sagacontext_setup.py, scripts/setup_support.py, scripts/install.sh, scripts/install.ps1, tests/test_setup.py.

- [ ] Validate platform, paths, config, uv, optional Node/Docker/Codex, port and mutually exclusive operations before changes.
- [ ] Use atomic protected writes and backups; merge owned hooks; support removal.
- [ ] Install locked dependencies and optionally rebuild the console or start only the pinned sidecar.
- [ ] Start/status/stop only an identified process; provide user service install/removal with deterministic ownership.
- [ ] Verify dry-run, repeat installation, quoting, malformed state, failure cleanup and process lifecycle in temporary directories.

## Task 2: Deployment View

Files: src/sagacontext/console/deployment.py, router.py, models.py, static.py, daemon.py, web/src/pages/DeploymentPage.tsx, App.tsx, API types, tests/console, web tests.

- [ ] Add GET /console/v1/deployment under existing loopback/same-origin policy, independent of Ledger reads.
- [ ] Expose version, instance identity, mode, worker/scheduler, STOP, configured dependencies and packaged console availability; reachability remains unchecked.
- [ ] Add a responsive read-only page and navigation reachable without a workspace.
- [ ] Verify no mutation/network/domain evaluation, typed API and empty/normal/stale page states.

## Task 3: Documentation And Release

Files: README.md, docs/README.md, docs/deployment.md, docs/integration.md, .gitignore, packaged console assets, release acceptance record.

- [ ] Document install/start/stop/status, hooks, services, upgrade, recovery and integration boundaries with executable commands.
- [ ] Build and include console assets, verify wheel/sdist packaging and isolated install without Node.
- [ ] Run full Python/frontend regression and browser checks at desktop/mobile sizes.
- [ ] Review the final diff and fix confirmed findings.
- [ ] Commit scoped changes, integrate remote drift, fast-forward main, push and verify remote SHA.

## Decisions And Evidence

- Baseline merge 4e01eab includes the console and preserves main's observation/runtime changes; baseline regression: 322 passed, 96 subtests.
- Existing remote v1.0.0 remains at 19b3f631f03d770aba91fc1304ba3056d522df85; main advances within V1.
- Unrelated artifacts in the original checkout are not release source. All other inspected branches have no commits missing from origin/main.
- Sidecar startup and user service registration are opt-in; service templates/commands are tested in isolation, not installed into the developer's login session.
