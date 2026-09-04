import unittest
from pathlib import Path
from sagacontext.memfile import parse, render
from sagacontext.recall import render as render_recall
from sagacontext.config import Config
from sagacontext.capture import detect
from sagacontext.transcript import read_incremental
from sagacontext.reconcile import compress, correction_plan
from sagacontext.models import Delta, MemoryRecord
from sagacontext.compliance import check_pattern
from sagacontext.reconcile import WritePlan, evolve
from sagacontext.writer import apply

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

    def test_compress_prefers_user_turns(self):
        class Turn:
            def __init__(self, idx, role, text): self.idx, self.role, self.text = idx, role, text
        result = compress([Turn(0, "assistant", "background" * 100), Turn(1, "user", "keep this")], 10)
        self.assertIn("user", result)

    def test_compliance_pattern(self):
        self.assertEqual(check_pattern("src/a.ts: any", r"\bany\b").decision, "deny")
        self.assertEqual(check_pattern("unknown", r"\bany\b").decision, "allow")

    def test_evolve_relations(self):
        existing = MemoryRecord(uri="viking://x/no_any.md", type="dev_convention", version=2,
                                fields={"version": 2, "topic": "no_any", "rule": "no any", "evidence_count": 1, "contra_count": 0, "status": "active"})
        confirm, _ = evolve(existing, Delta(layer="preference", type="dev_convention", relation="confirm", anchor_uri=existing.uri, key="no_any"), "viking://root", "repo")
        self.assertEqual(confirm[0].fields["evidence_count"], 2)
        refine, _ = evolve(existing, Delta(layer="preference", type="dev_convention", relation="refine", anchor_uri=existing.uri, key="no_any", fields={"rule": "use unknown"}), "viking://root", "repo")
        self.assertEqual(refine[0].fields["rule"], "use unknown")
        supersede, _ = evolve(existing, Delta(layer="preference", type="dev_convention", relation="supersede", anchor_uri=existing.uri, key="no_any", fields={"rule": "allow any"}, strong_signal=True), "viking://root", "repo")
        self.assertEqual(len(supersede), 2)
        self.assertEqual(supersede[0].fields["status"], "superseded")
        conflict, pending = evolve(existing, Delta(layer="preference", type="dev_convention", relation="conflict", anchor_uri=existing.uri, key="no_any", fields={"rule": "maybe any"}), "viking://root", "repo")
        self.assertEqual(conflict[0].fields["status"], "pending_confirm")
        self.assertEqual(len(pending), 1)

class AsyncCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_rebases_version(self):
        class Client:
            def __init__(self): self.writes = []
            async def read(self, uri): return {"content": render({"version": 3, "topic": "x", "rule": "old"})}
            async def write(self, uri, content): self.writes.append(content); return {}
            @staticmethod
            def content_from_response(payload): return payload["content"]
        client = Client()
        fields = {"version": 3, "topic": "x", "rule": "new"}
        written = await apply([WritePlan("viking://x", "dev_convention", render(fields), fields, "update", 2)], client)
        self.assertEqual(written, ["viking://x"])
        self.assertIn('"version": 4', client.writes[0])

if __name__ == "__main__":
    unittest.main()
