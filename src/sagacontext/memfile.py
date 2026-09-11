from __future__ import annotations
import json, re
from typing import Any
from .models import MemoryRecord

MARKER = "<!-- MEMORY_FIELDS"
def render(fields: dict[str, Any], body: str | None = None) -> str:
    body = body or fields.get("rule") or fields.get("content") or fields.get("decision") or fields.get("goal") or fields.get("symptom") or fields.get("topic", "")
    return f"{body}\n\n{MARKER}\n{json.dumps(fields, ensure_ascii=True, sort_keys=True)}\n-->\n"

def parse(uri: str, content: str, type_: str = "dev_convention", score: float | None = None) -> MemoryRecord:
    match = re.search(r"<!-- MEMORY_FIELDS\s*(\{.*?\})\s*-->", content, re.S)
    fields = json.loads(match.group(1)) if match else {}
    body = content[:match.start()].strip() if match else content.strip()
    return MemoryRecord(uri=uri, type=type_, fields=fields, body=body, version=int(fields.get("version", 1)), score=score)
