"""Dual-host rollout: frozen host mapping, digests, pinning drift and updater settings."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sagacontext.application import Application
from sagacontext.backends import InMemoryBackend
from sagacontext.config import Config
from sagacontext.hook import excluded, prepare
from sagacontext.hosts import pinning, settings as host_settings
from sagacontext.ledger import Ledger
from sagacontext.observation import finish_window, snapshot, start_window, stop_reason, tick, utcnow
from sagacontext.rollout import RolloutRuntime

CODEX, CLAUDE = "codex-cli 0.159.2", "2.1.285 (Claude Code)"
SECRET = "sk-synthetic-provider-token"


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def fake_host(home: Path, host: str, version: str, release: str) -> str:
    """A native-installer layout: entry symlink -> versioned executable printing its version."""
    target = home / "releases" / host / release
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"#!/bin/sh\necho '{version}'\n")
    target.chmod(0o755)
    link = pinning.entrypoint(host, home)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)
    return str(target.resolve())


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path.resolve()
    ledger = Ledger(root / "ledger.db", owner_id="owner")
    ledger.register_project("test", root)
    config = Config(state_path=root / "state", ledger_path=ledger.path, rollout_mode="guarded",
        rollout_workspaces=(str(root),), rollout_stop_file=root / "STOP", rollout_approver="ops",
        rollout_key_id="k1", rollout_token_digest=hashlib.sha256(b"key").hexdigest(),
        rollout_hosts=(("claude-code", CLAUDE), ("codex", CODEX)))
    yield root, ledger, config, RolloutRuntime(ledger, config)
    ledger.close()


def frozen(root):
    return {"codex": {"version": CODEX, "executable": str(root / "bin/codex")},
            "claude-code": {"version": CLAUDE, "executable": str(root / "bin/claude")}}


def auth():
    now = datetime.now(timezone.utc)
    return dict(token="key", approver="ops", key_id="k1", issued_at=stamp(now - timedelta(seconds=1)),
                expires_at=stamp(now + timedelta(minutes=3)))


def event(runtime, root, host, version, *, kind="UserPromptSubmit", session="s", key="e", **extra):
    return runtime.ingest({"hook_event_name": kind, "cwd": str(root), "host": host, "host_version": version,
                           "source_generation": "g1", "session_id": session, "source_event_ref": key, **extra})


def test_single_run_accepts_both_hosts_with_their_own_identity(runtime):
    root, ledger, config, rt = runtime
    rt.activate(mode="guarded", workspace=str(root), approval_receipt="a",
                deadline=datetime.now(timezone.utc) + timedelta(hours=1), hosts=frozen(root), **auth())
    assert event(rt, root, "codex", CODEX, session="same", key="1").status == "accepted"
    assert event(rt, root, "claude-code", CLAUDE, session="same", key="2").status == "accepted"
    rows = ledger.db.execute("SELECT host,host_version,trust_class FROM events ORDER BY host").fetchall()
    assert [tuple(r) for r in rows] == [("claude-code", CLAUDE, "claude_code_verified_receipt"),
                                        ("codex", CODEX, "codex_verified_receipt")]
    # Same host session id on two hosts is two sessions, sharing one quota.
    assert ledger.db.execute("SELECT COUNT(*) FROM rollout_sessions").fetchone()[0] == 2
    assert event(rt, root, "claude-code", "2.1.286 (Claude Code)", key="3").reason == "host_version_not_verified"
    assert event(rt, root, "cursor", "1", key="4").reason == "host_not_allowed"


def test_frozen_mapping_ignores_later_config_changes(runtime):
    root, ledger, config, rt = runtime
    rt.activate(mode="guarded", workspace=str(root), approval_receipt="a",
                deadline=datetime.now(timezone.utc) + timedelta(hours=1), hosts=frozen(root), **auth())
    changed = RolloutRuntime(ledger, replace(config, rollout_hosts=(("codex", "codex-cli 9.9.9"),)))
    assert event(changed, root, "claude-code", CLAUDE).status == "accepted"
    assert event(changed, root, "codex", "codex-cli 9.9.9", key="x").reason == "host_version_not_verified"


def test_hosts_must_match_configuration_and_join_the_digest(runtime):
    root, ledger, config, rt = runtime
    with pytest.raises(ValueError, match="hosts_not_configured"):
        rt.activate(mode="guarded", workspace=str(root), approval_receipt="a",
                    deadline=datetime.now(timezone.utc) + timedelta(hours=1),
                    hosts={"codex": frozen(root)["codex"]}, **auth())
    deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    a = auth()
    fields = {"mode": "guarded", "workspace": str(root), "deadline": stamp(deadline), "max_sessions": 10,
              "max_candidates": 20, "generation": "g1",
              "rollback_plan_digest": hashlib.sha256(json.dumps({"schema_version": "rollout-plan-v1", "resources": "rollout-scoped", "compensate_inflight": True}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
              "owner": "owner"}
    signed = {k: v for k, v in a.items() if k != "token"}
    # Key order in the caller's mapping must not change the digest.
    reordered = dict(reversed(list(frozen(root).items())))
    digest = rt.auth.request_digest("activation", {**fields, "hosts": pinning.canonical_hosts(reordered),
        "approver": "ops", "key_id": "k1", "approval_receipt": "a",
        "issued_at": signed["issued_at"], "expires_at": signed["expires_at"]})
    legacy = rt.auth.request_digest("activation", {**fields, "approver": "ops", "key_id": "k1",
        "approval_receipt": "a", "issued_at": signed["issued_at"], "expires_at": signed["expires_at"]})
    assert digest != legacy
    rt.register_grant(action="activation", target_receipt="a", request_digest=digest,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=2), receipt="g",
        auth_expires_at=a["expires_at"], **{k: v for k, v in a.items() if k != "expires_at"})
    run = rt.activate(mode="guarded", workspace=str(root), approval_receipt="a", deadline=deadline,
                      hosts=reordered, **a)
    assert run["status"] == "running"
    assert ledger.db.execute("SELECT consumed_at FROM rollout_approval_grants WHERE receipt='a'").fetchone()[0]


def test_legacy_run_keeps_single_host_behaviour(runtime):
    root, ledger, config, rt = runtime
    legacy = RolloutRuntime(ledger, replace(config, rollout_hosts=(), rollout_host_version=CODEX))
    legacy.activate(mode="guarded", workspace=str(root), approval_receipt="a",
                    deadline=datetime.now(timezone.utc) + timedelta(hours=1), **auth())
    assert ledger.db.execute("SELECT hosts_json FROM rollout_runs").fetchone()[0] is None
    assert event(legacy, root, "codex", CODEX).status == "accepted"
    assert event(legacy, root, "claude-code", CLAUDE, key="2").reason == "host_not_allowed"


@pytest.mark.parametrize("source,status", [("startup", "accepted"), ("resume", "accepted"),
                                           ("compact", "rejected"), ("clear", "rejected"), (None, "rejected")])
def test_claude_session_start_source_guard(runtime, source, status):
    root, ledger, config, rt = runtime
    rt.activate(mode="guarded", workspace=str(root), approval_receipt="a",
                deadline=datetime.now(timezone.utc) + timedelta(hours=1), hosts=frozen(root), **auth())
    extra = {} if source is None else {"source": source}
    receipt = event(rt, root, "claude-code", CLAUDE, kind="SessionStart", **extra)
    assert receipt.status == status
    if status == "rejected":
        assert receipt.reason == "session_start_source_not_allowed"


def test_codex_session_start_is_not_source_guarded(runtime):
    root, ledger, config, rt = runtime
    rt.activate(mode="guarded", workspace=str(root), approval_receipt="a",
                deadline=datetime.now(timezone.utc) + timedelta(hours=1), hosts=frozen(root), **auth())
    assert event(rt, root, "codex", CODEX, kind="SessionStart", source="compact").status == "accepted"


def test_hook_excludes_meta_sessions_and_forwards_source(tmp_path):
    assert excluded({"SKILL_EVOLUTION_REVIEW": "1"}) and excluded({"SAGACONTEXT_HOOK_DISABLE": "1"})
    assert not excluded({"SKILL_EVOLUTION_REVIEW": "0"}) and not excluded({})
    config = Config(state_path=tmp_path / "s", ledger_path=tmp_path / "l", rollout_workspaces=(str(tmp_path),))
    record = prepare({"session_id": "s", "cwd": str(tmp_path), "source": "compact"}, config, "claude-code", "SessionStart")
    assert record["source"] == "compact"
    assert "source" not in prepare({"session_id": "s", "cwd": str(tmp_path), "source": "x", "prompt": "p"},
                                   config, "claude-code", "UserPromptSubmit")


def test_hook_process_exits_before_config_when_excluded(tmp_path):
    import subprocess, sys
    env = {**os.environ, "SKILL_EVOLUTION_REVIEW": "1", "SAGACONTEXT_HOME": str(tmp_path / "missing"),
           "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run([sys.executable, "-m", "sagacontext.hook", "claude-code", "SessionStart"],
                            input="{}", env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and result.stdout.strip() == "{}"


def test_freeze_and_drift_detect_version_and_path_changes(tmp_path):
    home = tmp_path
    exe = fake_host(home, "claude-code", CLAUDE, "2.1.285")
    fake_host(home, "codex", CODEX, "0.159.2")
    hosts = pinning.freeze_hosts({"claude-code": CLAUDE, "codex": CODEX}, home)
    assert hosts["claude-code"]["executable"] == exe
    assert pinning.drift_reason(hosts, home) is None
    with pytest.raises(ValueError, match="host_version_changed"):
        pinning.freeze_hosts({"codex": "codex-cli 0.153.4"}, home)
    # An auto-update repoints the entry symlink at a new release.
    fake_host(home, "claude-code", "2.1.286 (Claude Code)", "2.1.286")
    assert pinning.drift_reason(hosts, home) == "host_version_changed"
    Path(exe).unlink()
    assert pinning.drift_reason(hosts, home) == "host_executable_missing"
    with pytest.raises(ValueError, match="host_executable_missing"):
        pinning.freeze_hosts({"codex": CODEX}, tmp_path / "empty")


def test_drift_skips_legacy_entries_without_executable(tmp_path):
    assert pinning.drift_reason({"codex": {"version": CODEX, "executable": None}}, tmp_path) is None


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize("initial", ["file_missing", "key_missing", "present"])
def test_settings_single_key_round_trip(tmp_path, host, initial):
    path = tmp_path / host_settings.AUTO_UPDATE[host][0]
    section, key = host_settings.AUTO_UPDATE[host][1]
    original_data = None
    if initial != "file_missing":
        original_data = {"env": {"ANTHROPIC_AUTH_TOKEN": SECRET}, "other": [1, 2]}
        if initial == "present":
            original_data.setdefault(section, {})[key] = "0" if host == "claude-code" else True
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(original_data))
        path.chmod(0o640)
    original = host_settings.disable(host, tmp_path)
    assert SECRET not in json.dumps(original)
    assert host_settings.disabled(host, tmp_path)
    data = json.loads(path.read_text())
    if original_data:
        assert data["other"] == [1, 2] and data["env"]["ANTHROPIC_AUTH_TOKEN"] == SECRET
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    host_settings.restore(host, original, tmp_path)
    if original_data is None:
        assert not path.exists()
    else:
        assert json.loads(path.read_text()) == original_data
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not list(path.parent.glob(".sagacontext-*"))


class Backend(InMemoryBackend):
    def close(self):
        pass


@pytest.fixture
def window(tmp_path):
    home = tmp_path / "home"
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    config = Config(state_path=tmp_path / "state", ledger_path=tmp_path / "ledger", rollout_workspaces=(str(root),),
        rollout_mode="guarded", rollout_stop_file=tmp_path / "STOP", rollout_approver="ops", rollout_key_id="k",
        rollout_token_digest=hashlib.sha256(b"test").hexdigest(), rollout_hosts=(("claude-code", CLAUDE), ("codex", CODEX)))
    config.rollout_stop_file.touch()
    fake_host(home, "claude-code", CLAUDE, "2.1.285")
    fake_host(home, "codex", CODEX, "0.159.2")
    claude = home / ".claude/settings.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": SECRET}}))
    app = Application(config)
    app.ledger.register_project("test", root)
    app.rollout_backend = app.rollout.backend = Backend()
    hosts = pinning.freeze_hosts(config.host_versions(), home)
    yield app, config, home, hosts
    app.close()


def test_window_disables_updaters_and_restores_on_close(window, monkeypatch):
    app, config, home, hosts = window
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    run = start_window(app, "test", hours=1, hosts=hosts, home=home)
    assert json.loads(app.ledger.db.execute("SELECT hosts_json FROM rollout_runs").fetchone()[0]) == hosts
    assert host_settings.disabled("claude-code", home) and host_settings.disabled("codex", home)
    record = (config.ledger_path.parent / "observations" / run["rollout_id"] / "host-settings.json").read_text()
    assert SECRET not in record
    assert snapshot(app, run["rollout_id"])["host_settings_restore"] == "pending"
    result = tick(app, lambda: "test", now=utcnow() + timedelta(hours=2))
    assert result[0]["status"] == "completed" and result[0]["host_settings_restore"] == "restored"
    assert json.loads((home / ".claude/settings.json").read_text()) == {"env": {"ANTHROPIC_AUTH_TOKEN": SECRET}}
    assert not (home / ".codex/app-server-daemon/settings.json").exists()


def test_watchdog_freezes_on_host_drift(window, monkeypatch):
    app, config, home, hosts = window
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    run = start_window(app, "test", hours=1, hosts=hosts, home=home)
    live = dict(app.ledger.db.execute("SELECT * FROM rollout_runs").fetchone())
    assert stop_reason(app, live) is None
    fake_host(home, "codex", "codex-cli 0.160.0", "0.160.0")
    assert stop_reason(app, live) == "host_version_changed"
    result = tick(app, lambda: "test")
    assert result[0]["reason"] == "host_version_changed" and config.rollout_stop_file.exists()


def test_failed_activation_restores_settings(window, monkeypatch):
    app, config, home, hosts = window
    with pytest.raises(ValueError, match="hosts_not_configured"):
        start_window(app, "test", hours=1, hosts={"codex": hosts["codex"]}, home=home)
    assert json.loads((home / ".claude/settings.json").read_text()) == {"env": {"ANTHROPIC_AUTH_TOKEN": SECRET}}
    assert not (home / ".codex/app-server-daemon/settings.json").exists()
    assert config.rollout_stop_file.exists()


def test_failed_restore_is_retried_on_later_ticks(window, monkeypatch):
    app, config, home, hosts = window
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    run = start_window(app, "test", hours=1, hosts=hosts, home=home)
    real = host_settings.restore
    monkeypatch.setattr(host_settings, "restore", lambda *a, **k: (_ for _ in ()).throw(OSError("busy")))
    result = tick(app, lambda: "test", now=utcnow() + timedelta(hours=2))
    assert result[0]["status"] == "completed" and result[0]["host_settings_restore"] == "failed"
    assert snapshot(app, run["rollout_id"])["host_settings_restore"] == "pending"
    monkeypatch.setattr(host_settings, "restore", real)
    tick(app, lambda: "test")
    assert snapshot(app, run["rollout_id"])["host_settings_restore"] == "restored"
    assert json.loads((home / ".claude/settings.json").read_text()) == {"env": {"ANTHROPIC_AUTH_TOKEN": SECRET}}
