from __future__ import annotations
import asyncio
from .memfile import parse
from .models import MemoryRecord
from .ov_client import OpenVikingClient
from .store import Store

async def select(host: str, session_id: str, summary: str, dev_root: str, store: Store, client: OpenVikingClient, limit: int = 20) -> list[MemoryRecord]:
    seen = store.recalled_uris(host, session_id)
    try:
        hits = await client.find(summary[:1000], [dev_root], limit=60, score_threshold=0.3)
    except Exception:
        hits = []
    scores = {str(hit.get("uri") or hit.get("path")): hit.get("score") for hit in hits if hit.get("uri") or hit.get("path")}
    uris = list(dict.fromkeys([*sorted(seen), *scores]))[:limit]

    def memory_type(uri: str) -> str:
        segment = uri.removesuffix(".md").split("/")
        known = {"profile", "taste", "convention", "correction", "decision", "map", "gotcha", "task"}
        name = next((part for part in reversed(segment) if part in known), "convention")
        return f"dev_{'project_map' if name == 'map' else name}"

    async def read_one(uri: str):
        try:
            payload = await client.read(uri)
            content = client.content_from_response(payload)
            return parse(uri, content, memory_type(uri), scores.get(uri))
        except Exception:
            return None

    records = await asyncio.gather(*(read_one(uri) for uri in uris))
    return [record for record in records if record is not None]
