from __future__ import annotations
import json
from typing import Any, Protocol
import httpx
from pydantic import TypeAdapter, ValidationError
from .models import Candidate, Delta

class Judge(Protocol):
    async def judge(self, anchors: list[dict[str, Any]], candidates: list[Candidate], summary: str) -> list[Delta]: ...

class OpenAIJudge:
    """OpenAI-compatible structured-output judge; retries once at temperature 0."""
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 5.0):
        self.base_url, self.api_key, self.model, self.timeout = base_url.rstrip("/"), api_key, model, timeout

    async def judge(self, anchors, candidates, summary):
        payload = {"model": self.model, "temperature": 0.1, "messages": [
            {"role": "system", "content": "You are a conservative memory reconciler. Return only a JSON array of deltas."},
            {"role": "user", "content": json.dumps({"anchors": anchors, "candidates": [c.model_dump() for c in candidates], "summary": summary}, ensure_ascii=True)},
        ], "response_format": {"type": "json_object"}}
        for attempt in range(2):
            try:
                if attempt: payload["temperature"] = 0
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(self.base_url + "/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
                    response.raise_for_status()
                    content = response.json()["choices"][0]["message"]["content"]
                data = json.loads(content)
                if isinstance(data, dict): data = data.get("deltas", [])
                return TypeAdapter(list[Delta]).validate_python(data)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError, ValidationError, TypeError):
                if attempt: return []
        return []
