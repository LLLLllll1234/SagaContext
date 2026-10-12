"""Joins local data into per-memory lifecycles. Everything here is ``inference``.

The chain we can reconstruct for Codex, per thread:

    rollout.jsonl (会话)  --stage1 job-->  stage1_outputs 行 (候选)
        --phase2 selection-->  rollout_summaries/<file>.md (证据文件)
        --consolidation agent-->  MEMORY.md 的 Task Group 块 + memory_summary.md 行 (资产)
        --session start-->  developer 指令里的 MEMORY_SUMMARY (召回)
        --<oai-mem-citation>-->  usage_count+1 (引用)
        --30d 未用 / 不在选集-->  summary 文件删除 → agent 移除 (取代/遗忘)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import codex_data as cd


def build_lifecycles(home: Path | None = None) -> dict[str, Any]:
    home = home or cd.default_codex_home()
    threads = {t["id"]: t for t in cd.list_threads(home, limit=2000)}
    stage1 = {s["thread_id"]: s for s in cd.stage1_outputs(home)}
    summaries = cd.rollout_summaries(home)
    by_summary_thread = {s["thread_id"]: s for s in summaries if s["thread_id"]}
    blocks = cd.memory_md_blocks(home)
    jobs = cd.memory_jobs(home)
    raw_threads = {r["thread_id"]: r for r in cd.raw_memories_threads(home)}
    citations = cd.scan_citations(home)
    consolidations = [j for j in jobs if j["kind"] == "memory_consolidate_global"]
    stage1_jobs = {j["job_key"]: j for j in jobs if j["kind"] == "memory_stage1"}

    cited_by: dict[str, list[dict[str, Any]]] = {}
    for c in citations:
        for rid in c["rollout_ids"]:
            cited_by.setdefault(rid, []).append({"citing_thread": c["citing_thread"], "timestamp": c["timestamp"],
                                                 "entries": c["entries"]})

    blocks_by_thread: dict[str, list[int]] = {}
    for idx, b in enumerate(blocks):
        for t in b["threads"]:
            blocks_by_thread.setdefault(t, []).append(idx)

    lifecycles: list[dict[str, Any]] = []
    all_ids = set(stage1) | set(by_summary_thread) | set(blocks_by_thread) | set(raw_threads)
    for tid in all_ids:
        t = threads.get(tid)
        s1 = stage1.get(tid)
        job = stage1_jobs.get(tid)
        summ = by_summary_thread.get(tid)
        stages: list[dict[str, Any]] = []
        stages.append({"stage": "session", "label": "原始会话", "status": "present" if t else "unknown",
                       "at": t["updated_at"] if t else None,
                       "note": (t["title"] if t else "state_5.sqlite 中无此线程记录"),
                       "evidence": "local_data" if t else "inference"})
        stages.append({"stage": "candidate", "label": "阶段一候选（stage1_outputs）",
                       "status": "present" if s1 else ("dropped" if (summ or tid in blocks_by_thread) else "absent"),
                       "at": s1["generated_at"] if s1 else None,
                       "note": (f"raw_memory {s1['raw_memory_chars']} 字 · 摘要 {s1['rollout_summary_chars']} 字 · 任务组 {s1['task_group'] or '-'}" if s1
                                else "SQLite 中已无该行（可能被 30 天保留策略清理），但文件层仍引用它"),
                       "job": job, "evidence": "local_data" if s1 else "inference"})
        stages.append({"stage": "selected", "label": "进入阶段二整合",
                       "status": "present" if (s1 and s1["selected_for_phase2"]) else ("absent" if s1 else "unknown"),
                       "at": None,
                       "note": ("selected_for_phase2=1：上次整合把它喂给了 agent" if s1 and s1["selected_for_phase2"]
                                else "selected_for_phase2=0：上次整合未使用（30 天未引用或超出 256 上限）" if s1 else "无 SQLite 行可判断"),
                       "evidence": "local_data" if s1 else "inference"})
        stages.append({"stage": "summary_file", "label": "证据文件 rollout_summaries/*.md",
                       "status": "present" if summ else "absent",
                       "at": summ["updated_at"] if summ else None,
                       "note": summ["file"] if summ else "文件不存在：未入选或已被 prune_rollout_summaries 删除",
                       "file": summ["file"] if summ else None, "evidence": "local_data"})
        idxs = blocks_by_thread.get(tid, [])
        stages.append({"stage": "asset", "label": "长期记忆资产 MEMORY.md",
                       "status": "present" if idxs else "absent", "at": None,
                       "note": ("；".join(blocks[i]["title"] for i in idxs) if idxs else "MEMORY.md 中没有引用该线程的 Task Group"),
                       "blocks": [{"index": i, "title": blocks[i]["title"], "line_start": blocks[i]["line_start"], "line_end": blocks[i]["line_end"]} for i in idxs],
                       "evidence": "local_data"})
        uses = cited_by.get(tid, [])
        stages.append({"stage": "recall", "label": "被后续会话引用",
                       "status": "present" if (uses or (s1 and s1["usage_count"])) else "absent",
                       "at": (s1["last_usage"] if s1 else None) or (uses[-1]["timestamp"] if uses else None),
                       "note": (f"usage_count={s1['usage_count'] if s1 else '?'} · rollout 中找到 {len(uses)} 次 <oai-mem-citation> 引用"),
                       "uses": uses[:20], "evidence": "local_data"})
        # supersede / forget
        forgotten = bool((idxs or raw_threads.get(tid)) and not summ)
        stages.append({"stage": "forget", "label": "取代 / 遗忘",
                       "status": "present" if forgotten else "absent", "at": None,
                       "note": ("证据文件已被删除但资产仍引用：下次整合时 agent 会看到删除 diff 并按提示词移除相关记忆" if forgotten
                                else "未观察到遗忘信号"),
                       "evidence": "inference"})
        lifecycles.append({"thread_id": tid, "title": (t["title"] if t else (summ["title"] if summ else s1["description"] if s1 else tid)),
                           "cwd": (t["cwd"] if t else (summ["cwd"] if summ else None)),
                           "updated_at": t["updated_at"] if t else None,
                           "usage_count": s1["usage_count"] if s1 else 0,
                           "in_memory_md": bool(idxs), "has_summary_file": bool(summ), "in_stage1": bool(s1),
                           "selected": bool(s1 and s1["selected_for_phase2"]), "stages": stages})
    lifecycles.sort(key=lambda x: (x["in_memory_md"], x["usage_count"], x["updated_at"] or ""), reverse=True)

    # asset view: each MEMORY.md block with its provenance
    assets = []
    for idx, b in enumerate(blocks):
        provenance = []
        for tid in b["threads"]:
            t = threads.get(tid)
            s1 = stage1.get(tid)
            provenance.append({"thread_id": tid, "title": t["title"] if t else None, "cwd": t["cwd"] if t else None,
                               "updated_at": t["updated_at"] if t else None, "rollout_exists": t["rollout_exists"] if t else False,
                               "summary_file": by_summary_thread.get(tid, {}).get("file"),
                               "usage_count": s1["usage_count"] if s1 else None, "in_stage1": bool(s1)})
        recalls = [u for tid in b["threads"] for u in cited_by.get(tid, [])]
        recalls.sort(key=lambda u: u["timestamp"] or "", reverse=True)
        # NOTE: <citation_entries> carry MEMORY.md line ranges, but MEMORY.md is rewritten by every
        # consolidation, so old line numbers do not map onto the current file. We only trust rollout_ids.
        assets.append({"index": idx, "title": b["title"], "line_start": b["line_start"], "line_end": b["line_end"],
                       "sections": b["sections"], "threads": b["threads"], "summary_files": b["summary_files"],
                       "provenance": provenance, "recall_count": len(recalls), "recalls": recalls[:30],
                       "text": b["text"], "evidence": "local_data"})

    return {"evidence": "inference", "lifecycles": lifecycles, "assets": assets,
            "consolidations": consolidations, "citations_total": len(citations),
            "counts": {"threads": len(threads), "stage1": len(stage1), "summary_files": len(summaries),
                       "memory_blocks": len(blocks), "raw_threads": len(raw_threads), "citations": len(citations),
                       "forgotten": sum(1 for l in lifecycles if l["stages"][-1]["status"] == "present")}}


def context_curve(rollout: dict[str, Any]) -> dict[str, Any]:
    """Derive the short-term-memory story from a parsed rollout: growth, compaction, what survived."""
    points = []
    compactions = []
    window = None
    for ev in rollout.get("events", []):
        if ev["kind"] == "token_count" and ev.get("tokens"):
            tk = ev["tokens"]
            window = tk.get("window") or window
            points.append({"ordinal": ev["ordinal"], "timestamp": ev["timestamp"], "context": tk.get("context") or 0,
                           "cached": tk.get("cached") or 0, "cumulative": tk.get("cumulative") or 0})
        elif ev["kind"] == "compacted":
            before = points[-1]["context"] if points else None
            compactions.append({"ordinal": ev["ordinal"], "timestamp": ev["timestamp"], "context_before": before,
                                "window_number": ev["detail"].get("window_number"), "kept": ev["detail"].get("kept"),
                                "summary_chars": ev["detail"].get("message_chars"),
                                "summary_text": ev["detail"].get("summary_text"),
                                "kept_user_messages": ev["detail"].get("kept_user_messages")})
    # context_after = first token_count after each compaction
    for c in compactions:
        after = next((p for p in points if p["ordinal"] > c["ordinal"] and p["context"]), None)
        c["context_after"] = after["context"] if after else None
    peak = max((p["context"] for p in points), default=0)
    threshold = int(window * 0.9) if window else None
    return {"evidence": "inference", "window": window, "auto_compact_threshold": threshold, "peak_context": peak,
            "points": points, "compactions": compactions,
            "note": ("阈值按源码 (context_window*9)/10 推算；实际判定用的是 last_token_usage + 追加项估算，"
                     "所以压缩点可能略早于曲线触碰阈值。" if window else "该会话未记录 model_context_window")}
