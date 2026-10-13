"""Memory Lab tests run against a synthetic ~/.codex fixture, never the real one."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from sagacontext.config import Config
from sagacontext.daemon import create_app
from sagacontext.memlab import codex_data as cd, lifecycle, source_facts
from scripts.memlab_fixture import T1, T2, build_home

class MemLabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = build_home(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_overview_and_threads(self):
        o = cd.home_overview(self.home)
        self.assertEqual(o["cli_version"], "0.154.0")
        self.assertTrue(o["config"]["memories"]["generate_memories"])
        th = cd.list_threads(self.home)
        self.assertEqual([t["id"] for t in th], [T1, T2])
        self.assertTrue(th[0]["rollout_exists"]); self.assertFalse(th[1]["rollout_exists"])
        self.assertEqual(th[0]["compactions"], 1)
        self.assertEqual(th[1]["stage1"]["usage_count"], 4)

    def test_rollout_timeline_compaction_and_redaction(self):
        r = cd.read_rollout(self.home, T1)
        self.assertTrue(r["found"])
        kinds = [e["kind"] for e in r["events"]]
        self.assertIn("memory_injection", kinds); self.assertIn("compacted", kinds); self.assertIn("unparsed", kinds)
        user = next(e for e in r["events"] if e["kind"] == "user")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234", user["summary"])
        self.assertEqual(r["citations"][0]["rollout_ids"], [T2])
        self.assertEqual(r["citations"][0]["entries"][0], {"file": "MEMORY.md", "start": 3, "end": 5, "note": "used prefs"})
        curve = lifecycle.context_curve(r)
        self.assertEqual(curve["window"], 100_000); self.assertEqual(curve["auto_compact_threshold"], 90_000)
        self.assertEqual(curve["compactions"][0]["context_before"], 95_000)
        self.assertEqual(curve["compactions"][0]["context_after"], 8_000)
        self.assertEqual(curve["compactions"][0]["kept"], ["message/user", "message/user"])

    def test_memory_files_and_lifecycles(self):
        blocks = cd.memory_md_blocks(self.home)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["threads"], [T2])
        self.assertEqual(blocks[0]["sections"]["User preferences"], ["always run tests [Task 1]"])
        lc = lifecycle.build_lifecycles(self.home)
        by = {l["thread_id"]: l for l in lc["lifecycles"]}
        t2 = by[T2]
        self.assertTrue(t2["in_memory_md"] and t2["has_summary_file"] and t2["in_stage1"] and t2["selected"])
        recall = next(s for s in t2["stages"] if s["stage"] == "recall")
        self.assertEqual(recall["status"], "present"); self.assertEqual(recall["uses"][0]["citing_thread"], T1)
        orphan = by["01a00000-0000-7000-8000-000000000003"]
        self.assertEqual(next(s for s in orphan["stages"] if s["stage"] == "forget")["status"], "present")
        self.assertEqual(next(s for s in orphan["stages"] if s["stage"] == "candidate")["status"], "dropped")
        self.assertEqual(lc["counts"]["forgotten"], 1)
        asset = lc["assets"][0]
        self.assertEqual(asset["recall_count"], 1)
        self.assertEqual(asset["provenance"][0]["summary_file"], "2026-09-01T09-00-00-abcd-old.md")

    def test_source_facts_missing_root(self):
        v = source_facts.verify_all(Path(self.tmp.name) / "nope")
        self.assertEqual(v["counts"]["missing"], len(source_facts.FACTS))
        self.assertFalse(v["source"]["exists"])

    def test_source_fact_anchor_verification(self):
        root = Path(self.tmp.name) / "codex"
        f = source_facts.FACTS[0]
        p = root / "codex-rs" / f.file
        p.parent.mkdir(parents=True)
        p.write_text("fn a() {}\n    start_memories_startup_task(x);\n")
        r = source_facts.verify_fact(f, root)
        self.assertEqual((r["status"], r["line"]), ("verified", 2))
        p.write_text("nothing here\n")
        source_facts._read.cache_clear()
        self.assertEqual(source_facts.verify_fact(f, root)["status"], "drifted")

    def test_parameters_read_from_source_or_marked_unverified(self):
        missing = source_facts.verify_all(Path(self.tmp.name) / "nope")["params"]
        self.assertEqual(set(missing), set(source_facts.PARAMETERS))
        self.assertTrue(all(x["check"] == "unverified" for x in missing.values()))
        self.assertEqual(missing["summary_token_limit"]["value"], 2500)
        root = Path(self.tmp.name) / "codex"
        fact = next(f for f in source_facts.FACTS if f.id == "recall.injection")
        path = root / "codex-rs" / fact.file
        path.parent.mkdir(parents=True)
        path.write_text("const MEMORY_TOOL_DEVELOPER_INSTRUCTIONS_SUMMARY_TOKEN_LIMIT: usize = 3_000;\n")
        source_facts._read.cache_clear()
        read = source_facts.verify_all(root)["params"]["summary_token_limit"]
        self.assertEqual((read["value"], read["check"]), (3000, "read"))

    def test_api_is_read_only_and_serves_pages(self):
        import os
        from unittest.mock import patch
        cfg = Config(state_path=Path(self.tmp.name) / "s.db", ledger_path=Path(self.tmp.name) / "l.db")
        before = {p: p.stat().st_mtime_ns for p in self.home.rglob("*") if p.is_file()}
        with patch.dict(os.environ, {"CODEX_HOME": str(self.home), "SAGACONTEXT_CODEX_SOURCE": str(Path(self.tmp.name) / "nope")}):
            with TestClient(create_app(cfg)) as c:
                self.assertEqual(c.get("/memlab/").status_code, 200)
                self.assertIn("SagaContext Memory Lab", c.get("/memlab/").text)
                o = c.get("/memlab/api/overview").json()
                self.assertEqual(o["codex"]["home"]["cli_version"], "0.154.0")
                self.assertEqual(o["codex"]["fact_counts"]["missing"], len(source_facts.FACTS))
                self.assertEqual(c.get(f"/memlab/api/codex/rollout/{T1}").json()["curve"]["window"], 100_000)
                self.assertEqual(c.get("/memlab/api/codex/rollout/deadbeef").status_code, 404)
                self.assertEqual(c.get("/memlab/static/..%2Fapi.py").status_code, 404)
                self.assertEqual(c.get("/console/memory/view", follow_redirects=False).status_code, 307)
                self.assertEqual(len(c.get("/memlab/api/codex/assets").json()["assets"]), 2)
        after = {p: p.stat().st_mtime_ns for p in self.home.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "memlab must never write to the Codex home")


if __name__ == "__main__":
    unittest.main()
