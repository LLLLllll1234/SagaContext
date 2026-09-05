from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from .base import BackendCapabilities, BackendHit, Projection
from .errors import BackendDefiniteError, BackendUnknownError, BackendVerificationTimeout


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class OpenVikingBackendAdapter:
    """G1 HTTP contract. Each instance owns one explicitly assigned managed area."""

    def __init__(
        self, base_url: str, api_key: str, *, namespace: str, owner_id: str,
        timeout: float = 5.0, transport: httpx.BaseTransport | None = None,
    ):
        if not re.fullmatch(
            r"viking://user/[A-Za-z0-9._-]+/memories/sagacontext/[A-Za-z0-9_-]+", namespace
        ):
            raise ValueError("an explicit SagaContext managed namespace is required")
        if not owner_id or timeout <= 0:
            raise ValueError("owner_id and positive timeout are required")
        self.namespace = namespace
        self.owner_id = owner_id
        self.timeout = timeout
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"), headers={"X-API-Key": api_key},
            timeout=timeout, trust_env=False, transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend="openviking", version="v0.4.17.1/http-0.1.0/projection-v1",
            stable_id_mapping=True, enumerable_managed_area=True,
            visibility_check=True, exactly_once=False,
        )

    def _request(self, method: str, path: str, *, mutation: bool = False, **kwargs):
        uncertain = BackendUnknownError if mutation else BackendVerificationTimeout
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.TransportError:
            raise uncertain("backend_transport_unavailable") from None
        if response.status_code in (401, 403):
            raise BackendDefiniteError("authentication_failed")
        if response.status_code == 404 and (not mutation or method == "DELETE"):
            return None
        if response.status_code >= 500 or response.status_code in (408, 429):
            raise uncertain("backend_http_unavailable")
        if response.status_code >= 400:
            raise BackendDefiniteError("backend_request_rejected")
        try:
            body = response.json()
        except ValueError:
            raise uncertain("response_schema_changed") from None
        if not isinstance(body, dict) or body.get("status") != "ok" or "result" not in body:
            raise uncertain("response_schema_changed")
        return body["result"]

    def _locator(self, memory_id: str, revision: int, generation: str) -> str:
        if not memory_id or not generation or revision < 1:
            raise BackendDefiniteError("invalid_projection_identity")
        # Hash untrusted IDs so slashes and URI escape sequences cannot change ownership.
        key = hashlib.sha256(canonical([generation, memory_id, revision]).encode()).hexdigest()
        return f"{self.namespace}/{key}.json"

    def _check_locator(self, locator: str) -> None:
        if not re.fullmatch(re.escape(self.namespace) + r"/[0-9a-f]{64}\.json", locator):
            raise BackendDefiniteError("locator_outside_managed_area")

    def _validate(self, projection: Projection) -> None:
        payload = projection.model_dump(exclude={"payload_digest"})
        digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
        if projection.owner_id != self.owner_id or projection.payload_digest != digest:
            raise BackendDefiniteError("projection_identity_mismatch")

    def _read(self, locator: str) -> tuple[Projection, str] | None:
        self._check_locator(locator)
        raw = self._request("GET", "/api/v1/content/read", params={"uri": locator, "raw": True})
        if raw is None:
            return None
        try:
            # G1 observed metadata appended after the JSON document.
            body, _ = json.JSONDecoder().raw_decode(raw.lstrip())
            if body["schema"] != "projection-v1" or not isinstance(body["operation_key"], str):
                raise ValueError
            projection = Projection.model_validate(body["projection"])
        except (ValueError, TypeError, KeyError, AttributeError, ValidationError):
            raise BackendDefiniteError("projection_schema_mismatch") from None
        self._validate(projection)
        if locator != self._locator(projection.memory_id, projection.revision, projection.generation):
            raise BackendDefiniteError("projection_locator_mismatch")
        return projection, body["operation_key"]

    def materialize(self, projection: Projection, operation_key: str) -> str:
        self._validate(projection)
        if not operation_key:
            raise BackendDefiniteError("operation_key_required")
        locator = self._locator(projection.memory_id, projection.revision, projection.generation)
        observed = self._read(locator)
        if observed:
            if observed != (projection, operation_key):
                raise BackendDefiniteError("projection_operation_collision")
            return locator
        self._request(
            "POST", "/api/v1/content/write", mutation=True,
            json={"uri": locator, "content": canonical({
                "schema": "projection-v1", "operation_key": operation_key,
                "projection": projection.model_dump(),
            }), "mode": "replace", "wait": False, "processing_mode": "semantic_and_vectors"},
        )
        return locator

    def locate_projection(self, memory_id, revision, generation, operation_key=None) -> str | None:
        locator = self._locator(memory_id, revision, generation)
        observed = self._read(locator)
        if observed is None:
            return None
        if operation_key is not None and observed[1] != operation_key:
            raise BackendDefiniteError("projection_operation_collision")
        return locator

    def inspect_projection(self, locator: str) -> Projection | None:
        observed = self._read(locator)
        return observed[0] if observed else None

    def search(self, query: str, generation: str, limit: int = 10) -> list[BackendHit]:
        if limit < 1:
            return []
        result = self._request("POST", "/api/v1/search/find", json={
            "query": query, "target_uri": self.namespace, "limit": limit, "score_threshold": 0.0,
        })
        if result is None:
            return []
        if not isinstance(result, dict):
            raise BackendVerificationTimeout("response_schema_changed")
        candidates = result.get("memories")
        if not isinstance(candidates, list):
            raise BackendVerificationTimeout("response_schema_changed")
        hits = []
        seen = set()
        for item in candidates:
            if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
                raise BackendVerificationTimeout("response_schema_changed")
            locator = item["uri"]
            if locator in seen or not locator.startswith(self.namespace + "/"):
                continue
            seen.add(locator)
            try:
                projection = self.inspect_projection(locator)
            except BackendDefiniteError:
                continue
            if projection is not None and projection.generation == generation:
                hits.append(BackendHit(
                    memory_id=projection.memory_id, revision=projection.revision,
                    generation=generation, rank=len(hits) + 1, backend_locator=locator,
                ))
        return hits[:limit]

    def remove_projection(self, locators: list[str]) -> int:
        for locator in locators:
            self._check_locator(locator)
        removed = 0
        for locator in dict.fromkeys(locators):
            if self._read(locator) is None:
                continue
            self._request("DELETE", "/api/v1/fs", mutation=True, params={
                "uri": locator, "recursive": False, "wait": True, "timeout": self.timeout,
            })
            removed += 1
        return removed
