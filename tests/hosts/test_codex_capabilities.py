from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sagacontext.hosts import HostCapabilities


FIXTURE = Path(__file__).parents[1] / "fixtures" / "hosts" / "codex-cli-0.150.0-alpha.8.json"


class CodexCapabilitiesTests(unittest.TestCase):
    def test_checked_in_fixture_matches_capability_contract(self):
        payload = json.loads(FIXTURE.read_text())
        capabilities = HostCapabilities.model_validate(payload)
        self.assertEqual(capabilities.host_form, "cli-exec")
        self.assertEqual(capabilities.probe_result.status, "blocked")
        self.assertEqual(capabilities.probe_result.blocker, "model_authentication_failed")
        self.assertEqual(capabilities.observed_events(), set())
        self.assertIn("SessionStart", capabilities.source_declared_events)
        self.assertTrue(capabilities.runtime_feature_flags["hooks"])

    def test_fixture_contains_no_raw_prompt_or_transcript(self):
        payload = json.loads(FIXTURE.read_text())
        self.assertFalse(payload["transcript_support"]["content_saved"])
        for record in payload["payload_shapes"]:
            self.assertNotIn("prompt", record)
            self.assertNotIn("tool_input", record)
            self.assertNotIn("tool_response", record)

    def test_recorder_saves_shape_without_sensitive_values(self):
        recorder = Path(__file__).parents[2] / "scripts" / "g3_hook_recorder.py"
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "private-session-id",
            "cwd": "/private/repository",
            "prompt": "private prompt",
            "tool_name": "shell",
            "tool_input": {"command": "print a secret"},
            "tool_response": {"output": "secret output"},
        }
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            completed = subprocess.run(
                [sys.executable, str(recorder), "--log", str(log)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                check=True,
            )
            record = json.loads(log.read_text())

        self.assertEqual(json.loads(completed.stdout), {})
        self.assertTrue(record["has_session_id"])
        self.assertTrue(record["has_cwd"])
        self.assertEqual(record["tool_input_keys"], ["command"])
        self.assertEqual(record["tool_response_keys"], ["output"])
        serialized = json.dumps(record)
        for sensitive in payload.values():
            if isinstance(sensitive, str) and sensitive not in {"PreToolUse", "shell"}:
                self.assertNotIn(sensitive, serialized)


if __name__ == "__main__":
    unittest.main()
