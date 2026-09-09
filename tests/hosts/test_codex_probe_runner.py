import importlib.util
import json
import copy
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "codex_probe", Path(__file__).parents[2] / "scripts" / "probe_codex_host.py"
)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

EVAL_SPEC = importlib.util.spec_from_file_location(
    "codex_evaluator", Path(__file__).parents[2] / "scripts/evaluate_codex_host_probe.py")
EVALUATOR = importlib.util.module_from_spec(EVAL_SPEC)
EVAL_SPEC.loader.exec_module(EVALUATOR)


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
                [{"type": "item.completed", "item": {"type": "agent_message", "text": "G3_SESSION_START_CONTEXT"}}]
            )
        )

    def test_tool_output_marker_is_not_consumption(self):
        self.assertFalse(PROBE._agent_received_marker([
            {"type": "item.completed", "item": {"type": "command_execution", "aggregated_output": "G3_SESSION_START_CONTEXT"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "MISSING"}},
        ]))

    def test_only_exact_final_message_counts(self):
        def message(text):
            return {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
        self.assertFalse(PROBE._agent_received_marker([message("Not received: G3_SESSION_START_CONTEXT")]))
        self.assertFalse(PROBE._agent_received_marker([message(PROBE.SESSION_START_CONTEXT), message("MISSING")]))
        self.assertTrue(PROBE._agent_received_marker([message("G3_RANDOM")], "G3_RANDOM"))
        self.assertFalse(PROBE._agent_received_marker([message(PROBE.SESSION_START_CONTEXT)], "G3_RANDOM"))

    def test_host_version_drift_rejected_before_runtime(self):
        with patch.object(PROBE.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "codex-cli future", "")), \
                patch.object(PROBE.tempfile, "TemporaryDirectory") as temporary:
            with self.assertRaisesRegex(ValueError, "host_version_not_pinned"):
                PROBE.run_probe(Path("/nonexistent-saga-probe/capture.json"))
            temporary.assert_not_called()

    def test_evaluator_rejects_marker_evidence_tampering(self):
        scenarios = [{"name": "no_context_control", "exit_code": 0, "blocker": None,
            "agent_received_injected_context": False,
            "consumption_evidence": {"final_message_is_missing": True}}]
        for i, name in enumerate(PROBE.SCENARIOS[1:]):
            digest = PROBE.hashlib.sha256(str(i).encode()).hexdigest()
            scenarios.append({"name": name, "exit_code": 0, "blocker": None,
                "agent_received_injected_context": True, "consumption_evidence": {
                    "expected_marker_digest": digest, "final_message_digest": digest,
                    "final_message_present": True, "marker_in_non_message_item": False}})
        capture = {"adapter_version": "g3-probe-v5", "scenarios": scenarios}
        def assertions(value):
            return {a["name"]: a["status"] for a in EVALUATOR.evaluate(value, "digest")["assertions"]}
        self.assertEqual(assertions(capture)["random_marker_final_message_evidence"], "pass")
        for change in ({"final_message_digest": "wrong"}, {"marker_in_non_message_item": True},
                       {"final_message_present": False}):
            changed = copy.deepcopy(capture)
            changed["scenarios"][1]["consumption_evidence"].update(change)
            self.assertNotEqual(assertions(changed)["random_marker_final_message_evidence"], "pass")
        changed = copy.deepcopy(capture)
        changed["scenarios"][0]["consumption_evidence"]["final_message_is_missing"] = False
        self.assertEqual(assertions(changed)["no_context_negative_control"], "fail")
