from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
import json
from typing import Any, Protocol

import httpx
from pydantic import TypeAdapter, ValidationError
from .models import Candidate, Delta

class Judge(Protocol):
    async def judge(self, anchors: list[dict[str, Any]], candidates: list[Candidate], summary: str) -> list[Delta]: ...


@dataclass(frozen=True, slots=True)
class JudgeError(RuntimeError):
    class_name: str
    retryable: bool
    attempts: int = 1
    status_code: int | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.class_name)


class OpenAIJudge:
    """OpenAI-compatible structured-output judge with classified failures."""

    prompt_contract_version = "openai-judge-prompt-v1"
    response_schema_version = "delta-v2"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 5.0):
        self.base_url, self.api_key, self.model, self.timeout = base_url.rstrip("/"), api_key, model, timeout

    async def judge(self, anchors, candidates, summary):
        if not self.base_url or not self.api_key or not self.model:
            raise JudgeError("judge_configuration_error", False, detail="missing llm configuration")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise JudgeError("judge_configuration_error", False, detail="invalid llm base url")
        payload = {"model": self.model, "temperature": 0.1, "messages": [
            {"role": "system", "content": (
                "You are a conservative memory reconciler. Return only a JSON object "
                "with a deltas array. Each delta must include candidate_id, relation, "
                "anchor_uri, key, fields, evidence_ids, and rationale. "
                "Do not invent candidates, anchors, revisions, scopes, or evidence."
            )},
            {"role": "user", "content": json.dumps({"anchors": anchors, "candidates": [c.model_dump() for c in candidates], "summary": summary}, ensure_ascii=True)},
        ], "response_format": {"type": "json_object"}}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.base_url + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.TimeoutException as error:
            raise JudgeError("judge_timeout", True, detail=type(error).__name__) from error
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in {401, 403}:
                raise JudgeError("judge_authentication_error", False, status_code=status) from error
            if status in {408, 429} or status >= 500:
                name = "judge_rate_limited" if status == 429 else "judge_service_unavailable"
                raise JudgeError(name, True, status_code=status) from error
            raise JudgeError("judge_response_error", False, status_code=status) from error
        except httpx.RequestError as error:
            raise JudgeError("judge_service_unavailable", True, detail=type(error).__name__) from error

        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise JudgeError("judge_response_error", False, detail="missing structured content") from error
        if not content:
            raise JudgeError("judge_response_error", False, detail="empty structured content")

        try:
            data = json.loads(content) if isinstance(content, str) else content
        except (json.JSONDecodeError, TypeError) as error:
            raise JudgeError("judge_response_error", False, detail="content is not valid JSON") from error
        if not isinstance(data, dict) or "deltas" not in data:
            raise JudgeError("judge_response_error", False, detail="response must contain deltas")
        try:
            return TypeAdapter(list[Delta]).validate_python(data["deltas"])
        except (ValidationError, TypeError) as error:
            raise JudgeError("judge_schema_error", False, detail="invalid delta schema") from error
