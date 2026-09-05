from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import httpx

from sagacontext.bench.real_judge import load_replay_cases, markdown_report, run_replay
from sagacontext.ledger import Scope
from sagacontext.llm import JudgeError, OpenAIJudge
from sagacontext.maintenance import (
    BatchInput,
    DeltaProposal,
    JudgeAnchor,
    JudgeCandidate,
    OpenAIProposalJudge,
    convert_deltas,
)
from sagacontext.maintenance.judge import JudgeTrace
from sagacontext.models import Candidate, Delta


def _batch(*, with_anchor: bool = True) -> BatchInput:
    candidate = JudgeCandidate(
        candidate_id="candidate-1",
        kind="checkpoint",
        memory_type_hint="task_checkpoint",
        scope_hint=Scope(kind="global"),
        topic_key="checkpoint",
        event_ids=("event-1",),
        text="continue the projector task",
    )
    anchors = (
        JudgeAnchor(
            memory_id="memory-1",
            revision=3,
            memory_type="task_checkpoint",
            scope=Scope(kind="global"),
            payload={"key": "checkpoint", "next": "old"},
        ),
    ) if with_anchor else ()
    return BatchInput(
        batch_id="batch-1",
        input_digest="digest",
        policy_version="policy-v1",
        maintenance_schema_version=2,
        judge_version="openai-proposal-v1",
        event_ids=("event-1",),
        candidate_ids=("candidate-1",),
        anchor_revisions=tuple((anchor.memory_id, anchor.revision) for anchor in anchors),
        judge_candidates=(candidate,),
        judge_anchors=anchors,
        summary="continue the projector task",
    )


class _AsyncFakeJudge:
    def __init__(self, deltas: list[Delta]):
        self.deltas = deltas

    async def judge(self, anchors, candidates, summary):
        return self.deltas


class ProposalConversionTests(unittest.TestCase):
    def _delta(self, relation: str, *, anchor: str | None = None) -> Delta:
        return Delta(
            candidate_id="candidate-1",
            layer="task",
            type="task_checkpoint",
            relation=relation,
            anchor_uri=anchor,
            key="checkpoint",
            fields={"next": "new"},
            evidence_ids=["event-1"],
            rationale="fixture",
        )

    def test_each_relation_converts_with_frozen_target_and_scope(self):
        for relation in ("new", "confirm", "refine", "supersede", "conflict"):
            with self.subTest(relation=relation):
                batch = _batch(with_anchor=relation != "new")
                proposals = convert_deltas(
                    batch,
                    [self._delta(relation, anchor=None if relation == "new" else "memory-1")],
                )
                self.assertEqual(len(proposals), 1)
                proposal = proposals[0]
                self.assertEqual(proposal.operation, relation)
                self.assertEqual(proposal.scope, batch.judge_candidates[0].scope_hint)
                if relation == "new":
                    self.assertIsNone(proposal.target_id)
                    self.assertIsNone(proposal.expected_revision)
                else:
                    self.assertEqual((proposal.target_id, proposal.expected_revision), ("memory-1", 3))

    def test_empty_success_is_no_change(self):
        proposal = convert_deltas(_batch(), [])
        self.assertEqual(proposal, (DeltaProposal(
            candidate_id="candidate-1",
            operation="no_change",
            memory_type="task_checkpoint",
            scope=Scope(kind="global"),
            evidence_ids=("event-1",),
            rationale="validated_empty_delta",
        ),))

    def test_invalid_delta_is_not_no_change(self):
        with self.assertRaises(JudgeError) as caught:
            convert_deltas(_batch(), [self._delta("refine", anchor="unknown")])
        self.assertEqual((caught.exception.class_name, caught.exception.retryable), ("judge_conversion_error", False))

    def test_sync_facade_rejects_running_event_loop(self):
        adapter = OpenAIProposalJudge(_AsyncFakeJudge([]))

        async def call_inside_loop():
            adapter.judge(_batch())

        with self.assertRaises(JudgeError) as caught:
            asyncio.run(call_inside_loop())
        self.assertEqual(caught.exception.class_name, "judge_event_loop_error")


