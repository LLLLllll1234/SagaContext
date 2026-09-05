#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx


PROBE_VERSION = "g1-openviking-probe-v1"
SCHEMA_VERSION = "projection-v1"
EXPECTED_IMAGE = (
    "ghcr.io/volcengine/openviking@"
    "sha256:14553ec16f2bda9bd08a188cffb659fcfff4fde5891cbb881ed1bd8488b23294"
)
SECRET_KEY = re.compile(r"api[_-]?key|authorization|bearer|token|secret", re.I)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    text = value if isinstance(value, str) else _canonical(value)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return {"type": "array", "length": len(value)}
    if value is None:
        return "null"
    return type(value).__name__


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET_KEY.search(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class BackendCallError(RuntimeError):
    def __init__(self, error_class: str, detail: str, status_code: int | None = None):
        super().__init__(detail)
        self.error_class = error_class
        self.status_code = status_code


class RecordingClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 10.0,
        exchanges: list[dict[str, Any]] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.exchanges = exchanges if exchanges is not None else []

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        require_result: bool = True,
    ) -> dict[str, Any]:
        started = time.monotonic()
        summary: dict[str, Any] = {
            "method": method,
            "path": path,
            "request": {
                "body_keys": sorted(json_body or {}),
                "body_digest": _digest(json_body or {}),
                "body_bytes": len(_canonical(json_body or {}).encode()),
                "query_keys": sorted(params or {}),
            },
        }
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.request(
                    method,
                    self.base_url + path,
                    headers=headers,
                    json=json_body,
                    params=params,
                )
        except httpx.TimeoutException as exc:
            summary["error_class"] = "backend_timeout"
            raise BackendCallError("backend_timeout", type(exc).__name__) from exc
        except httpx.ConnectError as exc:
            summary["error_class"] = "backend_unavailable"
            raise BackendCallError("backend_unavailable", type(exc).__name__) from exc
        finally:
            summary["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
            self.exchanges.append(summary)

        summary["status_code"] = response.status_code
        if response.status_code in {401, 403}:
            summary["error_class"] = "authentication_failed"
            raise BackendCallError(
                "authentication_failed", "credential rejected", response.status_code
            )
        if response.status_code == 404:
            summary["error_class"] = "not_found"
            raise BackendCallError("not_found", "locator absent", response.status_code)
        if response.status_code >= 400:
            summary["error_class"] = "backend_http_error"
            raise BackendCallError(
                "backend_http_error", f"HTTP {response.status_code}", response.status_code
            )
        try:
            payload = response.json()
        except ValueError as exc:
            summary["error_class"] = "response_schema_changed"
            raise BackendCallError("response_schema_changed", "non-JSON response") from exc
        summary["response"] = {
            "body_digest": _digest(_redact(payload)),
            "body_bytes": len(response.content),
            "shape": _shape(payload),
        }
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            summary["error_class"] = "response_schema_changed"
            raise BackendCallError("response_schema_changed", "missing status=ok envelope")
        if require_result and "result" not in payload:
            summary["error_class"] = "response_schema_changed"
            raise BackendCallError("response_schema_changed", "missing result envelope")
        summary["error_class"] = None
        return payload


class _FaultHandler(BaseHTTPRequestHandler):
    mode = "schema"

    def do_GET(self) -> None:  # noqa: N802
        if self.mode == "timeout":
            time.sleep(0.2)
        body = b'{}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, _format: str, *args: Any) -> None:
        return


def _fault_server(mode: str) -> tuple[ThreadingHTTPServer, str]:
    handler = type("FaultHandler", (_FaultHandler,), {"mode": mode})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _collect_uris(value: Any) -> list[str]:
    uris: list[str] = []
    if isinstance(value, dict):
        uri = value.get("uri")
        if isinstance(uri, str):
            uris.append(uri)
        for item in value.values():
            uris.extend(_collect_uris(item))
    elif isinstance(value, list):
        for item in value:
            uris.extend(_collect_uris(item))
    elif isinstance(value, str) and value.startswith("viking://"):
        uris.append(value)
    return list(dict.fromkeys(uris))


def _expect_error(fn: Any, expected: str) -> bool:
    try:
        fn()
    except BackendCallError as exc:
        return exc.error_class == expected
    return False


class G1Probe:
    def __init__(
        self,
        root: Path,
        base_url: str,
        config: Path,
        output_dir: Path,
        visibility_timeout: float,
    ) -> None:
        self.root = root
        self.compose_dir = root / "OpenViking"
        self.base_url = base_url.rstrip("/")
        self.config_path = config
        self.output_dir = output_dir
        self.visibility_timeout = visibility_timeout
        self.config = json.loads(config.read_text())
        self.api_key = self.config["server"]["root_api_key"]
        self.exchanges: list[dict[str, Any]] = []
        self.root_client = RecordingClient(
            self.base_url, self.api_key, exchanges=self.exchanges
        )
        self.client = self.root_client
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.probe_id = f"g1-{stamp}-{uuid.uuid4().hex[:8]}"
        self.assertions: list[dict[str, Any]] = []
        self.visibility: list[dict[str, Any]] = []
        self.cleanup: dict[str, Any] = {"attempted": False, "result": "not_run"}
        self.namespace = ""
        self.probe_user = ""
        self.probe_user_created = False
        self.identities: list[dict[str, Any]] = []

    def check(self, name: str, passed: bool, evidence: Any) -> None:
        self.assertions.append(
            {"name": name, "status": "pass" if passed else "fail", "evidence": evidence}
        )

    def _docker(self, *args: str) -> str:
        return subprocess.run(
            ["docker", *args], check=True, text=True, capture_output=True
        ).stdout.strip()

    def _compose(self, *args: str) -> str:
        return subprocess.run(
            ["docker", "compose", *args],
            cwd=self.compose_dir,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def _identity(self, memory_id: str, revision: int, generation: str) -> dict[str, Any]:
        marker = f"{self.probe_id}-{memory_id}-r{revision}-{generation}"
        normalized = {
            "owner_id": "synthetic-owner",
            "memory_id": memory_id,
            "revision": revision,
            "generation": generation,
            "memory_type": "synthetic_probe",
            "searchable_text": f"SagaContext synthetic projection {marker}",
            "scope_filter_tags": ["owner:synthetic-owner", "scope:probe"],
        }
        operation = {
            "action": "upsert",
            "backend": "openviking",
            "generation": generation,
            "memory_id": memory_id,
            "revision": revision,
        }
        locator = f"{self.namespace}/{generation}/{memory_id}/r{revision}.json"
        return {
            **normalized,
            "payload_digest": _digest(normalized),
            "operation_key": _digest(operation),
            "projection_identity": _digest(normalized),
            "marker": marker,
            "locator": locator,
        }

    def _write(self, identity: dict[str, Any]) -> dict[str, Any]:
        content = _canonical({"schema": SCHEMA_VERSION, "projection": identity})
        return self.client.request(
            "POST",
            "/api/v1/content/write",
            json_body={
                "uri": identity["locator"],
                "content": content,
                "mode": "replace",
                "wait": False,
                "processing_mode": "semantic_and_vectors",
            },
        )

    def _inspect(self, identity: dict[str, Any]) -> dict[str, Any] | None:
        try:
            response = self.client.request(
                "GET",
                "/api/v1/content/read",
                params={"uri": identity["locator"], "raw": True},
            )
        except BackendCallError as exc:
            if exc.error_class == "not_found":
                return None
            raise
        raw = response["result"]
        if not isinstance(raw, str):
            raise BackendCallError("response_schema_changed", "read result is not text")
        parsed, _trailing = json.JSONDecoder().raw_decode(raw.lstrip())
        return parsed.get("projection")

    def _search(self, query: str, target_uri: str | None = None) -> list[str]:
        response = self.client.request(
            "POST",
            "/api/v1/search/find",
            json_body={
                "query": query,
                "target_uri": target_uri or self.namespace,
                "limit": 20,
                "score_threshold": 0.0,
            },
        )
        return _collect_uris(response["result"])

    def _wait_visible(self, identity: dict[str, Any]) -> bool:
        started = time.monotonic()
        polls = 0
        last: list[str] = []
        while time.monotonic() - started < self.visibility_timeout:
            polls += 1
            last = self._search(identity["marker"])
            if identity["locator"] in last:
                elapsed = round(time.monotonic() - started, 3)
                self.visibility.append(
                    {
                        "locator": identity["locator"],
                        "status": "visible",
                        "polls": polls,
                        "elapsed_seconds": elapsed,
                        "timeout_seconds": self.visibility_timeout,
                    }
                )
                return True
            time.sleep(1.0)
        self.visibility.append(
            {
                "locator": identity["locator"],
                "status": "visibility_timeout",
                "polls": polls,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "timeout_seconds": self.visibility_timeout,
                "last_hit_count": len(last),
            }
        )
        return False

    def _wait_absent(self, identity: dict[str, Any]) -> bool:
        started = time.monotonic()
        polls = 0
        while time.monotonic() - started < self.visibility_timeout:
            polls += 1
            try:
                hits = self._search(identity["marker"], self._memory_root())
            except BackendCallError as exc:
                if exc.error_class == "not_found":
                    hits = []
                else:
                    raise
            if identity["locator"] not in hits:
                self.visibility.append(
                    {
                        "locator": identity["locator"],
                        "status": "absent",
                        "polls": polls,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "timeout_seconds": self.visibility_timeout,
                    }
                )
                return True
            time.sleep(1.0)
        return False

    def _memory_root(self) -> str:
        return self.namespace.split("/sagacontext-g1/", 1)[0]

    def _wait_user_absent(self, timeout: float = 30.0) -> bool:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            response = self.root_client.request(
                "GET", "/api/v1/admin/accounts/default/users"
            )
            users = response.get("result")
            if isinstance(users, list) and all(
                item.get("user_id") != self.probe_user
                for item in users
                if isinstance(item, dict)
            ):
                return True
            time.sleep(0.25)
        return False

    def _wait_health(self, timeout: float = 60.0) -> dict[str, Any]:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            try:
                with httpx.Client(timeout=2.0, trust_env=False) as client:
                    response = client.get(self.base_url + "/health")
                if response.status_code == 200 and response.json().get("healthy") is True:
                    return {
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "health": response.json(),
                    }
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.5)
        raise RuntimeError("sidecar health timeout after restart")

    def _error_classification_checks(self) -> None:
        bad_auth = RecordingClient(self.base_url, "synthetic-invalid-key")
        self.check(
            "authentication_failure_classified",
            _expect_error(
                lambda: bad_auth.request("GET", "/api/v1/system/status"),
                "authentication_failed",
            ),
            {"expected": "authentication_failed"},
        )
        self.client.exchanges.extend(bad_auth.exchanges)

        unavailable = RecordingClient("http://127.0.0.1:1", self.api_key, timeout=0.2)
        self.check(
            "backend_unavailable_classified",
            _expect_error(
                lambda: unavailable.request("GET", "/api/v1/system/status"),
                "backend_unavailable",
            ),
            {"expected": "backend_unavailable"},
        )
        self.client.exchanges.extend(unavailable.exchanges)

        timeout_server, timeout_url = _fault_server("timeout")
        try:
            timeout_client = RecordingClient(timeout_url, "", timeout=0.02)
            passed = _expect_error(
                lambda: timeout_client.request("GET", "/probe"), "backend_timeout"
            )
            self.client.exchanges.extend(timeout_client.exchanges)
            self.check(
                "request_timeout_classified",
                passed,
                {"expected": "backend_timeout", "transport": "synthetic_loopback"},
            )
        finally:
            timeout_server.shutdown()

        schema_server, schema_url = _fault_server("schema")
        try:
            schema_client = RecordingClient(schema_url, "")
            passed = _expect_error(
                lambda: schema_client.request("GET", "/probe"),
                "response_schema_changed",
            )
            self.client.exchanges.extend(schema_client.exchanges)
            self.check(
                "response_shape_change_classified",
                passed,
                {"expected": "response_schema_changed", "transport": "synthetic_loopback"},
            )
        finally:
            schema_server.shutdown()

    def _cleanup_namespace(self) -> None:
        self.cleanup["attempted"] = bool(self.namespace)
        namespace_result = "not_run"
        user_result = "not_run"
        try:
            if self.namespace:
                try:
                    response = self.client.request(
                        "DELETE",
                        "/api/v1/fs",
                        params={
                            "uri": self.namespace,
                            "recursive": True,
                            "wait": True,
                            "timeout": self.visibility_timeout,
                        },
                    )
                    namespace_result = "deleted"
                    response_shape = _shape(response)
                except BackendCallError as exc:
                    if exc.error_class != "not_found":
                        raise
                    namespace_result = "already_absent"
                    response_shape = None
                all_absent = all(self._inspect(item) is None for item in self.identities)
                index_absent = all(self._wait_absent(item) for item in self.identities)
            else:
                all_absent = not self.identities
                index_absent = not self.identities
                response_shape = None
            if self.probe_user_created:
                user_response = self.root_client.request(
                    "DELETE",
                    f"/api/v1/admin/accounts/default/users/{self.probe_user}",
                )
                user_absent = user_response.get("status") == "ok" and self._wait_user_absent()
                user_result = "deleted" if user_absent else "delete_unconfirmed"
                self.probe_user_created = not user_absent
            self.cleanup = {
                "attempted": bool(self.namespace or self.probe_user),
                "result": (
                    "pass"
                    if all_absent and index_absent and not self.probe_user_created
                    else "fail"
                ),
                "namespace_result": namespace_result,
                "ephemeral_user_result": user_result,
                "response_shape": response_shape,
                "all_locators_absent": all_absent,
                "all_index_entries_absent": index_absent,
            }
        except Exception as exc:  # cleanup evidence must survive probe failure
            if self.probe_user_created:
                try:
                    self.root_client.request(
                        "DELETE",
                        f"/api/v1/admin/accounts/default/users/{self.probe_user}",
                    )
                    user_absent = self._wait_user_absent()
                    user_result = (
                        "deleted_after_namespace_error"
                        if user_absent
                        else "delete_unconfirmed"
                    )
                    self.probe_user_created = not user_absent
                except Exception:
                    user_result = "delete_failed"
            self.cleanup = {
                "attempted": bool(self.namespace or self.probe_user),
                "result": "fail",
                "namespace_result": namespace_result,
                "ephemeral_user_result": user_result,
                "error_class": getattr(exc, "error_class", type(exc).__name__),
            }

    def run(self) -> dict[str, Any]:
        started_at = _utc_now()
        fatal_error: dict[str, str] | None = None
        metadata: dict[str, Any] = {}
        try:
            configured_images = self._compose("config", "--images").splitlines()
            configured_image = next(
                (item for item in configured_images if item.startswith("ghcr.io/volcengine/")),
                "",
            )
            container = json.loads(self._docker("inspect", "openviking"))[0]
            image = json.loads(self._docker("image", "inspect", EXPECTED_IMAGE))[0]
            repo_digests = image["RepoDigests"]
            labels = image.get("Config", {}).get("Labels") or {}
            with httpx.Client(timeout=15.0, trust_env=False) as client:
                health = client.get(self.base_url + "/health").json()
                openapi = client.get(self.base_url + "/openapi.json").json()
                ready = client.get(self.base_url + "/ready")
            ready_payload = ready.json()
            metadata = {
                "probe_version": PROBE_VERSION,
                "schema_version": SCHEMA_VERSION,
                "code_revision": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.root,
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip(),
                "image_reference": configured_image,
                "image_repo_digests": repo_digests,
                "image_id": image["Id"],
                "image_architecture": image.get("Architecture"),
                "container_image_id": container["Image"],
                "source_revision": labels.get("org.opencontainers.image.revision"),
                "container_version": health.get("version"),
                "api_version": (openapi.get("info") or {}).get("version"),
                "auth_mode": health.get("auth_mode"),
                "config_fingerprint": _digest(_redact(self.config)),
                "ready_status_code": ready.status_code,
                "ready_checks": _redact(ready_payload.get("checks", {})),
            }
            self.check(
                "image_reference_pinned",
                configured_image == EXPECTED_IMAGE and EXPECTED_IMAGE in repo_digests,
                {"configured": configured_image, "expected": EXPECTED_IMAGE},
            )
            self.check(
                "backend_ready",
                ready.status_code == 200 and ready_payload.get("status") == "ready",
                {"status_code": ready.status_code, "status": ready_payload.get("status")},
            )

            self.probe_user = "sagag1-" + uuid.uuid4().hex[:12]
            registration = self.root_client.request(
                "POST",
                "/api/v1/admin/accounts/default/users",
                json_body={"user_id": self.probe_user, "role": "user"},
            )
            user_key = registration["result"].get("user_key")
            if not isinstance(user_key, str) or not user_key:
                raise BackendCallError(
                    "response_schema_changed", "user registration omitted user_key"
                )
            self.probe_user_created = True
            self.client = RecordingClient(
                self.base_url, user_key, exchanges=self.exchanges
            )
            system = self.client.request("GET", "/api/v1/system/status")
            user_id = str(system["result"]["user"])
            user_segment = re.sub(r"[^A-Za-z0-9._-]", "-", user_id)
            self.namespace = (
                f"viking://user/{user_segment}/memories/sagacontext-g1/{self.probe_id}"
            )
            self.check(
                "isolated_namespace",
                user_id == self.probe_user
                and self.namespace.endswith("/" + self.probe_id),
                {
                    "namespace": self.namespace,
                    "payload_class": "synthetic",
                    "credential_class": "ephemeral_user",
                },
            )

            memory_id = "memory-alpha"
            rev1 = self._identity(memory_id, 1, "generation-a")
            rev2 = self._identity(memory_id, 2, "generation-a")
            other_generation = self._identity(memory_id, 1, "generation-b")
            self.identities = [rev1, rev2, other_generation]

            first_write = self._write(rev1)
            observed = self._inspect(rev1)
            self.check(
                "projection_write",
                first_write.get("status") == "ok" and observed is not None,
                {"locator": rev1["locator"], "response_shape": _shape(first_write)},
            )
            mapping_keys = ("memory_id", "revision", "generation", "payload_digest")
            self.check(
                "identity_mapping",
                observed is not None
                and all(observed.get(key) == rev1[key] for key in mapping_keys),
                {key: rev1[key] for key in mapping_keys},
            )
            self.check(
                "exact_locator",
                observed is not None and observed.get("locator") == rev1["locator"],
                {"locator": rev1["locator"]},
            )

            self._write(rev1)
            listing = self.client.request(
                "GET",
                "/api/v1/fs/ls",
                params={"uri": self.namespace, "recursive": True, "simple": True},
            )
            listed = _collect_uris(listing["result"])
            duplicate_count = listed.count(rev1["locator"])
            if duplicate_count == 0:
                duplicate_count = _canonical(listing["result"]).count(rev1["locator"])
            self.check(
                "idempotent_materialize",
                self._inspect(rev1) == observed and duplicate_count <= 1,
                {"locator_occurrences": duplicate_count},
            )

            self._write(rev2)
            self._write(other_generation)
            all_exact = all(self._inspect(item) is not None for item in self.identities)
            self.check(
                "revision_generation_locators",
                all_exact and len({item["locator"] for item in self.identities}) == 3,
                {
                    "mappings": [
                        {
                            "memory_id": item["memory_id"],
                            "revision": item["revision"],
                            "generation": item["generation"],
                            "locator": item["locator"],
                        }
                        for item in self.identities
                    ]
                },
            )

            visibility_passed = all(self._wait_visible(item) for item in self.identities)
            self.check(
                "index_eventually_visible",
                visibility_passed,
                {"observations": self.visibility.copy()},
            )
            current_identity = (memory_id, 2, "generation-a")
            broad_hit_uris = self._search(self.probe_id)
            backend_hits = []
            for item in self.identities:
                if item["locator"] in broad_hit_uris:
                    observed_hit = self._inspect(item)
                    if observed_hit is not None:
                        backend_hits.append(
                            (
                                observed_hit["memory_id"],
                                observed_hit["revision"],
                                observed_hit["generation"],
                            )
                        )
            filtered = [item for item in backend_hits if item == current_identity]
            self.check(
                "old_revision_and_generation_filtered",
                len(backend_hits) == 3 and filtered == [current_identity],
                {"candidate_count": len(backend_hits), "current_count": len(filtered)},
            )

            delete_response = self.client.request(
                "DELETE",
                "/api/v1/fs",
                params={
                    "uri": rev1["locator"],
                    "recursive": False,
                    "wait": True,
                    "timeout": self.visibility_timeout,
                },
            )
            deleted = self._inspect(rev1) is None and self._wait_absent(rev1)
            self.check(
                "precise_delete",
                deleted,
                {"locator": rev1["locator"], "response_shape": _shape(delete_response)},
            )

            restart_started = time.monotonic()
            self._compose("restart", "openviking")
            restart = self._wait_health()
            restart["command_seconds"] = round(time.monotonic() - restart_started, 3)
            persisted = self._inspect(rev2)
            persisted_other = self._inspect(other_generation)
            self.check(
                "locator_survives_restart",
                persisted is not None
                and persisted_other is not None
                and all(persisted.get(key) == rev2[key] for key in mapping_keys),
                restart,
            )

            self._error_classification_checks()
        except Exception as exc:
            fatal_error = {
                "error_class": getattr(exc, "error_class", type(exc).__name__),
                "detail": str(exc)[:240],
            }
        finally:
            self._cleanup_namespace()

        self.check(
            "namespace_cleanup",
            self.cleanup.get("result") == "pass",
            self.cleanup,
        )
        finished_at = _utc_now()
        required_pass = all(item["status"] == "pass" for item in self.assertions)
        result = {
            "probe_id": self.probe_id,
            "gate": "G1",
            "status": "passed" if required_pass and fatal_error is None else "failed_contract",
            "started_at": started_at,
            "finished_at": finished_at,
            "metadata": metadata,
            "namespace": self.namespace,
            "payload_policy": "synthetic_only",
            "fixtures": [
                {
                    "owner_id": item["owner_id"],
                    "memory_id": item["memory_id"],
                    "revision": item["revision"],
                    "generation": item["generation"],
                    "operation_key": item["operation_key"],
                    "projection_identity": item["projection_identity"],
                    "payload_digest": item["payload_digest"],
                    "namespace": self.namespace,
                    "expected_locator": item["locator"],
                }
                for item in self.identities
            ],
            "assertions": self.assertions,
            "visibility": self.visibility,
            "http_exchanges": self.exchanges,
            "cleanup": self.cleanup,
            "fatal_error": fatal_error,
        }
        self._write_artifacts(result)
        return result

    def _write_artifacts(self, result: dict[str, Any]) -> None:
        directory = self.output_dir / self.probe_id
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "g1-openviking.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        rows = "\n".join(
            f"| `{item['name']}` | **{item['status']}** |"
            for item in result["assertions"]
        )
        metadata = result.get("metadata", {})
        waits = ", ".join(
            f"`{item['status']}` {item.get('elapsed_seconds', 0)}s/{item.get('polls', 0)} polls"
            for item in result.get("visibility", [])
        )
        restart = next(
            (
                item["evidence"]
                for item in result["assertions"]
                if item["name"] == "locator_survives_restart"
            ),
            {},
        )
        report = f"""# OpenViking G1 准入探针

**Probe ID：** `{result['probe_id']}`
**状态：** `{result['status']}`
**运行时间：** `{result['started_at']}` 至 `{result['finished_at']}`

## 固定环境

- 容器版本：`{metadata.get('container_version')}`
- API 版本：`{metadata.get('api_version')}`
- 镜像：`{metadata.get('image_reference')}`
- 镜像 ID：`{metadata.get('image_id')}`
- 上游源码 revision：`{metadata.get('source_revision')}`
- 配置 fingerprint：`{metadata.get('config_fingerprint')}`
- namespace：`{result.get('namespace')}`
- payload：仅合成数据；HTTP artifact 不保存正文、key 或响应正文

## 必需断言

| 断言 | 结果 |
|---|---|
{rows}

## 等待与清理

- 可见性观测：{waits or '`not_run`'}
- sidecar 重启：命令 {restart.get('command_seconds')}s；启动后健康恢复 {restart.get('elapsed_seconds')}s
- 清理结果：`{result['cleanup'].get('result')}`
- 精确 locator 全部不可读：`{result['cleanup'].get('all_locators_absent')}`
- 索引条目全部不可见：`{result['cleanup'].get('all_index_entries_absent')}`

完整脱敏请求/响应摘要见同目录 `g1-openviking.json`。G1 仅在全部必需断言为 `pass` 时为 `passed`。
"""
        (directory / "README.md").write_text(report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:1933")
    parser.add_argument(
        "--config", type=Path, default=Path("OpenViking/data/ov.conf")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/probes")
    )
    parser.add_argument("--visibility-timeout", type=float, default=120.0)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = args.config if args.config.is_absolute() else root / args.config
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    result = G1Probe(
        root, args.base_url, config, output_dir, args.visibility_timeout
    ).run()
    print(
        json.dumps(
            {
                "probe_id": result["probe_id"],
                "status": result["status"],
                "passed": sum(a["status"] == "pass" for a in result["assertions"]),
                "failed": sum(a["status"] != "pass" for a in result["assertions"]),
                "artifact": str(output_dir / result["probe_id"]),
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
