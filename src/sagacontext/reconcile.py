from __future__ import annotations
import json
import hashlib
from dataclasses import dataclass
from datetime import date
from .models import Candidate
from .memfile import render

@dataclass(slots=True)
class WritePlan:
    uri: str
    type: str
    content: str
    fields: dict

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
