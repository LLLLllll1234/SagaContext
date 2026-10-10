# Integration Guide

SagaContext V1 exposes a local, read-only console and a fail-open Codex hook adapter. Integration is opt-in and remains disabled until the configuration and operator workflow are explicitly completed.

## Install

```sh
./scripts/install.sh --with-console
```

The installer uses `uv.lock`, creates a protected local home, and keeps an existing `config.toml`. It never writes secret values. On Windows, run the PowerShell wrapper from WSL; native Windows hooks and the observation runtime are not supported in V1.

## Start and inspect

```sh
./scripts/install.sh --start
./scripts/install.sh --status
```

Open `http://127.0.0.1:37780/console/deployment` for the deployment view. The page reports configuration facts only. It does not test OpenViking, call a model, inspect the Ledger, or change rollout state. The backend configured flags are booleans; endpoint and credential values are intentionally omitted.

## Codex hooks

After configuring the exact workspace in `[rollout].workspace`, install its four hooks:

```sh
./scripts/install.sh --with-hooks --workspace /absolute/path/to/project
```

The installer preserves unrelated hook groups and settings, writes an atomic backup, and adds commands pinned to `codex-cli 0.153.4`. Re-running is idempotent. Remove only SagaContext-owned handlers with:

```sh
./scripts/install.sh --remove-hooks --workspace /absolute/path/to/project
```

The adapter reads only the hook JSON payload and never opens transcript files. Delivery failures are fail-open and do not create consumption receipts.

## OpenViking and services

Prepare the protected `OpenViking/data/ov.conf` first, then optionally start only the pinned sidecar:

```sh
./scripts/install.sh --with-openviking
```

Service registration is optional and platform-specific. Use `--install-service` on macOS or Linux after configuration checks. The generated service directly runs the daemon under launchd/systemd with `rollout.mode=off`; `--remove-service` removes only the service generated for the selected home. Test service files before enabling them in a login session.

## Stop and recovery

```sh
./scripts/install.sh --stop
./scripts/install.sh --status
```

An explicit `--start` records its PID, command identity, instance ID, and a local `setup-daemon.log`; it signals a process only when all identity checks match. A port occupied by an unrelated process is rejected. Service-managed daemons are stopped through launchd/systemd. Remove or repair a stale local process record only after inspecting it.

## Release boundary

V1 keeps `rollout.mode = "off"` and `worker_enabled = false` in generated configuration. Enabling shadow or guarded mode, selecting an approver, adding credentials, and accepting real transcript events remain separate operational decisions. The packaged console assets are included in the Python distribution, so a wheel install does not require Node to serve the UI.
