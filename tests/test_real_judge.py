from __future__ import annotations

import asyncio
import json
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
    _body_matches,
    load_replay_cases,
    load_replay_dataset,
    markdown_report,
    run_replay,
)
from sagacontext.bench.admission import admission_errors, audit_models, load_results
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

    def test_l0_delta_converts_to_memory_type_layer(self):
        batch = _batch()
        delta = self._delta("new").model_copy(update={"layer": "l0"})
        proposal, = convert_deltas(batch, [delta])
        self.assertEqual(proposal.memory_type, "task_checkpoint")

    def test_duplicate_key_in_fields_equal_to_top_level_is_removed(self):
        batch = _batch()
        delta = self._delta("new").model_copy(update={"fields": {"key": "checkpoint", "next": "new"}})
        proposal, = convert_deltas(batch, [delta])
        self.assertEqual(proposal.payload, {"key": "checkpoint", "next": "new"})

    def test_invalid_delta_is_not_no_change(self):
        with self.assertRaises(JudgeError) as caught:
            convert_deltas(_batch(), [self._delta("refine", anchor="unknown")])
        self.assertEqual((caught.exception.class_name, caught.exception.retryable), ("judge_conversion_error", False))

    def test_duplicate_key_in_fields_is_removed_when_equal(self):
        delta = self._delta("new")
        delta = delta.model_copy(update={"fields": {"key": "checkpoint", "next": "new"}})
        proposal, = convert_deltas(_batch(with_anchor=False), [delta])
        self.assertEqual(proposal.payload, {"key": "checkpoint", "next": "new"})

    def test_conflicting_key_in_fields_is_rejected(self):
        delta = self._delta("new")
        delta = delta.model_copy(update={"fields": {"key": "other", "next": "new"}})
        with self.assertRaises(JudgeError) as caught:
            convert_deltas(_batch(with_anchor=False), [delta])
        self.assertEqual(caught.exception.detail, "delta fields cannot overwrite key")

    def test_sync_facade_rejects_running_event_loop(self):
        adapter = OpenAIProposalJudge(_AsyncFakeJudge([]))

        async def call_inside_loop():
            adapter.judge(_batch())

        with self.assertRaises(JudgeError) as caught:
            asyncio.run(call_inside_loop())
        self.assertEqual(caught.exception.class_name, "judge_event_loop_error")

    def test_refine_patch_is_rejected_and_raw_output_is_retained(self):
        dataset = load_replay_dataset(Path("bench/cases/real_judge/cases-v2.yaml"))
        case = next(case for case in dataset.cases if case.id == "v2-smoke-refine-gotcha")
        partial = _delta_for(case, fields={"applies_when": "async task is already closing"})
        adapter = OpenAIProposalJudge(_AsyncFakeJudge([partial]))
        original_batch = case.batch.model_dump(mode="json")

        with self.assertRaises(JudgeError) as caught:
            adapter.judge(case.batch)

        self.assertEqual(caught.exception.class_name, "judge_conversion_error")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(adapter.last_trace.status, "error")
        self.assertEqual(adapter.last_trace.deltas, (partial.model_dump(mode="json"),))
        self.assertIsNotNone(adapter.last_trace.response_digest)
        self.assertEqual(case.batch.model_dump(mode="json"), original_batch)

    def test_complete_refine_and_reversed_preference_are_transferred_unchanged(self):
        dataset = load_replay_dataset(Path("bench/cases/real_judge/cases-v2.yaml"))
        for case_id in ("v2-smoke-refine-gotcha", "v2-reversal-explicit"):
            with self.subTest(case=case_id):
                case = next(case for case in dataset.cases if case.id == case_id)
                delta = _delta_for(case)
                proposal, = convert_deltas(case.batch, [delta])
                self.assertEqual(proposal.payload, {"key": delta.key, **delta.fields})
                self.assertEqual(proposal.expected_revision, case.expected_context.expected_revision)


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

    def test_v3_helper_fields_are_optional_trace_only_and_defaulted(self):
        content = '{"deltas": [{"layer": "project", "type": "decision", "relation": "new", "key": "k", "fields": {"command": "pytest"}, "evidence_ids": ["e"]}]}'
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            _ResponseClient.response = _response(200, {"choices": [{"message": {"content": content}}]})
            judge = OpenAIJudge("https://llm.example", "key", "model")
            deltas = asyncio.run(judge.judge([], [], "summary"))
        self.assertEqual((deltas[0].strong_signal, deltas[0].confidence_hint), (False, 0.5))
        self.assertEqual(judge.last_auxiliary, ({"strong_signal": None, "confidence_hint": None},))

    def test_v3_helper_fields_reject_wrong_types_and_bounds(self):
        for field, value in (("strong_signal", "false"), ("confidence_hint", 1.1), ("confidence_hint", "0.5")):
            with self.subTest(field=field, value=value), patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
                content = json.dumps({"deltas": [{
                    "layer": "project", "type": "decision", "relation": "new", "key": "k",
                    "fields": {"command": "pytest"}, "evidence_ids": ["e"], field: value,
                }]})
                with self.assertRaises(JudgeError) as caught:
                    self._call(_response(200, {"choices": [{"message": {"content": content}}]}))
                self.assertEqual(caught.exception.class_name, "judge_schema_error")
                self.assertIn("0." + field, caught.exception.detail)

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
        self.assertEqual(judge.last_status_code, 200)
        request = _ResponseClient.request_kwargs["json"]
        user_payload = request["messages"][1]["content"]
        system_prompt = request["messages"][0]["content"]
        self.assertEqual(request["temperature"], 0.0)
        self.assertIn("candidate-1", user_payload)
        self.assertIn("event-1", user_payload)
        self.assertIn("empty deltas array", system_prompt)
        self.assertIn("Never invent a fact", system_prompt)

    def test_request_exposes_full_body_and_enum_contract_without_annotations(self):
        dataset = load_replay_dataset(Path("bench/cases/real_judge/cases-v2.yaml"))
        case = next(case for case in dataset.cases if case.id == "v2-smoke-refine-gotcha")
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            _ResponseClient.response = _response(200, {
                "choices": [{"message": {"content": '{"deltas": []}'}}],
            })
            judge = OpenAIJudge("https://llm.example", "key", "model")
            adapter = OpenAIProposalJudge(judge)
            adapter.judge(case.batch)
        self.assertEqual(adapter.last_trace.status_code, 200)

        request = _ResponseClient.request_kwargs["json"]
        system_prompt = request["messages"][0]["content"]
        user_payload = json.loads(request["messages"][1]["content"])
        self.assertEqual(judge.prompt_contract_version, "openai-judge-prompt-v5")
        self.assertEqual(judge.response_schema_version, "delta-v3")
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertIn("COMPLETE replacement body, never a patch", system_prompt)
        self.assertIn("only the lowercase strings json and prose", system_prompt)
        self.assertEqual(set(user_payload), {"anchors", "candidates", "summary"})
        self.assertEqual(user_payload["anchors"][0]["payload"], case.batch.judge_anchors[0].payload)

    def test_timeout_phase_is_reported_without_retry_or_exception_text(self):
        for error_type, phase in (
            (httpx.ConnectTimeout, "connect"),
            (httpx.ReadTimeout, "read"),
            (httpx.WriteTimeout, "write"),
            (httpx.PoolTimeout, "pool"),
            (httpx.TimeoutException, "unknown"),
        ):
            with self.subTest(phase=phase), patch("sagacontext.llm.httpx.AsyncClient") as client:
                post = client.return_value.__aenter__.return_value.post
                post.side_effect = error_type("sensitive-request-context")
                judge = OpenAIJudge("https://llm.example", "key", "model", timeout=60)
                adapter = OpenAIProposalJudge(judge)
                with self.assertRaises(JudgeError) as caught:
                    adapter.judge(_batch())
                self.assertEqual(caught.exception.class_name, "judge_timeout")
                self.assertTrue(caught.exception.retryable)
                self.assertEqual(caught.exception.attempts, 1)
                self.assertEqual(adapter.last_trace.timeout_phase, phase)
                self.assertIsNone(adapter.last_trace.status_code)
                self.assertNotIn("sensitive-request-context", repr(adapter.last_trace))
                post.assert_awaited_once()

    def test_schema_error_is_not_empty_result(self):
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            with self.assertRaises(JudgeError) as caught:
                self._call(_response(200, {
                    "choices": [{"message": {"content": '{"deltas": [{"relation": "new"}]}'}}]
                }))
            self.assertEqual(caught.exception.class_name, "judge_schema_error")

    def test_schema_and_http_failures_keep_safe_diagnostics(self):
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            with self.assertRaises(JudgeError) as caught:
                self._call(_response(200, {
                    "choices": [{"message": {"content": '{"deltas": [{"relation": "new"}]}'}}]
                }))
        self.assertIn("invalid delta schema at", caught.exception.detail)
        self.assertNotIn("input", caught.exception.detail)
        self.assertEqual(caught.exception.status_code, 200)
        self.assertRegex(caught.exception.response_digest or "", r"^sha256:[0-9a-f]{64}$")

        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            with self.assertRaises(JudgeError) as caught:
                self._call(_response(503, {"error": "provider-private-detail"}))
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (503, "HTTP 503"))
        self.assertNotIn("provider-private-detail", repr(caught.exception))
        self.assertRegex(caught.exception.response_digest or "", r"^sha256:[0-9a-f]{64}$")

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
        base_url="https://judge.example/v1/",
        timeout=12.5,
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
    v3_path = Path("bench/cases/real_judge/cases-v3.yaml")

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

    def test_v3_has_new_digest_and_audited_refine_variants(self):
        v2 = load_replay_dataset(self.v2_path)
        v3 = load_replay_dataset(self.v3_path)
        self.assertEqual((v2.dataset_id, v2.dataset_digest), (
            "real-judge-v2",
            "254c47f4554c3d506e419200203e96f1f6c7e05386c370d7cab7e614e9b6cfce",
        ))
        self.assertEqual((v3.dataset_id, v3.schema_version, len(v3.cases)), (
            "real-judge-v3", "real-judge-dataset-v3", 14,
        ))
        self.assertNotEqual(v2.dataset_digest, v3.dataset_digest)
        refine = next(case for case in v3.cases if case.category == "refine")
        self.assertEqual(
            {body["applies_when"] for body in refine.acceptable_bodies},
            {"async task is already closing", "the async task is already closing", "when the async task is already closing"},
        )
        self.assertTrue(refine.field_evidence["applies_when"][0].equivalence)

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

    def test_enum_expansions_and_refine_patches_still_fail_frozen_body_scoring(self):
        dataset = load_replay_dataset(self.v2_path)
        for case_id, fields in (
            ("v2-smoke-refine-gotcha", {"applies_when": "async task is already closing"}),
            ("v2-smoke-supersede-taste", {"format": "JSON"}),
            ("v2-smoke-supersede-taste", {"format": "concise JSON results"}),
            ("v2-reversal-explicit", {"format": "prose summaries"}),
        ):
            with self.subTest(case=case_id, fields=fields):
                case = next(case for case in dataset.cases if case.id == case_id)
                # Replay the recorded Delta to score it independently of conversion guards.
                self.assertFalse(_body_matches(case, [_delta_for(case, fields=fields).model_dump()]))

    def test_timeout_metadata_reaches_artifact_and_report(self):
        dataset = load_replay_dataset(self.v2_path)
        case = dataset.cases[0]
        judge = OpenAIJudge("https://llm.example", "key", "model", timeout=60)
        adapter = OpenAIProposalJudge(judge)
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            _ResponseClient.response = httpx.ReadTimeout("sensitive-request-context")
            result, = run_replay(_subset(dataset, case.id), adapter, repeats=1, run_id="timeout")
        self.assertEqual(result.timeout_phase, "read")
        self.assertIsNone(result.memory_body_correct)
        report = markdown_report([result])
        self.assertIn("Acceptance: blocked", report)
        self.assertIn("Prompt contract: openai-judge-prompt-v5", report)
        self.assertIn("Response schema: delta-v3", report)
        self.assertIn("Converter: delta-to-proposal-v2", report)
        self.assertIn("| judge_timeout | - | ReadTimeout | read |", report)
        self.assertNotIn("sensitive-request-context", report + result.model_dump_json())

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

    def test_report_records_non_secret_request_configuration(self):
        dataset = load_replay_dataset(self.v2_path)
        case = dataset.cases[0]
        result = run_replay(
            _subset(dataset, case.id),
            _ReplayAdapter({case.batch.batch_id: [[_delta_for(case)]]}),
            repeats=1,
            run_id="config",
        )[0]
        serialized = result.model_dump_json()
        report = markdown_report([result])
        self.assertRegex(result.endpoint_fingerprint, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual((result.request_timeout_seconds, result.max_attempts), (12.5, 1))
        self.assertNotIn("judge.example", serialized)
        self.assertIn(f"Endpoint fingerprint: {result.endpoint_fingerprint}", report)
        self.assertIn("Request timeout: 12.5s", report)
        self.assertIn("Token usage: unavailable", report)
        self.assertIn("Cost: unavailable", report)

    def test_admission_requires_independent_frozen_42_observations(self):
        dataset = load_replay_dataset(self.v3_path)
        case = dataset.cases[0]
        results = run_replay(
            _subset(dataset, case.id),
            _ReplayAdapter({case.batch.batch_id: [[_delta_for(case)]]}),
            repeats=1,
            max_attempts=3,
            run_id="admission",
        )
        errors = admission_errors(results, model="test")
        self.assertIn("observation_count=1 expected=42", errors)
        self.assertIn("repeat_set_mismatch", errors)
        self.assertIn("case_count_per_repeat_mismatch", errors)
        self.assertIn("timeout_mismatch", errors)

    def test_frozen_pro_and_flash_artifacts_pass_independent_admission(self):
        for model, artifact in (
            ("deepseek-v4-pro", Path("artifacts/real-judge/20260906T-pro-v3-retry-final/replay.jsonl")),
            ("deepseek-v4-flash", Path("artifacts/real-judge/20260906T-flash-v3-key-fixed-final/replay.jsonl")),
        ):
            with self.subTest(model=model):
                self.assertEqual(admission_errors(load_results(artifact), model=model, contract="v3"), [])
                self.assertIn("dataset_mismatch", admission_errors(load_results(artifact), model=model))

    def test_cross_model_audit_marks_semantic_difference_without_merging(self):
        dataset = load_replay_dataset(self.v3_path)
        case = dataset.cases[0]
        pro = run_replay(_subset(dataset, case.id), _ReplayAdapter({case.batch.batch_id: [[_delta_for(case)]]}), repeats=1, run_id="pro")
        altered = _delta_for(case).model_copy(update={"fields": {"command": "nox"}})
        flash = run_replay(_subset(dataset, case.id), _ReplayAdapter({case.batch.batch_id: [[altered]]}), repeats=1, run_id="flash")
        audit = audit_models(pro, flash)
        self.assertEqual(audit["pro_observations"], audit["flash_observations"])
        self.assertEqual(audit["different_semantics"], 1)
        self.assertIn("body", audit["difference_dimensions"])

    def test_missing_configuration_is_reported_per_observation(self):
        class BlockedAdapter:
            version = "openai-proposal-v1"
            converter_version = "delta-to-proposal-v1"
            judge_client = SimpleNamespace(
                prompt_contract_version="openai-judge-prompt-v2",
                response_schema_version="delta-v2",
                model="",
                base_url="",
                timeout=5.0,
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
