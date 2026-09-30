import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROBE = load("claude_probe", "scripts/probe_claude_host.py")
EVAL = load("claude_eval", "scripts/evaluate_claude_host_probe.py")


def stream(final, *, error=False, session="s-1", api_error=None):
    return "\n".join(json.dumps(e) for e in (
        {"type": "system", "subtype": "hook_response", "hook_event": "SessionStart", "outcome": "success"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": final}]}},
        {"type": "result", "subtype": "success", "is_error": error, "result": final, "session_id": session,
         "api_error_status": api_error},
    ))


def run(stdout, code=0, stderr=""):
    with patch.object(PROBE.subprocess, "run", return_value=subprocess.CompletedProcess([], code, stdout, stderr)) as mocked:
        result = PROBE._run_claude(workspace=Path("/w"), env={}, timeout_seconds=5, model="sonnet")
    return result, mocked.call_args


def test_command_isolates_settings_and_uses_frozen_path():
    result, call = run(stream("OK"))
    command = call.args[0]
    assert command[0] == PROBE._executable() and command[0].startswith("/")
    assert command[command.index("--setting-sources") + 1] == "project,local"
    assert "--include-hook-events" in command and call.kwargs["stdin"] is subprocess.DEVNULL


def test_marker_consumption_requires_exact_final_message():
    marker = "G3_ABC"
    ok, _ = run(stream(marker))
    assert PROBE._scenario_result("baseline", ok, marker)["agent_received_injected_context"] is True
    near, _ = run(stream(f"The marker is {marker}"))
    assert PROBE._scenario_result("baseline", near, marker)["agent_received_injected_context"] is False
    control, _ = run(stream("MISSING"))
    assert PROBE._scenario_result("control", control, "")["consumption_evidence"]["final_message_is_missing"] is True


def test_api_errors_classify_as_environment_blockers():
    auth, _ = run(stream("Invalid API key", error=True, api_error=401), code=1)
    assert PROBE._scenario_result("x", auth, "M")["blocker"] == "model_authentication_failed"
    other, _ = run(stream("", error=True, api_error=529), code=1)
    assert PROBE._scenario_result("x", other, "M")["blocker"] == "model_request_failed"
    crash, _ = run("", code=2)
    assert PROBE._scenario_result("x", crash, "M")["blocker"] == "host_execution_failed"


def test_child_env_passes_provider_variables_without_recording_values(tmp_path, monkeypatch):
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "sk-secret", "OTHER": "x"}}))
    monkeypatch.setattr(PROBE.Path, "home", classmethod(lambda cls: tmp_path))
    env, runtime = PROBE._child_env(tmp_path / "cfg", meta_flag=True)
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-secret" and "OTHER" not in env
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg") and env["SKILL_EVOLUTION_REVIEW"] == "1"
    assert "sk-secret" not in json.dumps(runtime) and runtime["provider_variable_count"] == 1


def capture(**overrides):
    marker_digests = [f"{i}" * 64 for i in range(4)]
    base = {
        "probe_id": "p", "executable_version": "v", "pinned_executable_version": "v",
        "executable_path": "/home/.local/share/claude/versions/v", "config_fingerprint": "sha256:" + "a" * 64,
        "payload_policy": "synthetic_only_redacted_receipts",
        "model_runtime": {"mode": "temporary_claude_config_dir", "user_settings_loaded": False,
                          "provider_value_saved": False, "auth_material_saved": False},
        "cleanup": {"global_config_modified": False, "private_transcript_read": False, "temporary_root_removed": True},
        "verified_events": [{"event": e, "status": "observed"} for e in EVAL.REQUIRED_EVENTS],
        "probe_result": {"status": "completed"},
        "hook_env_inherited": True,
        "session_start_sources": {"observed_sources": ["resume", "startup"], "resume_keeps_session_id": True},
        "interactive_smoke": {"status": "pass"},
        "payload_shapes": [
            {"hook_event_name": "UserPromptSubmit", "session_ref": "s", "workspace_ref": "w", "task_ref": "t",
             "receipt_status": "written_before_handler_action"},
            {"hook_event_name": "PreToolUse", "session_ref": "s", "workspace_ref": "w", "task_ref": "t", "tool_call_ref": "c",
             "exit_class": "scheduled_nonzero_7", "timeout_class": "configured_timeout_target", "duplicate_group_ref": "d",
             "receipt_status": "written_before_handler_action"},
            {"hook_event_name": "PostToolUse", "session_ref": "s", "workspace_ref": "w", "task_ref": "t", "tool_call_ref": "c",
             "duplicate_group_ref": "d", "receipt_status": "written_before_handler_action"},
        ],
        "scenarios": [{"name": "no_context_control", "exit_code": 0, "blocker": None,
                       "consumption_evidence": {"final_message_is_missing": True}}] + [
            {"name": n, "exit_code": 0, "blocker": None, "agent_received_injected_context": True,
             "consumption_evidence": {"expected_marker_digest": d, "marker_in_non_message_item": False},
             "event_counts": {"PostToolUse": 1}, "resumed_from_baseline": n == "restart_recovery",
             "session_id_continuous": n == "restart_recovery", "session_start_sources": ["resume"]}
            for n, d in zip(EVAL.POSITIVE, marker_digests)],
    }
    base.update(overrides)
    return base


def test_complete_capture_passes():
    result = EVAL.evaluate(capture(), "sha256:x")
    assert result["status"] == "passed", [a for a in result["assertions"] if a["status"] != "pass"]


def test_missing_interactive_smoke_is_not_passed():
    assert EVAL.evaluate(capture(interactive_smoke={"status": "not_run"}), "d")["status"] == "inconclusive"


def test_env_not_inherited_fails_contract():
    assert EVAL.evaluate(capture(hook_env_inherited=False), "d")["status"] == "failed_contract"


def test_saved_raw_fields_fail_redaction():
    leaked = capture()
    leaked["payload_shapes"][0]["prompt"] = "raw"
    result = EVAL.evaluate(leaked, "d")
    assert result["status"] == "failed_contract"
    assert next(a for a in result["assertions"] if a["name"] == "redacted_receipts")["status"] == "fail"
