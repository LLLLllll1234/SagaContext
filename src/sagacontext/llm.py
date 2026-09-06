from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
import json
import hashlib
from typing import Any, Literal, Protocol

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
    timeout_phase: Literal["connect", "read", "write", "pool", "unknown"] | None = None
    response_digest: str | None = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.class_name)


def _response_digest(response: httpx.Response | None = None, content: object = None) -> str | None:
    if response is not None:
        data = response.content
    elif content is not None:
        data = content if isinstance(content, bytes) else str(content).encode("utf-8", "replace")
    else:
        return None
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class OpenAIJudge:
    """OpenAI-compatible structured-output judge with classified failures."""

    prompt_contract_version = "openai-judge-prompt-v3"
    response_schema_version = "delta-v2"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 5.0,
        temperature: float = 0.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature

    async def judge(self, anchors, candidates, summary):
        if not self.base_url or not self.api_key or not self.model:
            raise JudgeError("judge_configuration_error", False, detail="missing llm configuration")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise JudgeError("judge_configuration_error", False, detail="invalid llm base url")
        system_prompt = (
            "You are a conservative durable-memory reconciler. Return only a JSON object "
            "with a deltas array. Return an empty deltas array for one-off requests, chatter, "
            "irrelevant content, insufficient evidence, or temporary preferences. Relations: "
            "new has no matching anchor; confirm repeats an anchor; refine adds supported detail; "
            "supersede explicitly replaces an anchor; conflict is a plausible but unresolved "
            "disagreement. Never invent a fact to complete a body. Each delta must include "
            "candidate_id, layer, type, relation, anchor_uri, key, fields, evidence_ids, "
            "strong_signal, confidence_hint, and rationale. Copy candidate_id and use topic_key "
            "as key; set layer "
            "from layer_guess and type from memory_type_hint. Copy anchor_uri and evidence IDs "
            "from the input. Use null anchor_uri only for new. "
            "Allowed body fields by type: decision={command}; convention={command}; "
            "gotcha={symptom,fix,applies_when}; taste={format}; project_map={path}. "
            "Put key outside fields. fields is a COMPLETE replacement body, never a patch. "
            "For confirm, copy all anchor.payload fields except key without rewriting them. "
            "For refine, start with all anchor.payload fields except key, preserve existing "
            "supported facts, and add the newly supported detail. Returning only the added "
            "field would erase the other facts. For supersede, replace the contradicted "
            "value while retaining unaffected supported fields. Do not carry the old value "
            "into the replacement value. For conflict, return the supported alternative "
            "body for review; do not invent missing details. "
            "taste.format is a canonical enum: only the lowercase strings json and prose "
            "are valid. Map requests for JSON output to json and prose output to prose; "
            "do not put adjectives, explanations, or the word summaries/results in format. "
            "For free-text fields, use a concise phrase directly supported by the input; "
            "copy unchanged anchor text exactly. Preserve exact commands and paths. "
            "Before returning, check that a refine body contains every existing anchor "
            "field and that enum values use the canonical spelling. If the candidate "
            "does not support a durable change or confirmation, return an empty deltas array."
        )
        user_payload = {
            "anchors": anchors,
            "candidates": [candidate.model_dump() for candidate in candidates],
            "summary": summary,
        }
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.base_url + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.TimeoutException as error:
            phase = next((
                name for error_type, name in (
                    (httpx.ConnectTimeout, "connect"),
                    (httpx.ReadTimeout, "read"),
                    (httpx.WriteTimeout, "write"),
                    (httpx.PoolTimeout, "pool"),
                ) if isinstance(error, error_type)
            ), "unknown")
            raise JudgeError(
                "judge_timeout", True, detail=type(error).__name__, timeout_phase=phase
            ) from error
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in {401, 403}:
                raise JudgeError(
                    "judge_authentication_error", False, status_code=status,
                    detail=f"HTTP {status}", response_digest=_response_digest(error.response)
                ) from error
            if status in {408, 429} or status >= 500:
                name = "judge_rate_limited" if status == 429 else "judge_service_unavailable"
                raise JudgeError(
                    name, True, status_code=status, detail=f"HTTP {status}",
                    response_digest=_response_digest(error.response)
                ) from error
            raise JudgeError(
                "judge_response_error", False, status_code=status, detail=f"HTTP {status}",
                response_digest=_response_digest(error.response)
            ) from error
        except httpx.RequestError as error:
            raise JudgeError("judge_service_unavailable", True, detail=type(error).__name__) from error

        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise JudgeError(
                "judge_response_error", False, detail="missing structured content",
                response_digest=_response_digest(response)
            ) from error
        if not content:
            raise JudgeError(
                "judge_response_error", False, detail="empty structured content",
                response_digest=_response_digest(response)
            )

        try:
            data = json.loads(content) if isinstance(content, str) else content
        except (json.JSONDecodeError, TypeError) as error:
            raise JudgeError(
                "judge_response_error", False, detail="content is not valid JSON",
                response_digest=_response_digest(content=content)
            ) from error
        if not isinstance(data, dict) or "deltas" not in data:
            raise JudgeError(
                "judge_response_error", False, detail="response must contain deltas",
                response_digest=_response_digest(content=content)
            )
        try:
            return TypeAdapter(list[Delta]).validate_python(data["deltas"])
        except (ValidationError, TypeError) as error:
            if isinstance(error, ValidationError):
                locations = [
                    ".".join(str(part) for part in item.get("loc", ())) or "response"
                    for item in error.errors(include_input=False)
                ]
                detail = "invalid delta schema at " + ",".join(locations[:8])
            else:
                detail = "invalid delta schema"
            raise JudgeError(
                "judge_schema_error", False, detail=detail,
                response_digest=_response_digest(content=content)
            ) from error
