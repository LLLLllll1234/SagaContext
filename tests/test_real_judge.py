from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import yaml

from sagacontext.bench.real_judge import (
    ReplayDataset,
    load_replay_cases,
    load_replay_dataset,
    markdown_report,
    run_replay,
)
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

    def test_request_preserves_context_and_uses_frozen_sampling(self):
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
        system_prompt = request["messages"][0]["content"]
        self.assertEqual(request["temperature"], 0.0)
        self.assertIn("candidate-1", user_payload)
        self.assertIn("event-1", user_payload)
        self.assertIn("empty deltas array", system_prompt)
        self.assertIn("Never invent a fact", system_prompt)

    def test_schema_error_is_not_empty_result(self):
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            with self.assertRaises(JudgeError) as caught:
                self._call(_response(200, {
                    "choices": [{"message": {"content": '{"deltas": [{"relation": "new"}]}'}}]
                }))
            self.assertEqual(caught.exception.class_name, "judge_schema_error")

    def test_missing_configuration_is_blocking(self):
        judge = OpenAIJudge("", "", "")
        with self.assertRaises(JudgeError) as caught:
            asyncio.run(judge.judge([], [], "summary"))
        self.assertEqual(
            (caught.exception.class_name, caught.exception.retryable),
            ("judge_configuration_error", False),
        )


_LAYER = {
    "decision": "project",
    "convention": "preference",
    "gotcha": "project",
    "taste": "preference",
    "project_map": "project",
}


def _delta_for(case, relation: str | None = None, fields: dict | None = None) -> Delta:
    body = dict(case.expected_body or {})
    key = body.pop("key", case.batch.judge_candidates[0].topic_key)
    relation = relation or case.expected_relation
    target = case.expected_context.target_id if relation != "new" else None
    return Delta(
        candidate_id=case.batch.judge_candidates[0].candidate_id,
        layer=_LAYER[case.expected_context.memory_type],
        type=case.expected_context.memory_type,
        relation=relation,
        anchor_uri=target,
        key=key,
        fields=body if fields is None else fields,
        evidence_ids=list(case.expected_evidence_ids),
        strong_signal=relation == "supersede",
        confidence_hint=0.9,
        rationale="test",
    )


class _ReplayAdapter:
    version = "openai-proposal-v1"
    converter_version = "delta-to-proposal-v1"
    judge_client = SimpleNamespace(
        prompt_contract_version="openai-judge-prompt-v2",
        response_schema_version="delta-v2",
        model="test-model",
        temperature=0.0,
    )

    def __init__(self, outputs: dict[str, list[list[Delta]]]):
        self.outputs = outputs
        self.calls = defaultdict(int)
        self.last_trace = JudgeTrace(status="not_run", latency_ms=0)

    def judge(self, batch):
        index = self.calls[batch.batch_id]
        self.calls[batch.batch_id] += 1
        deltas = self.outputs[batch.batch_id][index]
        proposals = convert_deltas(batch, deltas)
        self.last_trace = JudgeTrace(
            status="ok",
            latency_ms=0,
            response_digest="test",
            deltas=tuple(delta.model_dump(mode="json") for delta in deltas),
        )
        return proposals


def _subset(dataset: ReplayDataset, *case_ids: str) -> ReplayDataset:
    selected = tuple(case for case in dataset.cases if case.id in case_ids)
    return dataset.model_copy(update={"cases": selected})