class _ResponseClient:
    response = None
    request_kwargs = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        type(self).request_kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("POST", "https://llm.example/chat/completions"),
    )


class OpenAIJudgeContractTests(unittest.TestCase):
    def _call(self, response):
        _ResponseClient.response = response
        judge = OpenAIJudge("https://llm.example", "key", "model")
        return asyncio.run(judge.judge([], [], "summary"))

    def test_http_failures_are_classified(self):
        for status, expected, retryable in (
            (401, "judge_authentication_error", False),
            (429, "judge_rate_limited", True),
            (408, "judge_service_unavailable", True),
            (500, "judge_service_unavailable", True),
        ):
            with self.subTest(status=status), patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
                with self.assertRaises(JudgeError) as caught:
                    self._call(_response(status, {"error": "x"}))
                self.assertEqual((caught.exception.class_name, caught.exception.retryable), (expected, retryable))

    def test_valid_empty_delta_is_distinct_from_bad_response(self):
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            self.assertEqual(self._call(_response(200, {"choices": [{"message": {"content": '{"deltas": []}'}}]})), [])
            with self.assertRaises(JudgeError) as caught:
                self._call(_response(200, {"choices": [{"message": {"content": ""}}]}))
            self.assertEqual(caught.exception.class_name, "judge_response_error")

    def test_request_preserves_candidate_identity_and_evidence_context(self):
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            _ResponseClient.response = _response(200, {"choices": [{"message": {"content": '{"deltas": []}'}}]})
            judge = OpenAIJudge("https://llm.example", "key", "model")
            asyncio.run(judge.judge([], [Candidate(
                level="L0",
                layer_guess="task",
                kind="checkpoint",
                candidate_id="candidate-1",
                event_ids=["event-1"],
                text="checkpoint",
            )], "summary"))
        request = _ResponseClient.request_kwargs["json"]
        user_payload = request["messages"][1]["content"]
        self.assertIn("candidate-1", user_payload)
        self.assertIn("event-1", user_payload)

    def test_schema_error_is_not_empty_result(self):
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            with self.assertRaises(JudgeError) as caught:
                self._call(_response(200, {"choices": [{"message": {"content": '{"deltas": [{"relation": "new"}]}'}}]}))
            self.assertEqual(caught.exception.class_name, "judge_schema_error")

    def test_missing_configuration_is_blocking(self):
        judge = OpenAIJudge("", "", "")
        with self.assertRaises(JudgeError) as caught:
            asyncio.run(judge.judge([], [], "summary"))
        self.assertEqual((caught.exception.class_name, caught.exception.retryable), ("judge_configuration_error", False))


class ReplayRunnerTests(unittest.TestCase):
    def test_frozen_dataset_has_six_relation_cases(self):
        cases = load_replay_cases(Path("bench/cases/real_judge/cases.yaml"))
        self.assertEqual(len(cases), 6)
        self.assertEqual(
            {case.category for case in cases},
            {"new", "confirm", "refine", "supersede", "conflict", "no_change"},
        )

    def test_missing_configuration_is_reported_per_case(self):
        class BlockedAdapter:
            version = "openai-proposal-v1"
            converter_version = "delta-to-proposal-v1"
            judge_client = SimpleNamespace(
                prompt_contract_version="openai-judge-prompt-v1",
                response_schema_version="delta-v2",
                model="",
            )
            last_trace = JudgeTrace(status="not_run", latency_ms=0)

            def judge(self, batch):
                self.last_trace = JudgeTrace(status="error", latency_ms=0, error_class="judge_configuration_error")
                raise JudgeError("judge_configuration_error", False)

        results = run_replay(load_replay_cases(Path("bench/cases/real_judge/cases.yaml")), BlockedAdapter())
        self.assertEqual({result.status for result in results}, {"blocked_configuration"})
        report = markdown_report(results)
        self.assertIn("blocked_configuration", report)
        self.assertIn("Relation accuracy (successful calls only): n/a", report)


if __name__ == "__main__":
    unittest.main()
