"""Read-only readers for the local Codex home (``~/.codex``).

Everything returned here is tagged ``evidence="local_data"``. The readers never
open SQLite files in write mode and never touch the memories git repo.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    return con


def _iso(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    if seconds > 10_000_000_000:  # milliseconds
        seconds = seconds / 1000
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def approx_tokens(text: str) -> int:
    """Rough tokenizer-free estimate: CJK characters ~1 token each, everything else ~4 chars/token."""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u30ff")
    return cjk + (len(text) - cjk) // 4


def redact(text: str) -> str:
    """Best-effort secret scrubbing before anything reaches the browser."""
    text = re.sub(r"(sk|rk|ghp|gho|xox[abp])-[A-Za-z0-9_\-]{12,}", "<redacted-token>", text)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)(\S{8,})", r"\1\2<redacted>", text)
    text = re.sub(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", "<redacted-jwt>", text)
    return text


# --------------------------------------------------------------------------- home

def home_overview(home: Path | None = None) -> dict[str, Any]:
    home = home or default_codex_home()
    memories_dir = home / "memories"
    files: list[dict[str, Any]] = []
    for name in ("memory_summary.md", "MEMORY.md", "raw_memories.md"):
        p = memories_dir / name
        files.append({"name": name, "exists": p.exists(), "bytes": p.stat().st_size if p.exists() else 0,
                      "modified": _iso(p.stat().st_mtime) if p.exists() else None})
    summaries = sorted((memories_dir / "rollout_summaries").glob("*.md")) if memories_dir.exists() else []
    skills = sorted(p.name for p in (memories_dir / "skills").iterdir()) if (memories_dir / "skills").exists() else []
    notes = sorted(p.name for p in (memories_dir / "extensions" / "ad_hoc" / "notes").glob("*.md")) if memories_dir.exists() else []
    config = _read_memory_config(home / "config.toml")
    sessions_dir = home / "sessions"
    rollouts = sum(1 for _ in sessions_dir.rglob("rollout-*.jsonl")) if sessions_dir.exists() else 0
    archived = sum(1 for _ in (home / "archived_sessions").glob("rollout-*.jsonl")) if (home / "archived_sessions").exists() else 0
    cli_version = _latest_cli_version(home)
    return {
        "evidence": "local_data",
        "home": str(home), "exists": home.exists(),
        "cli_version": cli_version,
        "config": config,
        "files": files,
        "rollout_summaries": len(summaries),
        "skills": skills,
        "ad_hoc_notes": notes,
        "rollouts": rollouts, "archived_rollouts": archived,
        "databases": {n: (home / n).exists() for n in ("memories_1.sqlite", "state_5.sqlite", "thread_history_1.sqlite")},
    }


def _read_memory_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False}
    try:
        import tomllib
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "error": str(exc)}
    features = data.get("features", {}) if isinstance(data.get("features"), dict) else {}
    memories = data.get("memories", {}) if isinstance(data.get("memories"), dict) else {}
    return {
        "present": True,
        "features.memories": features.get("memories"),
        "memories": {k: memories.get(k) for k in ("generate_memories", "use_memories", "disable_on_external_context",
                                                    "extract_model", "consolidation_model", "max_rollouts_per_startup",
                                                    "min_rollout_idle_hours", "max_rollout_age_days",
                                                    "max_raw_memories_for_consolidation", "max_unused_days") if k in memories},
        "model_context_window": data.get("model_context_window"),
        "model_auto_compact_token_limit": data.get("model_auto_compact_token_limit"),
        "compact_prompt": bool(data.get("compact_prompt")),
    }


def _latest_cli_version(home: Path) -> str | None:
    db = home / "state_5.sqlite"
    if not db.exists():
        return None
    try:
        with _ro(db) as con:
            row = con.execute("SELECT cli_version FROM threads WHERE cli_version != '' ORDER BY updated_at DESC LIMIT 1").fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None


# --------------------------------------------------------------------------- threads

def list_threads(home: Path | None = None, limit: int = 300) -> list[dict[str, Any]]:
    home = home or default_codex_home()
    db = home / "state_5.sqlite"
    if not db.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with _ro(db) as con:
            rows = con.execute(
                "SELECT id, title, first_user_message, cwd, model, source, thread_source, cli_version, created_at, updated_at, "
                "tokens_used, memory_mode, archived, rollout_path, git_branch, git_sha FROM threads "
                "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error:
        return []
    stage1 = {r["thread_id"]: r for r in _stage1_rows(home)}
    compactions = _compaction_counts(home)
    for r in rows:
        source = r["source"]
        is_subagent = source.startswith("{")
        s1 = stage1.get(r["id"])
        out.append({
            "evidence": "local_data",
            "id": r["id"], "title": (r["title"] or r["first_user_message"] or "").strip()[:160],
            "cwd": r["cwd"], "model": r["model"], "source": "subagent" if is_subagent else source,
            "cli_version": r["cli_version"], "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"]),
            "tokens_used": r["tokens_used"], "memory_mode": r["memory_mode"], "archived": bool(r["archived"]),
            "rollout_path": r["rollout_path"], "rollout_exists": Path(r["rollout_path"]).exists() if r["rollout_path"] else False,
            "git_branch": r["git_branch"], "compactions": compactions.get(r["id"], 0),
            "stage1": None if s1 is None else {
                "generated_at": _iso(s1["generated_at"]), "usage_count": s1["usage_count"] or 0,
                "last_usage": _iso(s1["last_usage"]), "selected_for_phase2": bool(s1["selected_for_phase2"]),
                "slug": s1["rollout_slug"]},
        })
    return out


def _compaction_counts(home: Path) -> dict[str, int]:
    db = home / "thread_history_1.sqlite"
    if not db.exists():
        return {}
    try:
        with _ro(db) as con:
            return {r[0]: r[1] for r in con.execute(
                "SELECT thread_id, COUNT(*) FROM thread_items WHERE item_type='contextCompaction' GROUP BY thread_id")}
    except sqlite3.Error:
        return {}


def _stage1_rows(home: Path) -> list[sqlite3.Row]:
    db = home / "memories_1.sqlite"
    if not db.exists():
        return []
    try:
        with _ro(db) as con:
            return con.execute("SELECT * FROM stage1_outputs").fetchall()
    except sqlite3.Error:
        return []


def stage1_outputs(home: Path | None = None) -> list[dict[str, Any]]:
    home = home or default_codex_home()
    out = []
    for r in _stage1_rows(home):
        raw = r["raw_memory"] or ""
        front = _frontmatter(raw)
        out.append({
            "evidence": "local_data",
            "thread_id": r["thread_id"], "slug": r["rollout_slug"],
            "source_updated_at": _iso(r["source_updated_at"]), "generated_at": _iso(r["generated_at"]),
            "usage_count": r["usage_count"] or 0, "last_usage": _iso(r["last_usage"]),
            "selected_for_phase2": bool(r["selected_for_phase2"]),
            "raw_memory_chars": len(raw), "rollout_summary_chars": len(r["rollout_summary"] or ""),
            "description": front.get("description"), "task_group": front.get("task_group"),
            "task_outcome": front.get("task_outcome"), "keywords": front.get("keywords"), "cwd": front.get("cwd"),
            "raw_memory": redact(raw), "rollout_summary": redact(r["rollout_summary"] or ""),
        })
    out.sort(key=lambda x: x["generated_at"] or "", reverse=True)
    return out


def _frontmatter(text: str) -> dict[str, str]:
    m = re.search(r"^---\n(.*?)\n---", text, re.S | re.M)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def memory_jobs(home: Path | None = None) -> list[dict[str, Any]]:
    home = home or default_codex_home()
    db = home / "memories_1.sqlite"
    if not db.exists():
        return []
    try:
        with _ro(db) as con:
            rows = con.execute("SELECT * FROM jobs ORDER BY COALESCE(finished_at, started_at) DESC").fetchall()
    except sqlite3.Error:
        return []
    return [{
        "evidence": "local_data",
        "kind": r["kind"], "job_key": r["job_key"], "status": r["status"], "worker_id": r["worker_id"],
        "started_at": _iso(r["started_at"]), "finished_at": _iso(r["finished_at"]),
        "duration_s": (r["finished_at"] - r["started_at"]) if r["finished_at"] and r["started_at"] else None,
        "retry_remaining": r["retry_remaining"], "last_error": r["last_error"],
        "input_watermark": _iso(r["input_watermark"]), "last_success_watermark": _iso(r["last_success_watermark"]),
    } for r in rows]


# --------------------------------------------------------------------------- memory files

_THREAD_RE = re.compile(r"thread_id=([0-9a-f\-]{36})")
_SUMMARY_FILE_RE = re.compile(r"rollout_summaries/(\S+\.md)")


def memory_md_blocks(home: Path | None = None) -> list[dict[str, Any]]:
    """Split MEMORY.md into ``# Task Group`` blocks with the threads each block cites."""
    home = home or default_codex_home()
    path = home / "memories" / "MEMORY.md"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    blocks: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for i, line in enumerate(lines, start=1):
        if line.startswith("# "):
            if cur:
                cur["line_end"] = i - 1
                blocks.append(cur)
            cur = {"evidence": "local_data", "title": line[2:].strip(), "line_start": i, "line_end": None,
                   "threads": [], "summary_files": [], "sections": {}, "_section": None, "text": []}
        if cur is None:
            continue
        cur["text"].append(line)
        if line.startswith("## "):
            cur["_section"] = line[3:].strip()
            cur["sections"][cur["_section"]] = []
        elif line.startswith("- ") and cur["_section"] and not line.startswith("- rollout_summaries/"):
            cur["sections"][cur["_section"]].append(redact(line[2:].strip()))
        for t in _THREAD_RE.findall(line):
            if t not in cur["threads"]:
                cur["threads"].append(t)
        for f in _SUMMARY_FILE_RE.findall(line):
            if f not in cur["summary_files"]:
                cur["summary_files"].append(f)
    if cur:
        cur["line_end"] = len(lines)
        blocks.append(cur)
    for b in blocks:
        b.pop("_section", None)
        b["text"] = redact("\n".join(b["text"]))
        b["file"] = "MEMORY.md"
    return blocks