class ReplayRunnerTests(unittest.TestCase):
    v1_path = Path("bench/cases/real_judge/cases.yaml")
    v2_path = Path("bench/cases/real_judge/cases-v2.yaml")

    def test_v1_is_preserved_and_v2_has_audited_matrix(self):
        legacy = load_replay_dataset(self.v1_path)
        dataset = load_replay_dataset(self.v2_path)
        self.assertEqual((legacy.dataset_id, len(legacy.cases)), ("real-judge-v1", 6))
        self.assertEqual((dataset.dataset_id, len(dataset.cases)), ("real-judge-v2", 14))
        self.assertEqual(
            {case.group for case in dataset.cases},
            {"smoke", "duplicate_expression", "preference_reversal", "insufficient_information", "irrelevant_content"},
        )
        self.assertEqual(len(load_replay_cases(self.v1_path)), 6)

    def test_v2_rejects_missing_field_evidence_pointer(self):
        payload = yaml.safe_load(self.v2_path.read_text())
        payload["cases"][0]["field_evidence"]["command"][0]["source"] = "/batch/missing"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(yaml.safe_dump(payload, sort_keys=False))
            with self.assertRaisesRegex(ValueError, "pointer does not exist"):
                load_replay_dataset(path)

    def test_relation_and_conversion_fidelity_are_independent(self):
        dataset = load_replay_dataset(self.v2_path)
        case = next(case for case in dataset.cases if case.id == "v2-smoke-confirm-convention")
        subset = _subset(dataset, case.id)
        adapter = _ReplayAdapter({case.batch.batch_id: [[_delta_for(case, relation="refine")]]})
        result = run_replay(subset, adapter, repeats=1, run_id="test")[0]
        self.assertFalse(result.relation_correct)
        self.assertTrue(result.conversion_fidelity_correct)
        self.assertFalse(result.proposal_semantic_correct)

    def test_body_normalizer_is_narrow_and_aliases_fail(self):
        dataset = load_replay_dataset(self.v2_path)
        case = next(case for case in dataset.cases if case.id == "v2-smoke-new-decision")
        subset = _subset(dataset, case.id)
        normalized = _ReplayAdapter({
            case.batch.batch_id: [[_delta_for(case, fields={"command": "  ｐｙｔｅｓｔ  "})]]
        })
        result = run_replay(subset, normalized, repeats=1, run_id="normalized")[0]
        self.assertTrue(result.memory_body_correct)
        invalid_fields = (
            {"test_runner": "pytest"},
            {"command": "PyTest"},
            {"command": 123},
        )
        for index, fields in enumerate(invalid_fields):
            adapter = _ReplayAdapter({
                case.batch.batch_id: [[_delta_for(case, fields=fields)]]
            })
            result = run_replay(subset, adapter, repeats=1, run_id=f"invalid-{index}")[0]
            self.assertFalse(result.memory_body_correct)

    def test_empty_delta_has_na_model_evidence_and_proposal_provenance(self):
        dataset = load_replay_dataset(self.v2_path)
        case = next(case for case in dataset.cases if case.id == "v2-smoke-no-change")
        subset = _subset(dataset, case.id)
        result = run_replay(
            subset,
            _ReplayAdapter({case.batch.batch_id: [[]]}),
            repeats=1,
            run_id="empty",
        )[0]
        self.assertIsNone(result.memory_body_correct)
        self.assertIsNone(result.evidence_correct)
        self.assertTrue(result.ignore_correct)
        self.assertEqual(result.actual_proposals[0]["evidence_ids"], ["event-no-change"])

    def test_case_first_aggregation_rejects_pooled_four_of_six(self):
        dataset = load_replay_dataset(self.v2_path)
        selected = _subset(
            dataset,
            "v2-duplicate-exact-convention",
            "v2-duplicate-paraphrase-taste",
        )
        first, second = selected.cases
        correct_first = _delta_for(first)
        correct_second = _delta_for(second)
        wrong_second = _delta_for(second, relation="refine")
        adapter = _ReplayAdapter({
            first.batch.batch_id: [[correct_first], [correct_first], [correct_first]],
            second.batch.batch_id: [[wrong_second], [wrong_second], [correct_second]],
        })
        report = markdown_report(run_replay(selected, adapter, repeats=3, run_id="aggregate"))
        self.assertIn("v2-duplicate-paraphrase-taste", report)
        self.assertIn("Acceptance: failed", report)
        self.assertIn("1/3", report)

    def test_all_contract_scores_pass_the_three_repeat_gate(self):
        dataset = load_replay_dataset(self.v2_path)
        outputs = {}
        for case in dataset.cases:
            observation = [] if case.expected_relation == "no_change" else [_delta_for(case)]
            outputs[case.batch.batch_id] = [observation, observation, observation]
        report = markdown_report(
            run_replay(dataset, _ReplayAdapter(outputs), repeats=3, run_id="passing")
        )
        self.assertIn("Acceptance: passed", report)
        self.assertIn("| 1 | 14/14 | 8/8 | 8/8 | 14/14 | 6/6 |", report)

    def test_missing_configuration_is_reported_per_observation(self):
        class BlockedAdapter:
            version = "openai-proposal-v1"
            converter_version = "delta-to-proposal-v1"
            judge_client = SimpleNamespace(
                prompt_contract_version="openai-judge-prompt-v2",
                response_schema_version="delta-v2",
                model="",
                temperature=0.0,
            )
            last_trace = JudgeTrace(status="not_run", latency_ms=0)

            def judge(self, batch):
                self.last_trace = JudgeTrace(status="error", latency_ms=0, error_class="judge_configuration_error")
                raise JudgeError("judge_configuration_error", False)

        dataset = load_replay_dataset(self.v2_path)
        results = run_replay(dataset, BlockedAdapter(), repeats=1, run_id="blocked")
        self.assertEqual({result.status for result in results}, {"blocked_configuration"})
        self.assertTrue(all(result.relation_correct is None for result in results))
        report = markdown_report(results)
        self.assertIn("judge_configuration_error", report)
        self.assertIn("Acceptance: blocked", report)


if __name__ == "__main__":
    unittest.main()
