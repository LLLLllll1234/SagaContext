#!/usr/bin/env python3
"""Explicit S3-1 synthetic run; uses the admitted sidecar, never normal host hooks."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from probe_openviking_backend import EXPECTED_IMAGE, RecordingClient, _collect_uris, _digest
from sagacontext.backends import OpenVikingBackendAdapter
from sagacontext.ledger import CommitRequest, Ledger, Scope
from sagacontext.projection import Projector


class FaultTransport(httpx.BaseTransport):
    def __init__(self, exchanges):
        self.inner = httpx.HTTPTransport()
        self.exchanges = exchanges
        self.drop_write = False
        self.fail_reads = False

    def handle_request(self, request):
        started = time.monotonic()
        record = {"method": request.method, "path": request.url.path,
                  "request_digest": hashlib.sha256(request.content).hexdigest()}
        self.exchanges.append(record)
        try:
            if self.fail_reads and request.url.path.endswith("/read"):
                record["fault"] = "verification_transport_timeout"
                raise httpx.ReadTimeout("synthetic verification timeout")
            response = self.inner.handle_request(request)
            response.read()
            record["status_code"] = response.status_code
            if self.drop_write and request.url.path.endswith("/write") and response.status_code == 200:
                self.drop_write = False
                record["fault"] = "drop_response_after_real_write"
                response.close()
                raise httpx.ReadTimeout("response lost after real write")
            return response
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)

    def close(self):
        self.inner.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:1933")
    parser.add_argument("--config", type=Path, default=Path("OpenViking/data/ov.conf"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/probes"))
    parser.add_argument("--policy-stages", action="store_true")
    parser.add_argument("--longitudinal", action="store_true")
    args = parser.parse_args()
    run_id = "s3-1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = args.output_dir / run_id
    output.mkdir(parents=True)
    report = {"run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
              "status": "running", "assertions": [], "traces": [], "exchanges": [], "cleanup": {}}
    config = json.loads(args.config.read_text())
    admin = RecordingClient(args.base_url, config["server"]["root_api_key"])
    user = "sagas31-" + uuid.uuid4().hex[:12]
    user_created = False
    backend = None
    ledger = None
    namespace = None
    locators = []

    def check(name, value, evidence=None):
        report["assertions"].append({"name": name, "status": "pass" if value else "fail", "evidence": evidence})
        if not value:
            raise AssertionError(name)

    try:
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", EXPECTED_IMAGE]))[0]
        container = json.loads(subprocess.check_output(["docker", "inspect", "openviking"]))[0]
        report["environment"] = {
            "image_digest": EXPECTED_IMAGE, "image_id": image["Id"],
            "container_image_id": container["Image"],
            "code_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "source_digests": {str(path): _digest(path.read_text()) for path in sorted([
                *Path("src/sagacontext").rglob("*.py"), *Path("scripts").glob("*.py")])},
            "config_digest": _digest(args.config.read_bytes().hex()),
            "payload_class": "synthetic", "credential_class": "ephemeral_user",
        }
        check("admitted_image", image["Id"] == container["Image"] and EXPECTED_IMAGE in image["RepoDigests"])
        response = admin.request("POST", "/api/v1/admin/accounts/default/users", json_body={"user_id": user, "role": "user"})
        user_created = True
        key = response["result"]["user_key"]
        user_client = RecordingClient(args.base_url, key)
        namespace = f"viking://user/{user}/memories/sagacontext/{run_id}"

        def connect():
            transport = FaultTransport(report["exchanges"])
            return OpenVikingBackendAdapter(args.base_url, key, namespace=namespace,
                                           owner_id="synthetic-owner", timeout=3, transport=transport), transport

        backend, transport = connect()
        with tempfile.TemporaryDirectory(prefix="sagacontext-s31-") as directory:
            db_path = Path(directory) / "ledger.db"
            ledger = Ledger(db_path, owner_id="synthetic-owner")
            ledger.register_backend_generation("openviking", "g1")
            projector = Projector(ledger)

            def commit(name, memory_id=None, revision=None):
                return ledger.commit(CommitRequest(receipt=name, operation="refine" if memory_id else "new",
                    memory_id=memory_id, expected_revision=revision, memory_type="decision",
                    scope=Scope(kind="global"), payload={"decision": run_id + " " + name}))

            def now():
                return datetime.now(timezone.utc)

            def drain(**kwargs):
                return projector.drain_once(backend, worker_id="worker", now=now(),
                    backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2),
                    lease_duration=timedelta(seconds=30), verification_timeout=timedelta(seconds=3), **kwargs)

            def claim(worker="old"):
                return projector.claim_next(backend, worker_id=worker, now=now(),
                    backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2),
                    lease_duration=timedelta(seconds=30))

            def trace(name):
                report["traces"].append({"scenario": name, **{table: [dict(row) for row in ledger.db.execute(
                    "SELECT " + fields + " FROM " + table)] for table, fields in {
                    "outbox": "outbox_id,memory_id,revision,action,status,attempt_count,confirmed_receipt_id,last_error_class",
                    "projection_attempts": "outbox_id,operation_key,attempt_no,result_status,error_class,observed_locator",
                    "projection_receipts": "receipt_id,operation_key,action,memory_id,revision,generation,backend_locator,payload_digest",
                }.items()}})

            first = commit("p1")
            ledger.close()
            ledger = Ledger(db_path, owner_id="synthetic-owner")
            projector = Projector(ledger)
            check("P1_durable_outbox_after_reopen", drain().status == "confirmed")
            trace("P1")

            second = commit("p2")
            transport.drop_write = True
            check("P2_unknown_after_real_write", drain().status == "unknown")
            writes = sum(e["path"].endswith("/write") for e in report["exchanges"])
            trace("P2_unknown")
            backend.close()
            backend, transport = connect()
            ledger.close()
            ledger = Ledger(db_path, owner_id="synthetic-owner")
            projector = Projector(ledger)
            recovered = drain()
            check("P2_restart_locates_without_rewrite", recovered.status == "confirmed" and recovered.recovered and
                  writes == sum(e["path"].endswith("/write") for e in report["exchanges"]))
            trace("P2_recovered")

            third = commit("p3-old")
            old = claim()
            commit("p3-new", third.memory_id, 1)
            check("P3_new_revision_confirmed", drain().status == "confirmed")
            old_locator = projector.call_backend(old, backend, now=now())
            check("P3_old_write_arrives_late", projector.complete(old, backend, old_locator, now=now()).status == "obsolete")
            check("P3_obsolete_cleanup", drain().status == "confirmed" and backend.inspect_projection(old_locator) is None)
            trace("P3")

            commit("p4-before")
            old = claim()
            expired = old.lease_until
            new = projector.claim_next(backend, worker_id="new", now=expired,
                backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2),
                lease_duration=timedelta(seconds=30))
            check("P4_expiry_boundary_reclaimed", new is not None and new.source_status == "retry")
            locator = projector.call_backend(new, backend, now=expired)
            check("P4_old_lease_fenced", projector.complete(old, backend, locator, now=expired).status == "fenced")
            check("P4_new_worker_confirmed", projector.complete(new, backend, locator, now=expired).status == "confirmed")
            trace("P4_before_call")

            commit("p4-after")
            old = claim()
            locator = projector.call_backend(old, backend, now=now())
            recovered = projector.drain_once(backend, worker_id="recovery", now=old.lease_until,
                backend_timeout=timedelta(seconds=3), local_completion_margin=timedelta(seconds=2),
                lease_duration=timedelta(seconds=30), verification_timeout=timedelta(seconds=3))
            check("P4_expired_after_write_recovers", recovered.status == "confirmed" and recovered.recovered and
                  projector.complete(old, backend, locator, now=old.lease_until).status == "fenced")
            trace("P4_after_call")

            commit("p5")
            transport.drop_write = True
            check("P5_unknown_setup", drain().status == "unknown")
            transport.fail_reads = True
            check("P5_verification_failure_bounded", drain(max_attempts=2).status == "blocked")
            transport.fail_reads = False
            trace("P5")

            receipt = ledger.db.execute("SELECT * FROM projection_receipts WHERE memory_id=?", (first.memory_id,)).fetchone()
            observed = backend.inspect_projection(receipt["backend_locator"])
            writes = sum(e["path"].endswith("/write") for e in report["exchanges"])
            check("duplicate_materialize_same_locator", backend.materialize(observed, receipt["operation_key"]) == receipt["backend_locator"])
            ledger.db.execute("UPDATE outbox SET status='pending',confirmed_receipt_id=NULL WHERE memory_id=?", (first.memory_id,))
            check("P6_receipt_replay", drain().status == "confirmed" and writes == sum(e["path"].endswith("/write") for e in report["exchanges"]))
            check("P6_unique_receipt", ledger.db.execute("SELECT COUNT(*) FROM projection_receipts WHERE memory_id=?", (first.memory_id,)).fetchone()[0] == 1)
            trace("P6")

            started = time.monotonic()
            hits = []
            while time.monotonic() - started < 60:
                hits = backend.search(run_id, "g1", 50)
                if any(hit.memory_id == third.memory_id and hit.revision == 2 for hit in hits):
                    break
                time.sleep(0.5)
            check("search_visibility", any(hit.memory_id == third.memory_id and hit.revision == 2 for hit in hits),
                  {"elapsed_seconds": round(time.monotonic() - started, 3), "bound_seconds": 60, "hit_count": len(hits)})
            check("old_revision_filtered", all(hit.revision == 2 for hit in projector.filter_current_hits(hits) if hit.memory_id == third.memory_id))
            listing = user_client.request("GET", "/api/v1/fs/ls", params={"uri": namespace, "recursive": True, "simple": True})
            locators = [uri for uri in _collect_uris(listing["result"]) if uri.endswith(".json")]
            check("managed_area_enumerable", len(locators) == 6, {"projection_count": len(locators)})
            if args.policy_stages or args.longitudinal:
                from s3_policy_acceptance import verify_policy_stages
                verify_policy_stages(ledger, backend, check, report, directory)
                if args.longitudinal:
                    from s3_longitudinal_acceptance import verify_longitudinal
                    verify_longitudinal(ledger, backend, check, report, directory)
                listing = user_client.request("GET", "/api/v1/fs/ls", params={"uri": namespace, "recursive": True, "simple": True})
                locators = [uri for uri in _collect_uris(listing["result"]) if uri.endswith(".json")]
                trace("S3_final")
                report["maintenance_records"] = {table: [dict(row) for row in ledger.db.execute("SELECT * FROM " + table)]
                    for table in ("events", "candidates", "batches", "proposals", "revision_evidence", "evidence", "memories", "revisions")}
            ledger.close()
            ledger = None
    except Exception as exc:
        report["error_class"] = type(exc).__name__
    finally:
        if ledger:
            ledger.close()
        try:
            if namespace and backend:
                user_client.request("DELETE", "/api/v1/fs", params={"uri": namespace, "recursive": True, "wait": True, "timeout": 60})
                check("cleanup_exact_locators", all(backend.inspect_projection(uri) is None for uri in locators))
                remaining = backend.search(run_id, "g1", 50)
                check("cleanup_search_absent", not remaining)
        except Exception as exc:
            report["cleanup"] = {"status": "failed", "error_class": type(exc).__name__}
        try:
            if user_created:
                admin.request("DELETE", f"/api/v1/admin/accounts/default/users/{user}")
                users = admin.request("GET", "/api/v1/admin/accounts/default/users")["result"]
                check("cleanup_user_revoked", all(item["user_id"] != user for item in users))
            report["cleanup"].setdefault("status", "passed")
        except Exception as exc:
            report["cleanup"] = {"status": "failed", "error_class": type(exc).__name__}
        if backend:
            backend.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["status"] = "passed" if "error_class" not in report and report["cleanup"]["status"] == "passed" and all(a["status"] == "pass" for a in report["assertions"]) else "failed"
        (output / "s3-1.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"run_id": run_id, "status": report["status"], "assertions": len(report["assertions"]), "artifact": str(output)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