def memory_summary(home: Path | None = None) -> dict[str, Any]:
    home = home or default_codex_home()
    path = home / "memories" / "memory_summary.md"
    if not path.exists():
        return {"evidence": "local_data", "exists": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    cur = None
    for i, line in enumerate(lines, start=1):
        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "line_start": i, "items": []}
            sections.append(cur)
        elif cur is not None and line.strip():
            cur["items"].append(redact(line.rstrip()))
    return {"evidence": "local_data", "exists": True, "version_line": lines[0] if lines else None,
            "bytes": len(text.encode()), "approx_tokens": approx_tokens(text), "sections": sections,
            "modified": _iso(path.stat().st_mtime), "text": redact(text)}


def rollout_summaries(home: Path | None = None) -> list[dict[str, Any]]:
    home = home or default_codex_home()
    d = home / "memories" / "rollout_summaries"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        head = _header(text)
        out.append({"evidence": "local_data", "file": p.name, "thread_id": head.get("thread_id"),
                    "rollout_path": head.get("rollout_path"), "cwd": head.get("cwd"), "updated_at": head.get("updated_at"),
                    "title": next((l[2:] for l in text.splitlines() if l.startswith("# ")), p.stem),
                    "bytes": len(text.encode()), "text": redact(text)})
    return out


