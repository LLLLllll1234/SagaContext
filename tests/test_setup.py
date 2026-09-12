import json
from pathlib import Path
import pytest

from scripts.sagacontext_setup import check_codex_version, config_text, hook_config, install_hooks
from scripts.setup_support import merged_hooks, service_spec


def test_generated_config_starts_off_and_contains_no_secret(tmp_path):
    text = config_text(tmp_path, port=37780)
    assert 'mode = "off"' in text
    assert 'api_key' not in text or 'api_key_file' in text
    assert 'token' not in text.lower()
    assert str(tmp_path / 'STOP') in text


def test_hooks_are_atomic_and_existing_file_is_backed_up(tmp_path):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    existing = workspace / '.codex' / 'hooks.json'
    existing.parent.mkdir()
    existing.write_text('{"hooks": {"existing": true}}')
    target = install_hooks(workspace, tmp_path / 'repo')
    assert target.exists()
    assert json.loads(target.read_text())['hooks']['SessionStart']
    assert json.loads(target.with_name('hooks.json.sagacontext-backup').read_text())['hooks']['existing'] is True
    assert not target.with_suffix('.tmp').exists()


def test_dry_run_hook_install_does_not_write(tmp_path):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    target = install_hooks(workspace, tmp_path / 'repo', dry_run=True)
    assert not target.exists()


def test_hook_merge_preserves_unrelated_handlers_and_is_idempotent(tmp_path):
    workspace = tmp_path / "project"
    target = workspace / ".codex" / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"settings": {"timeout": 3}, "hooks": {
        "SessionStart": [{"matcher": "resume", "hooks": [{"type": "command", "command": "echo keep"}]}]
    }}))
    install_hooks(workspace, tmp_path / "repo with space", tmp_path / "home with space")
    first = json.loads(target.read_text())
    install_hooks(workspace, tmp_path / "repo with space", tmp_path / "home with space")
    second = json.loads(target.read_text())
    assert second == first
    assert second["settings"] == {"timeout": 3}
    handlers = second["hooks"]["SessionStart"]
    assert handlers[0]["hooks"][0]["command"] == "echo keep"
    managed = handlers[-1]["hooks"][0]["command"]
    assert "codex SessionStart" in managed
    assert "repo with space" in managed and "home with space" in managed


def test_invalid_config_fails_before_home_creation(tmp_path, monkeypatch):
    from scripts import sagacontext_setup
    root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[daemon\n")
    with pytest.raises(ValueError):
        sagacontext_setup.main(["--root", str(root), "--home", str(home)])
    assert list(home.iterdir()) == [home / "config.toml"]


def test_service_spec_is_deterministic_and_uses_absolute_exec(tmp_path):
    path, data, install, remove = service_spec(tmp_path / "repo", tmp_path / "home", platform="linux")
    assert path.name.startswith("local.sagacontext.") and path.suffix == ".service"
    text = data.decode()
    assert "ExecStart=\"" in text
    assert "-m\" \"sagacontext.daemon" in text
    assert "SAGACONTEXT_HOME" in text
    assert install[0] == ["systemctl", "--user", "daemon-reload"]
    assert remove[0][:3] == ["systemctl", "--user", "disable"]


def test_packaged_console_assets_are_present():
    root = Path(__file__).resolve().parents[1]
    static = root / "src" / "sagacontext" / "console" / "_static"
    assert (static / "index.html").is_file()
    assert any((static / "assets").glob("*.js"))
    assert any((static / "assets").glob("*.css"))


def test_codex_version_check_rejects_unpinned_host(monkeypatch):
    from scripts import sagacontext_setup
    monkeypatch.setattr(sagacontext_setup.shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(sagacontext_setup.subprocess, "run", lambda *args, **kwargs: type(
        "Result", (), {"returncode": 0, "stdout": "codex-cli 0.154.0\n"}
    )())
    with pytest.raises(ValueError, match="version mismatch"):
        check_codex_version()
