import tempfile
import unittest
from pathlib import Path

from sagacontext.hosts.codex_shadow import CodexShadowAdapter, EVENTS
from sagacontext.ledger import Ledger


class CodexShadowTests(unittest.TestCase):
    def test_verified_events_replay_idempotently_and_unknown_events_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / "ledger.db", owner_id="owner")
            try:
                workspace = ledger.register_project("test", Path(directory))["workspace_id"]
                session = ledger.open_session("codex", "session", workspace)
                adapter = CodexShadowAdapter(ledger, host_version="codex-cli 0.153.4", verified_events=set(EVENTS))
                record = dict(hook_event_name="SessionStart", source_event_ref="event-1", session_ref="session-1",
                              probe_id="synthetic", scenario="shadow", payload_shape_digest="shape")
                first = adapter.ingest(record, session_id=session, workspace_id=workspace)
                second = adapter.ingest(record, session_id=session, workspace_id=workspace)
                self.assertEqual((first.status, second.status), ("accepted", "duplicate"))
                self.assertEqual(first.event_id, second.event_id)
                with self.assertRaises(ValueError):
                    adapter.ingest({**record, "hook_event_name": "PreCompact"}, session_id=session, workspace_id=workspace)
            finally:
                ledger.close()
