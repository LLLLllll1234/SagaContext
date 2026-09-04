from __future__ import annotations
from .memfile import parse
from .models import MemoryRecord

def _tokens(text: str) -> int: return max(1, len(text) // 4)
def render(items: list[MemoryRecord], budget: int = 2000) -> str:
    out, used = [], 0
    for it in sorted(items, key=lambda x: (float(x.fields.get("confidence", 0)), x.score or 0), reverse=True):
        f = it.fields; scope = f.get("scope_key", "global").split("-", 1)[0]
        body = f.get("rule") or f.get("content") or f.get("decision") or f.get("responsibility") or f.get("symptom") or f.get("goal") or it.body
        body = str(body).replace("<", "&lt;").replace(">", "&gt;")
        block = f'<memory uri="{it.uri}" type="{it.type}" layer="{f.get("layer", "preference")}" scope="{scope}" confidence="{float(f.get("confidence", 0)):.2f}">{body}</memory>'
        cost = _tokens(block)
        if used + cost > budget: break
        out.append(block); used += cost
    return "\n".join(out)

def records(results: list[dict]) -> list[MemoryRecord]:
    out = []
    for r in results:
        uri = r.get("uri") or r.get("path")
        content = r.get("content") or r.get("text") or r.get("abstract", "")
        if uri: out.append(parse(uri, content, r.get("type", "dev_convention"), r.get("score")))
    return out
