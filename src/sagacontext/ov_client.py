from __future__ import annotations
from typing import Any
import httpx

class OpenVikingClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 0.6):
        self.base_url = base_url.rstrip("/"); self.timeout = timeout; self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def find(self, query: str, target_directories: list[str], limit: int = 20, score_threshold: float = 0.0) -> list[dict[str, Any]]:
        payload = {"query": query, "target_directories": target_directories, "limit": limit, "score_threshold": score_threshold, "read_content": True}
        async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=self.timeout) as c:
            r = await c.post("/api/v1/search/find", json=payload); r.raise_for_status(); data = r.json()
        return data.get("data", data.get("results", data if isinstance(data, list) else []))

    async def read(self, uri: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=self.timeout) as c:
            r = await c.post("/api/v1/content/read", json={"uri": uri}); r.raise_for_status(); return r.json()

    async def write(self, uri: str, content: str, processing_mode: str = "sync") -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=self.timeout) as c:
            r = await c.post("/api/v1/content/write", json={"uri": uri, "content": content, "processing_mode": processing_mode}); r.raise_for_status(); return r.json()

    @staticmethod
    def content_from_response(payload: dict[str, Any]) -> str:
        data = payload.get("data", payload)
        if isinstance(data, dict):
            return str(data.get("content") or data.get("text") or "")
        return str(data)
