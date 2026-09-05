import hashlib
import json
import unittest

import httpx

from sagacontext.backends import (
    BackendDefiniteError, BackendUnknownError, BackendVerificationTimeout,
    OpenVikingBackendAdapter, Projection,
)
from sagacontext.backends.openviking import canonical


class OpenVikingContractTests(unittest.TestCase):
    def setUp(self):
        self.items = {}
        self.writes = 0
        self.fault = None
        self.backend = OpenVikingBackendAdapter(
            "http://backend.invalid", "synthetic", owner_id="owner",
            namespace="viking://user/test/memories/sagacontext/contract",
            transport=httpx.MockTransport(self.request),
        )
        payload = dict(owner_id="owner", memory_id="a/../b", revision=1,
                       generation="g1", memory_type="decision", searchable_text="text",
                       scope_filter_tags=["owner:owner", "scope:global"])
        self.projection = Projection(**payload, payload_digest=hashlib.sha256(canonical(payload).encode()).hexdigest())

    def tearDown(self):
        self.backend.close()

    def request(self, request):
        if self.fault == "auth":
            return httpx.Response(403)
        if self.fault == "schema":
            return httpx.Response(200, json={})
        if request.url.path.endswith("/write"):
            body = json.loads(request.content)
            self.items[body["uri"]] = body["content"]
            self.writes += 1
            if self.fault == "lost_response":
                raise httpx.ReadTimeout("synthetic")
            result = {}
        elif request.url.path.endswith("/read"):
            uri = request.url.params["uri"]
            if uri not in self.items:
                return httpx.Response(404)
            result = self.items[uri] + '\n<!-- backend metadata -->'
        elif request.url.path.endswith("/find"):
            result = {"memories": [{"uri": uri} for uri in self.items]}
        else:
            del self.items[request.url.params["uri"]]
            result = {}
        return httpx.Response(200, json={"status": "ok", "result": result})

    def test_duplicate_and_client_recovery_use_exact_identity(self):
        self.fault = "lost_response"
        with self.assertRaises(BackendUnknownError):
            self.backend.materialize(self.projection, "op")
        self.fault = None
        locator = self.backend.locate_projection("a/../b", 1, "g1", "op")
        self.assertEqual(self.backend.materialize(self.projection, "op"), locator)
        self.assertEqual(self.writes, 1)
        with self.assertRaises(BackendDefiniteError):
            self.backend.materialize(self.projection, "other-op")
        self.assertEqual(self.backend.search("text", "g2"), [])
        self.assertEqual(len(self.backend.search("text", "g1")), 1)
        self.assertEqual(self.backend.remove_projection([locator, locator]), 1)
        self.assertIsNone(self.backend.inspect_projection(locator))

    def test_tampering_and_cross_namespace_fail_closed(self):
        locator = self.backend.materialize(self.projection, "op")
        body = json.loads(self.items[locator])
        body["projection"]["searchable_text"] = "tampered"
        self.items[locator] = canonical(body)
        with self.assertRaises(BackendDefiniteError):
            self.backend.inspect_projection(locator)
        with self.assertRaises(BackendDefiniteError):
            self.backend.remove_projection(["viking://user/other/memories/private"])

    def test_auth_and_read_schema_errors_are_classified(self):
        self.fault = "auth"
        with self.assertRaisesRegex(BackendDefiniteError, "authentication_failed"):
            self.backend.locate_projection("a", 1, "g1")
        self.fault = "schema"
        with self.assertRaises(BackendVerificationTimeout):
            self.backend.locate_projection("a", 1, "g1")

    def test_delete_racing_with_another_delete_is_idempotent(self):
        locator = self.backend.materialize(self.projection, "op")
        original = self.request
        def concurrent_delete(request):
            if request.method == "DELETE":
                self.items.pop(request.url.params["uri"], None)
                return httpx.Response(404)
            return original(request)
        self.backend.client.close()
        self.backend.client = httpx.Client(transport=httpx.MockTransport(concurrent_delete), base_url="http://backend.invalid")
        self.assertEqual(self.backend.remove_projection([locator]), 1)
        self.assertIsNone(self.backend.inspect_projection(locator))
