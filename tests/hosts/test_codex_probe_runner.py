import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "codex_probe", Path(__file__).parents[2] / "scripts" / "probe_codex_host.py"
)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class ProbeRunnerTests(unittest.TestCase):
    def scenario(self, events, stderr, exit_code=0):
        completed = subprocess.CompletedProcess(
            [], exit_code, "\n".join(json.dumps(event) for event in events), stderr
        )
        with patch.object(PROBE.subprocess, "run", return_value=completed) as run:
            result = PROBE._scenario_result(
                name="baseline", workspace=Path("/synthetic"),
                codex_home=Path("/isolated"), timeout_seconds=10, model="gpt-5.6-sol",
            )
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.6-sol")
        return result

    def test_completed_turn_does_not_turn_warning_into_blocker(self):
        result = self.scenario([{"type": "turn.completed"}], "background authentication failed")
        self.assertIsNone(result["blocker"])

    def test_structured_model_error_takes_precedence(self):
        result = self.scenario(
            [{"type": "turn.failed", "error": {"message": "Model is not supported by any configured account"}}],
            "authentication failed", 1,
        )
        self.assertEqual(result["blocker"], "model_unavailable")

    def test_real_auth_failure_remains_blocked(self):
        result = self.scenario(
            [{"type": "turn.failed", "error": {"message": "401 Unauthorized"}}], "", 1,
        )
        self.assertEqual(result["blocker"], "model_authentication_failed")

    def test_authentication_word_alone_is_not_failure(self):
        self.assertIsNone(PROBE._classify_blocker("authentication initialized", False))

    def test_unknown_failure_does_not_count_as_completed(self):
        self.assertEqual(self.scenario([], "argument rejected", 2)["blocker"], "host_execution_failed")

    def test_missing_completed_turn_does_not_count_as_completed(self):
        self.assertEqual(self.scenario([], "")["blocker"], "host_execution_failed")

    def test_timeout_is_blocker(self):
        self.assertEqual(PROBE._classify_blocker("", True), "model_request_timed_out")
        self.assertEqual(
            PROBE._classify_blocker("background authentication failed", True),
            "model_request_timed_out",
        )

    def test_marker_detection_scans_completed_item_payload(self):
        self.assertTrue(
            PROBE._agent_received_marker(
                [{"type": "item.completed", "item": {"text": ["G3_SESSION_START_CONTEXT"]}}]
            )
        )