def _header(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines()[:12]:
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            if k.strip() in {"thread_id", "rollout_path", "cwd", "updated_at", "git_branch", "rollout_summary_file"}:
                out[k.strip()] = v.strip()
    return out


def raw_memories_threads(home: Path | None = None) -> list[dict[str, Any]]:
    home = home or default_codex_home()
    path = home / "memories" / "raw_memories.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r"^## Thread `([0-9a-f\-]{36})`\n(.*?)(?=^## Thread `|\Z)", text, re.S | re.M):
        body = m.group(2)
        head = _header(body)
        out.append({"evidence": "local_data", "thread_id": m.group(1), **head, "chars": len(body)})
    return out


def ad_hoc_notes(home: Path | None = None) -> list[dict[str, Any]]:
    home = home or default_codex_home()
    d = home / "memories" / "extensions" / "ad_hoc" / "notes"
    if not d.exists():
        return []
    return [{"evidence": "local_data", "file": p.name, "modified": _iso(p.stat().st_mtime),
             "text": redact(p.read_text(encoding="utf-8", errors="replace"))} for p in sorted(d.glob("*.md"))]


def memories_git_log(home: Path | None = None, limit: int = 20) -> list[dict[str, str]]:
    """The consolidation agent works inside a git repo; its log is the update history we can see."""
    import subprocess
    home = home or default_codex_home()
    root = home / "memories"
    if not (root / ".git").exists():
        return []
    try:
        out = subprocess.check_output(["git", "-C", str(root), "log", f"-{limit}", "--format=%H%x1f%cI%x1f%s", "--shortstat"],
                                      text=True, stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    entries = []
    for chunk in out.strip().split("\n\n"):
        lines = [l for l in chunk.splitlines() if l.strip()]
        if not lines:
            continue
        parts = lines[0].split("\x1f")
        if len(parts) == 3:
            entries.append({"commit": parts[0][:12], "date": parts[1], "subject": parts[2],
                            "stat": lines[1].strip() if len(lines) > 1 else ""})
    return entries


# --------------------------------------------------------------------------- rollouts

_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def parse_citation_block(block: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Mirror of codex-rs/memories/read/src/citations.rs: entries are ``file:start-end|note=[...]``."""
    entries: list[dict[str, Any]] = []
    ent = re.search(r"<citation_entries>(.*?)</citation_entries>", block, re.S)
    for line in (ent.group(1) if ent else "").splitlines():
        line = line.strip()
        if "|note=[" not in line:
            continue
        loc, note = line.rsplit("|note=[", 1)
        if ":" not in loc:
            continue
        file, rng = loc.rsplit(":", 1)
        m = re.match(r"(\d+)(?:-(\d+))?", rng)
        if not m:
            continue
        entries.append({"file": file, "start": int(m.group(1)), "end": int(m.group(2) or m.group(1)),
                        "note": redact(note.rstrip("]"))})
    ids_block = re.search(r"<(?:rollout_ids|thread_ids)>(.*?)</(?:rollout_ids|thread_ids)>", block, re.S)
    ids: list[str] = []
    for i in _UUID_RE.findall(ids_block.group(1) if ids_block else ""):
        if i not in ids:
            ids.append(i)
    return entries, ids


@dataclass
class RolloutEvent:
    ordinal: int
    timestamp: str | None
    kind: str          # session_meta | turn | user | assistant | reasoning | tool_call | tool_output | token_count | compacted | other
    summary: str
    tokens: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def iter_rollout(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                yield {"type": "unparsed", "payload": {"raw": line[:200]}}


def resolve_rollout(home: Path, thread_id: str) -> Path | None:
    db = home / "state_5.sqlite"
    if db.exists():
        try:
            with _ro(db) as con:
                row = con.execute("SELECT rollout_path FROM threads WHERE id = ?", (thread_id,)).fetchone()
                if row and Path(row[0]).exists():
                    return Path(row[0])
        except sqlite3.Error:
            pass
    for base in (home / "sessions", home / "archived_sessions"):
        if base.exists():
            hits = list(base.rglob(f"rollout-*{thread_id}*.jsonl"))
            if hits:
                return hits[0]
    return None


def read_rollout(home: Path | None, thread_id: str, *, include_text: bool = True, max_text: int = 1200) -> dict[str, Any]:
    """Turn a rollout into a compact timeline the browser can animate."""
    home = home or default_codex_home()
    path = resolve_rollout(home, thread_id)
    if path is None:
        return {"evidence": "local_data", "thread_id": thread_id, "found": False}
    events: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    counts: dict[str, int] = {}
    citations: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    memory_prompt_tokens: int | None = None
    turn_no = 0
    for i, o in enumerate(iter_rollout(path)):
        t = o.get("type")
        p = o.get("payload") or {}
        ts = o.get("timestamp")
        ev: dict[str, Any] | None = None
        if t == "session_meta":
            git = p.get("git") or {}
            meta = {"session_id": p.get("id"), "cwd": p.get("cwd"), "cli_version": p.get("cli_version"),
                    "originator": p.get("originator"), "source": p.get("source"), "memory_mode": p.get("memory_mode"),
                    "started": p.get("timestamp"), "git_branch": git.get("branch"), "git_commit": (git.get("commit_hash") or "")[:12],
                    "history_mode": p.get("history_mode"), "window_id": (p.get("context_window") or {}).get("window_id")}
            ev = {"kind": "session_meta", "summary": f"会话开始 · {p.get('originator')} {p.get('cli_version')} · cwd={p.get('cwd')}"}
        elif t == "turn_context":
            turn_no += 1
            ev = {"kind": "turn", "summary": f"第 {turn_no} 轮 · model={p.get('model')} effort={p.get('effort')}",
                  "detail": {"turn_id": p.get("turn_id"), "model": p.get("model"), "effort": p.get("effort"), "cwd": p.get("cwd")}}
        elif t == "response_item":
            pt = p.get("type")
            if pt == "message":
                role = p.get("role")
                text = _text_of(p.get("content"))
                if role == "developer":
                    tag = "developer"
                    if "MEMORY_SUMMARY BEGINS" in text:
                        tag = "memory_injection"
                        m = re.search(r"MEMORY_SUMMARY BEGINS =+(.*?)=+ MEMORY_SUMMARY ENDS", text, re.S)
                        memory_prompt_tokens = approx_tokens(m.group(1)) if m else None
                    ev = {"kind": tag, "summary": ("注入记忆摘要（memory_summary.md）" if tag == "memory_injection" else "developer 指令") + f" · {len(text)} 字",
                          "detail": {"chars": len(text)}}
                elif role == "user":
                    if text.startswith("<environment_context>") or text.startswith("<permissions") or text.startswith("# AGENTS.md"):
                        ev = {"kind": "harness", "summary": f"harness 注入 {text[:40].splitlines()[0]}… · {len(text)} 字", "detail": {"chars": len(text)}}
                    elif text.startswith("Another language model started to solve this problem"):
                        ev = {"kind": "summary_message", "summary": "压缩摘要作为 user 消息进入历史", "detail": {"chars": len(text)}}
                    else:
                        ev = {"kind": "user", "summary": text.strip()[:160], "detail": {"chars": len(text)}}
                elif role == "assistant":
                    cit = re.search(r"<oai-mem-citation>(.*?)</oai-mem-citation>", text, re.S)
                    if cit:
                        block = cit.group(1)
                        entries, ids = parse_citation_block(block)
                        citations.append({"ordinal": i, "timestamp": ts, "entries": entries, "rollout_ids": ids})
                        text = text[:cit.start()].rstrip()
                    ev = {"kind": "assistant", "summary": text.strip()[:160], "detail": {"chars": len(text), "cited_memory": bool(cit)}}
            elif pt == "reasoning":
                ev = {"kind": "reasoning", "summary": "推理", "detail": {}}
            elif pt in ("function_call", "custom_tool_call", "local_shell_call", "web_search_call"):
                name = p.get("name") or pt
                args = p.get("arguments") or p.get("input") or ""
                ev = {"kind": "tool_call", "summary": f"{name} {str(args)[:100]}", "detail": {"name": name}}
            elif pt in ("function_call_output", "custom_tool_call_output"):
                out = p.get("output")
                ev = {"kind": "tool_output", "summary": f"工具输出 · {len(str(out))} 字", "detail": {"chars": len(str(out))}}
            elif pt == "agent_message":
                ev = {"kind": "agent_message", "summary": "子 agent 消息"}
        elif t == "event_msg" and p.get("type") == "token_count":
            info = p.get("info") or {}
            last = info.get("last_token_usage") or {}
            total = info.get("total_token_usage") or {}
            ev = {"kind": "token_count", "summary": f"上下文 {last.get('input_tokens', 0):,} tokens",
                  "tokens": {"context": last.get("input_tokens"), "cached": last.get("cached_input_tokens"),
                             "output": last.get("output_tokens"), "cumulative": total.get("input_tokens"),
                             "window": info.get("model_context_window")}}
        elif t == "compacted":
            rh = p.get("replacement_history") or []
            kept_roles = [f"{x.get('type')}/{x.get('role')}" for x in rh if isinstance(x, dict)]
            windows.append({"ordinal": i, "timestamp": ts, "window_number": p.get("window_number"), "window_id": p.get("window_id"),
                            "previous_window_id": p.get("previous_window_id")})
            ev = {"kind": "compacted",
                  "summary": f"上下文压缩 · 第 {p.get('window_number')} 个窗口 · 保留 {len(rh)} 项",
                  "detail": {"message_chars": len(p.get("message") or ""), "kept": kept_roles, "window_number": p.get("window_number"),
                             "summary_text": redact((p.get("message") or "")[: max_text * 4]) if include_text else None,
                             "kept_user_messages": [redact(_text_of(x.get("content"))[:300]) for x in rh
                                                    if isinstance(x, dict) and x.get("role") == "user"] if include_text else None}}
        elif t == "event_msg" and p.get("type") in ("task_started", "task_complete", "turn_aborted"):
            ev = {"kind": "task", "summary": p.get("type")}
        elif t == "unparsed":
            ev = {"kind": "unparsed", "summary": "无法解析的行"}
        if ev is None:
            continue
        ev.setdefault("detail", {})
        ev.update(ordinal=i, timestamp=ts)
        if include_text and ev["kind"] in ("user", "assistant"):
            ev["summary"] = redact(ev["summary"])
        counts[ev["kind"]] = counts.get(ev["kind"], 0) + 1
        events.append(ev)
    return {"evidence": "local_data", "thread_id": thread_id, "found": True, "path": str(path),
            "bytes": path.stat().st_size, "meta": meta, "counts": counts, "events": events,
            "citations": citations, "windows": windows, "memory_prompt_tokens": memory_prompt_tokens}


def scan_citations(home: Path | None = None, limit_files: int = 400) -> list[dict[str, Any]]:
    """Every real <oai-mem-citation> emitted by the assistant across local rollouts."""
    home = home or default_codex_home()
    out: list[dict[str, Any]] = []
    files: list[Path] = []
    for base in (home / "sessions", home / "archived_sessions"):
        if base.exists():
            files.extend(base.rglob("rollout-*.jsonl"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:limit_files]:
        thread_id = None
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if thread_id is None and '"session_meta"' in line:
                        try:
                            thread_id = json.loads(line)["payload"].get("id")
                        except (ValueError, KeyError):
                            pass
                    if "<oai-mem-citation>" not in line or "assistant" not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    p = o.get("payload") or {}
                    if p.get("type") != "message" or p.get("role") != "assistant":
                        continue
                    text = _text_of(p.get("content"))
                    m = re.search(r"<oai-mem-citation>(.*?)</oai-mem-citation>", text, re.S)
                    if not m:
                        continue
                    block = m.group(1)
                    entries, ids = parse_citation_block(block)
                    if not entries and not ids:
                        continue
                    out.append({"evidence": "local_data", "citing_thread": thread_id, "timestamp": o.get("timestamp"),
                                "entries": entries, "rollout_ids": ids})
        except OSError:
            continue
    return out
