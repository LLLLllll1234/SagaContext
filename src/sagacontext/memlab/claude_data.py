"""Read-only readers for Claude Code local data (``~/.claude``).

Claude Code's source is not available locally, so every mechanism statement
here is either ``local_data`` (read from files) or ``inference`` (derived from
the shape of those files). There is no ``source`` evidence level for this agent.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .codex_data import _iso, redact


def default_claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))).expanduser()


def _frontmatter(text: str) -> dict[str, Any]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {"body": text}
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
        elif line.strip().startswith("type:"):
            meta["type"] = line.split(":", 1)[1].strip()
    meta["body"] = m.group(2)
    return meta


def overview(home: Path | None = None) -> dict[str, Any]:
    home = home or default_claude_home()
    projects = home / "projects"
    project_dirs = sorted(p for p in projects.iterdir() if p.is_dir()) if projects.exists() else []
    memories: list[dict[str, Any]] = []
    transcripts = 0
    for pd in project_dirs:
        transcripts += sum(1 for _ in pd.glob("*.jsonl"))
        mem = pd / "memory"
        if mem.exists():
            for f in sorted(mem.glob("*.md")):
                if f.name == "MEMORY.md":
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
                fm = _frontmatter(text)
                memories.append({"evidence": "local_data", "project": pd.name, "file": f.name, "name": fm.get("name", f.stem),
                                 "description": fm.get("description"), "type": fm.get("type"),
                                 "origin_session": fm.get("originSessionId"), "modified": _iso(f.stat().st_mtime),
                                 "body": redact(fm.get("body", "")).strip()[:1500]})
    claude_md = home / "CLAUDE.md"
    return {"evidence": "local_data", "home": str(home), "exists": home.exists(),
            "projects": [p.name for p in project_dirs], "transcripts": transcripts,
            "global_claude_md": {"exists": claude_md.exists(), "bytes": claude_md.stat().st_size if claude_md.exists() else 0},
            "memories": memories, "counts": {"memories": len(memories), "projects": len(project_dirs)}}


def list_sessions(home: Path | None = None, limit: int = 60) -> list[dict[str, Any]]:
    home = home or default_claude_home()
    projects = home / "projects"
    out: list[dict[str, Any]] = []
    if not projects.exists():
        return out
    files = sorted(projects.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    for f in files:
        first_user = None
        compact = 0
        version = None
        cwd = None
        n = 0
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    n += 1
                    if '"isCompactSummary":true' in line:
                        compact += 1
                    if first_user is None and '"type":"user"' in line and '"isMeta":true' not in line:
                        try:
                            o = json.loads(line)
                            c = o.get("message", {}).get("content")
                            txt = c if isinstance(c, str) else "".join(x.get("text", "") for x in c if isinstance(x, dict))
                            if txt and not txt.startswith("<"):
                                first_user = txt.strip()[:140]
                                version = o.get("version")
                                cwd = o.get("cwd")
                        except ValueError:
                            pass
        except OSError:
            continue
        out.append({"evidence": "local_data", "id": f.stem, "project": f.parent.name, "path": str(f), "bytes": f.stat().st_size,
                    "lines": n, "modified": _iso(f.stat().st_mtime), "title": redact(first_user or ""), "version": version,
                    "cwd": cwd, "compact_summaries": compact})
    return out


def read_session(home: Path | None, session_id: str) -> dict[str, Any]:
    home = home or default_claude_home()
    hits = list((home / "projects").glob(f"*/{session_id}.jsonl"))
    if not hits:
        return {"evidence": "local_data", "found": False, "id": session_id}
    path = hits[0]
    events: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    seen_msg: set[str] = set()
    with path.open(encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            t = o.get("type")
            ev: dict[str, Any] | None = None
            if t == "user":
                c = o.get("message", {}).get("content")
                txt = c if isinstance(c, str) else "".join(x.get("text", "") for x in (c or []) if isinstance(x, dict) and x.get("type") == "text")
                if o.get("isCompactSummary"):
                    ev = {"kind": "compacted", "summary": "上下文压缩：会话以摘要续接", "detail": {"chars": len(txt), "summary_text": redact(txt[:4000])}}
                elif o.get("isMeta"):
                    ev = {"kind": "harness", "summary": f"元消息 · {len(txt)} 字"}
                elif isinstance(c, list) and any(x.get("type") == "tool_result" for x in c if isinstance(x, dict)):
                    ev = {"kind": "tool_output", "summary": f"工具输出 · {len(json.dumps(c))} 字"}
                elif txt:
                    ev = {"kind": "user", "summary": redact(txt.strip()[:160])}
            elif t == "assistant":
                m = o.get("message", {})
                mid = m.get("id")
                usage = m.get("usage") or {}
                content = m.get("content") or []
                texts = [x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text"]
                tools = [x.get("name") for x in content if isinstance(x, dict) and x.get("type") == "tool_use"]
                if mid and mid not in seen_msg and usage:
                    seen_msg.add(mid)
                    ctx = (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
                    events.append({"kind": "token_count", "ordinal": i, "timestamp": o.get("timestamp"),
                                   "summary": f"上下文 {ctx:,} tokens",
                                   "tokens": {"context": ctx, "cached": usage.get("cache_read_input_tokens"),
                                              "output": usage.get("output_tokens"), "window": None}, "detail": {}})
                    counts["token_count"] = counts.get("token_count", 0) + 1
                if tools:
                    ev = {"kind": "tool_call", "summary": ", ".join(t for t in tools if t)}
                elif texts:
                    ev = {"kind": "assistant", "summary": redact("".join(texts).strip()[:160])}
            elif t == "system":
                ev = {"kind": "task", "summary": str(o.get("subtype") or o.get("content") or "system")[:80]}
            if ev is None:
                continue
            ev.setdefault("detail", {})
            ev.update(ordinal=i, timestamp=o.get("timestamp"))
            counts[ev["kind"]] = counts.get(ev["kind"], 0) + 1
            events.append(ev)
    return {"evidence": "local_data", "found": True, "id": session_id, "path": str(path), "counts": counts, "events": events,
            "meta": {"project": path.parent.name},
            "note": "Claude Code 的 token_count 由 assistant.message.usage 推算：input + cache_read + cache_creation；"
                    "没有本地源码可对照，压缩阈值未知。"}
