"""Frozen host identity: exact version plus the resolved executable path.

A rollout stores the mapping at activation. Checks only call the frozen absolute
path, so they never depend on PATH (launchd's default PATH lacks ~/.local/bin).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

# Entry points are the symlinks the native installers update in place.
ENTRYPOINTS = {"codex": ".local/bin/codex", "claude-code": ".local/bin/claude"}
TRUST_CLASS = {"codex": "codex_verified_receipt", "claude-code": "claude_code_verified_receipt"}


def canonical_hosts(hosts: Mapping[str, Any]) -> dict[str, dict[str, str | None]]:
    """The single normal form used for storage and request digests."""
    if not isinstance(hosts, Mapping) or not hosts:
        raise ValueError("hosts_required")
    result: dict[str, dict[str, str | None]] = {}
    for host in sorted(hosts):
        value = hosts[host]
        if host not in ENTRYPOINTS or not isinstance(value, Mapping) or set(value) != {"version", "executable"}:
            raise ValueError("invalid_hosts")
        version, executable = value["version"], value["executable"]
        if not isinstance(version, str) or not version or not (executable is None or isinstance(executable, str) and os.path.isabs(executable)):
            raise ValueError("invalid_hosts")
        result[host] = {"version": version, "executable": executable}
    return result


def run_hosts(run: Mapping[str, Any], legacy_host: str, legacy_version: str) -> dict[str, dict[str, str | None]]:
    """Runs activated before MIGRATION_6 have no mapping and no frozen executable."""
    raw = run.get("hosts_json")
    if raw:
        return canonical_hosts(json.loads(raw))
    return {legacy_host: {"version": legacy_version, "executable": None}}


def entrypoint(host: str, home: Path | None = None) -> Path:
    return (home or Path.home()) / ENTRYPOINTS[host]


def executable_version(executable: str) -> str:
    return subprocess.run([executable, "--version"], capture_output=True, text=True,
                          check=True, timeout=10, stdin=subprocess.DEVNULL).stdout.strip()


def freeze_hosts(versions: Mapping[str, str], home: Path | None = None) -> dict[str, dict[str, str | None]]:
    """Resolve each configured host now; any mismatch refuses activation."""
    frozen = {}
    for host, version in versions.items():
        if host not in ENTRYPOINTS:
            raise ValueError("host_not_supported")
        path = entrypoint(host, home)
        if not path.exists():
            raise ValueError("host_executable_missing")
        executable = str(path.resolve())
        try:
            observed = executable_version(executable)
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("host_executable_missing") from error
        if observed != version:
            raise ValueError("host_version_changed")
        frozen[host] = {"version": version, "executable": executable}
    return canonical_hosts(frozen)


def drift_reason(hosts: Mapping[str, Mapping[str, str | None]], home: Path | None = None) -> str | None:
    """Watchdog check for every frozen host; legacy entries without a path are skipped."""
    for host, value in hosts.items():
        executable = value["executable"]
        if executable is None:
            continue
        if not os.access(executable, os.X_OK):
            return "host_executable_missing"
        path = entrypoint(host, home)
        if not path.exists() or str(path.resolve()) != executable:
            return "host_version_changed"
        try:
            if executable_version(executable) != value["version"]:
                return "host_version_changed"
        except (OSError, subprocess.SubprocessError):
            return "host_executable_missing"
    return None


__all__ = ["ENTRYPOINTS", "TRUST_CLASS", "canonical_hosts", "drift_reason", "entrypoint",
           "executable_version", "freeze_hosts", "run_hosts"]
