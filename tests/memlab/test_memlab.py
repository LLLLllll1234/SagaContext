"""Memory Lab tests run against a synthetic ~/.codex fixture, never the real one."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from sagacontext.config import Config
from sagacontext.daemon import create_app
from sagacontext.memlab import codex_data as cd, lifecycle, source_facts

T1 = "01a00000-0000-7000-8000-000000000001"
T2 = "01a00000-0000-7000-8000-000000000002"


def _line(t: str, payload: dict, ts: str = "2026-09-01T10:00:00.000Z") -> str:
    return json.dumps({"timestamp": ts, "type": t, "payload": payload}) + "\n"


def build_home(root: Path) -> Path:
    home = root / "codex"
    (home / "sessions" / "2026" / "09" / "01").mkdir(parents=True)
    (home / "memories" / "rollout_summaries").mkdir(parents=True)
    (home / "memories" / "extensions" / "ad_hoc" / "notes").mkdir(parents=True)
    (home / "config.toml").write_text("[features]\nmemories = true\n[memories]\ngenerate_memories = true\nuse_memories = true\n")
    rollout = home / "sessions" / "2026" / "09" / "01" / f"rollout-2026-09-01T10-00-00-{T1}.jsonl"
    tok = lambda n, w=100_000: {"type": "token_count", "info": {"total_token_usage": {"input_tokens": n * 3}, "last_token_usage": {"input_tokens": n, "cached_input_tokens": 0}, "model_context_window": w}}
    rollout.write_text(
        _line("session_meta", {"id": T1, "cwd": "/w", "cli_version": "0.154.0", "originator": "Codex Desktop", "source": "vscode", "timestamp": "2026-09-01T10:00:00Z", "git": {"branch": "main", "commit_hash": "abc123def456"}})
        + _line("response_item", {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "## Memory\n========= MEMORY_SUMMARY BEGINS =========\nv1\nhello world\n========= MEMORY_SUMMARY ENDS ========="}]})
        + _line("turn_context", {"turn_id": "t1", "model": "gpt-x", "effort": "high", "cwd": "/w"})
        + _line("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "请帮我 sk-abcdefghijklmnopqrstuvwxyz1234 修 bug"}]})
        + _line("event_msg", tok(50_000))
        + _line("response_item", {"type": "function_call", "name": "exec", "arguments": "ls", "call_id": "c1"})
        + _line("response_item", {"type": "function_call_output", "call_id": "c1", "output": "x" * 500})
        + _line("event_msg", tok(95_000))
        + _line("compacted", {"message": "Another language model started to solve this problem... summary", "window_number": 1,
                              "replacement_history": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "请帮我修 bug"}]},
                                                      {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Another language model started..."}]}]})
        + _line("event_msg", tok(8_000))
        + _line("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done\n<oai-mem-citation>\n<citation_entries>\nMEMORY.md:3-5|note=[used prefs]\n</citation_entries>\n<rollout_ids>\n" + T2 + "\n</rollout_ids>\n</oai-mem-citation>"}]})
        + "this line is not json\n"
    )
    with sqlite3.connect(home / "state_5.sqlite") as con:
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, created_at INTEGER, updated_at INTEGER, source TEXT, model_provider TEXT, cwd TEXT, title TEXT, sandbox_policy TEXT, approval_mode TEXT, tokens_used INTEGER, has_user_event INTEGER, archived INTEGER, archived_at INTEGER, git_sha TEXT, git_branch TEXT, git_origin_url TEXT, cli_version TEXT, first_user_message TEXT, memory_mode TEXT, model TEXT, thread_source TEXT)")
        con.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (T1, str(rollout), 1756720000, 1756720100, "vscode", "openai", "/w", "修 bug", "{}", "never", 1234, 1, 0, None, None, "main", None, "0.154.0", "修 bug", "enabled", "gpt-x", "user"))
        con.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (T2, "/missing.jsonl", 1756620000, 1756620100, "vscode", "openai", "/w", "老会话", "{}", "never", 99, 1, 0, None, None, None, None, "0.153.0", "老会话", "enabled", "gpt-x", "user"))
    with sqlite3.connect(home / "thread_history_1.sqlite") as con:
        con.execute("CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, rollout_ordinal INTEGER, created_at_ms INTEGER, item_json TEXT, item_type TEXT, updated_at_ordinal INTEGER)")
        con.execute("INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)", (T1, "t1", "i1", 8, 0, "{}", "contextCompaction", 0))
    with sqlite3.connect(home / "memories_1.sqlite") as con:
        con.executescript((Path(__file__).parent / "fixtures" / "memories_schema.sql").read_text())
        con.execute("INSERT INTO stage1_outputs VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (T2, 1756620100, "---\ndescription: d\ntask_group: g\ntask_outcome: success\ncwd: /w\nkeywords: a, b\n---\nbody", "summary text", "slug", 1756700000, 4, 1756710000, 1, 1756620100))
        con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("memory_stage1", T2, "done", T1, "tok", 1756699000, 1756700000, None, None, 3, None, 1756620100, 1756620100))
        con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("memory_consolidate_global", "global", "done", T1, "tok", 1756700100, 1756700400, None, None, 3, None, 1756620100, 1756620100))
    (home / "memories" / "memory_summary.md").write_text("v1\n\n## User Profile\n\nThe user likes tests.\n\n## User preferences\n\n- pref one\n")
    (home / "memories" / "MEMORY.md").write_text(
        "# Task Group: Fix bugs\n\nscope: x\n\n## Task 1: fix\n\n### rollout_summary_files\n\n- rollout_summaries/2026-09-01T09-00-00-abcd-old.md (cwd=/w, thread_id=" + T2 + ", success)\n\n## User preferences\n\n- always run tests [Task 1]\n"
        "# Task Group: Orphan\n\n## Task 1: gone\n\n### rollout_summary_files\n\n- rollout_summaries/2026-08-01T09-00-00-zzzz-gone.md (thread_id=01a00000-0000-7000-8000-000000000003)\n")
    (home / "memories" / "rollout_summaries" / "2026-09-01T09-00-00-abcd-old.md").write_text(f"---\nthread_id: {T2}\nupdated_at: 2026-08-31T00:00:00+00:00\nrollout_path: /missing.jsonl\ncwd: /w\n\n# Old session\n\nbody")
    (home / "memories" / "raw_memories.md").write_text(f"# Raw Memories\n\n## Thread `{T2}`\nupdated_at: 2026-08-31T00:00:00+00:00\ncwd: /w\nrollout_path: /missing.jsonl\nrollout_summary_file: 2026-09-01T09-00-00-abcd-old.md\n\nbody\n")
    return home


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
