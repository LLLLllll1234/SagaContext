import unittest
from pathlib import Path
from sagacontext.memfile import parse, render
from sagacontext.recall import render as render_recall
from sagacontext.config import Config
from sagacontext.capture import detect
from sagacontext.transcript import read_incremental
from sagacontext.reconcile import compress, correction_plan
from sagacontext.models import Delta

class CoreTests(unittest.TestCase):
    def test_memory_roundtrip(self):
        content = render({"version": 2, "topic": "ts_no_any", "rule": "不要使用 <any>", "confidence": 0.7})
        rec = parse("viking://x", content)
        self.assertEqual(rec.version, 2)
        self.assertEqual(rec.fields["topic"], "ts_no_any")
        self.assertIn("MEMORY_FIELDS", content)

    def test_render_escapes_and_budget(self):
        rec = parse("viking://x", render({"version": 1, "layer": "preference", "scope_key": "global", "rule": "<x>", "confidence": 1}))
        output = render_recall([rec], 100)
        self.assertIn("&lt;x&gt;", output)
        self.assertTrue(output.startswith("<memory"))

    def test_default_config_loads(self):
        cfg = Config.load(Path("/tmp/sagacontext-config-that-does-not-exist"))
        self.assertEqual(cfg.port, 37780)

    def test_capture_rules(self):
        self.assertEqual(detect("以后不要使用 any")[0].kind, "explicit_negation")
        self.assertEqual(detect("We decided to use SQLite")[0].layer_guess, "project")

    def test_incremental_transcript_ignores_partial_line(self):
        path = Path("/tmp/sagacontext-transcript.jsonl")
        path.write_bytes(b'{"role":"user","text":"hello"}\n{"role":"assistant","text":"partial"}')
        turns, offset = read_incremental(path)
        self.assertEqual(len(turns), 1)
        self.assertLess(offset, path.stat().st_size)

    def test_reconcile_plan_is_stable(self):
        candidate = detect("不要使用 any")[0]
        first = correction_plan(candidate, "viking://~/memories/dev", "abcd1234")
        second = correction_plan(candidate, "viking://~/memories/dev", "abcd1234")
        self.assertEqual(first.uri, second.uri)
        self.assertIn("MEMORY_FIELDS", first.content)

    def test_delta_schema(self):
        delta = Delta(layer="preference", type="dev_correction", relation="new", key="no_any")
        self.assertEqual(delta.relation, "new")

if __name__ == "__main__":
    unittest.main()
