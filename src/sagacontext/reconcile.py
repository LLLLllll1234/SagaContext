from __future__ import annotations
import json
import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from .models import Candidate, Delta, MemoryRecord
from .memfile import render

@dataclass(slots=True)
class WritePlan:
    uri: str
    type: str
    content: str
    fields: dict
    mode: str = "create"
    expected_version: int | None = None

@dataclass(slots=True)
class PendingPlan:
    id: str
    layer: str
    type: str
    old_uri: str
    new_summary: str

def compress(turns, budget: int = 6000) -> str:
    """Deterministically retain user text first, then useful assistant text."""
    chunks = []
    for turn in sorted(turns, key=lambda t: 0 if t.role == "user" else 1):
        text = turn.text.strip()
        if not text: continue
        limit = 600 if turn.role == "user" else 300
        chunks.append(f"[{turn.idx}:{turn.role}] {text[:limit]}")
    result = []
    used = 0
    for chunk in chunks:
        cost = max(1, len(chunk) // 4)
        if used + cost > budget: break
        result.append(chunk); used += cost
    return "\n".join(result)

def correction_plan(candidate: Candidate, dev_root: str, repo_key: str, today: date | None = None) -> WritePlan:
    today = today or date.today()
    digest = hashlib.sha1(candidate.text.encode("utf-8")).hexdigest()[:10]
    topic = f"{candidate.kind}_{digest}"
    scope = "global" if candidate.layer_guess in {"user", "preference"} else f"repo-{repo_key}"
    fields = {"version": 1, "topic": topic, "layer": candidate.layer_guess,
              "scope_key": scope, "rule": candidate.text, "confidence": candidate.confidence,
              "evidence_count": 1, "contra_count": 0, "valid_from": today.isoformat(),
              "last_confirmed": today.isoformat(), "status": "active", "origin": "self",
              "evidence": json.dumps([{"turn_idx": candidate.turn_idx, "quote": candidate.text}], ensure_ascii=True)}
    uri = f"{dev_root}/correction/{scope}/{topic}.md"
    return WritePlan(uri=uri, type="dev_correction", content=render(fields), fields=fields)

def confidence(fields: dict[str, Any]) -> float:
    return max(0.1, min(1.0, 0.5 + 0.1 * int(fields.get("evidence_count", 0)) - 0.2 * int(fields.get("contra_count", 0))))

def evolve(existing: MemoryRecord | None, delta: Delta, dev_root: str, repo_key: str, today: date | None = None) -> tuple[list[WritePlan], list[PendingPlan]]:
    today = today or date.today()
    if existing is None:
        scope = "global" if delta.layer in {"user", "preference"} else f"repo-{repo_key}"
        fields = {"version": 1, "topic": delta.key, "layer": delta.layer, "scope_key": scope,
                  **delta.fields, "confidence": max(0.6, delta.confidence_hint), "evidence_count": 1,
                  "contra_count": 0, "valid_from": today.isoformat(), "last_confirmed": today.isoformat(),
                  "status": "active", "origin": "self"}
        leaf = delta.type.removeprefix("dev_")
        if delta.layer == "project":
            leaf = "map" if leaf == "project_map" else leaf
            uri = f"{dev_root}/project/repo-{repo_key}/{leaf}/{delta.key}.md"
        elif delta.layer == "task":
            uri = f"{dev_root}/task/repo-{repo_key}/{delta.key}.md"
        else:
            uri = f"{dev_root}/{leaf}/{scope}/{delta.key}.md"
        return [WritePlan(uri, delta.type, render(fields), fields)], []

    fields = dict(existing.fields)
    fields["version"] = existing.version + 1
    if delta.relation == "confirm":
        fields["evidence_count"] = int(fields.get("evidence_count", 0)) + 1
        fields["last_confirmed"] = today.isoformat()
    elif delta.relation == "refine":
        fields.update(delta.fields)
        fields["evidence_count"] = int(fields.get("evidence_count", 0)) + 1
        fields["last_confirmed"] = today.isoformat()
    elif delta.relation in {"supersede", "conflict"} and not delta.strong_signal:
        fields["contra_count"] = int(fields.get("contra_count", 0)) + 1
        fields["status"] = "pending_confirm"
        pending_id = hashlib.sha1(f"{existing.uri}:{delta.key}:{today}".encode()).hexdigest()[:16]
        pending = PendingPlan(pending_id, delta.layer, delta.type, existing.uri, json.dumps(delta.fields, ensure_ascii=True))
        fields["confidence"] = confidence(fields)
        return [WritePlan(existing.uri, existing.type, render(fields), fields, "update", existing.version)], [pending]
    elif delta.relation == "supersede":
        suffix = today.strftime("%Y%m%d")
        new_uri = existing.uri.removesuffix(".md") + f"-{suffix}.md"
        fields["status"] = "superseded"
        fields["superseded_by"] = new_uri
        fields["contra_count"] = int(fields.get("contra_count", 0)) + 1
        new_fields = {**delta.fields, "version": 1, "topic": delta.key, "layer": delta.layer,
                      "scope_key": existing.fields.get("scope_key", f"repo-{repo_key}"), "confidence": max(0.6, delta.confidence_hint),
                      "evidence_count": 1, "contra_count": 0, "valid_from": today.isoformat(),
                      "last_confirmed": today.isoformat(), "status": "active", "origin": "self"}
        return [WritePlan(existing.uri, existing.type, render(fields), fields, "update", existing.version),
                WritePlan(new_uri, delta.type, render(new_fields), new_fields)], []
    fields["confidence"] = confidence(fields)
    return [WritePlan(existing.uri, existing.type, render(fields), fields, "update", existing.version)], []
