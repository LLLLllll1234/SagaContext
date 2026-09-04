import unittest
from pathlib import Path
from sagacontext.memfile import parse, render
from sagacontext.recall import render as render_recall
from sagacontext.config import Config

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

if __name__ == "__main__":
    unittest.main()
