"""Synthetic ~/.codex home shared by Memory Lab tests and the browser fixture server."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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
        con.executescript((Path(__file__).resolve().parent.parent / "tests" / "memlab" / "fixtures" / "memories_schema.sql").read_text())
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
