from __future__ import annotations

import importlib.util
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from sagacontext.bench.admission import admission_errors, audit_models, FROZEN_DATASET_DIGEST
from sagacontext.bench.real_judge import (
    _body_matches, load_replay_dataset, markdown_report, run_replay,
)
from sagacontext.llm import OpenAIJudge
from sagacontext.maintenance.judge import OpenAIProposalJudge
from tests.test_real_judge import (
    _AsyncFakeJudge, _ReplayAdapter, _ResponseClient, _delta_for, _response,
)


DATASET_PATH = Path("bench/cases/real_judge/cases-v4.yaml")
HISTORICAL = Path("artifacts/real-judge/20260906T154914Z-deepseek-authorized-shadow/deepseek-v4-pro/replay.jsonl")


def passing_results():
    dataset = load_replay_dataset(DATASET_PATH)
    adapter = _ReplayAdapter({
        case.batch.batch_id: [([] if case.should_ignore else [_delta_for(case)])] * 3
        for case in dataset.cases
    })
    adapter.judge_client.prompt_contract_version = "openai-judge-prompt-v5"
    adapter.judge_client.response_schema_version = "delta-v3"
    adapter.judge_client.timeout = 300.0
    adapter.converter_version = "delta-to-proposal-v2"
    return run_replay(dataset, adapter, max_attempts=3, run_id="test-v4")


class ConditionComparisonTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_replay_dataset(DATASET_PATH)
        self.case = next(case for case in self.dataset.cases if case.category == "refine")

    def matches(self, phrase, **changes):
        fields = {name: value for name, value in self.case.expected_body.items() if name != "key"}
        fields.update(applies_when=phrase, **changes)
        return _body_matches(self.case, [_delta_for(self.case, fields=fields).model_dump()])

    def test_complete_grammar_family_matches_without_mutation(self):
        for when, article, copula in itertools.product(("", "when "), ("", "the "), ("", "is ")):
            phrase = f"{when}{article}async task {copula}already closing"
            with self.subTest(phrase=phrase):
                self.assertTrue(self.matches(phrase))
        self.assertEqual(self.case.expected_body["applies_when"], "async task is already closing")

    def test_semantic_counterexamples_remain_rejected(self):
        for phrase in (
            "async task is not already closing", "async task is already closed",
            "async task is about to close", "async task is closing",
            "async task is never closing", "async task was already closing",
            "async task may be already closing", "async task will be already closing",
            "another async task is already closing", "every async task is already closing",
            "async process is already closing", "sync task is already closing",
            "async task is already closing only after timeout",
            "unless the async task is already closing", "before async task is already closing",
        ):
            with self.subTest(phrase=phrase):
                self.assertFalse(self.matches(phrase))

    def test_anchor_facts_fields_and_exact_strings_remain_required(self):
        phrase = "async task already closing"
        for changes in ({"fix": "do not cancel task"}, {"symptom": "task crashes"}, {"fix": ""}):
            with self.subTest(changes=changes):
                self.assertFalse(self.matches(phrase, **changes))
        partial = _delta_for(self.case, fields={"applies_when": phrase}).model_dump()
        self.assertFalse(_body_matches(self.case, [partial]))
        for case in self.dataset.cases:
            if case.expected_body and "command" in case.expected_body:
                self.assertFalse(_body_matches(case, [_delta_for(case, fields={"command": "when pytest"}).model_dump()]))

    def test_historical_false_negatives_are_semantically_fixed_but_v3_unchanged(self):
        v3 = load_replay_dataset(Path("bench/cases/real_judge/cases-v3.yaml"))
        legacy = next(case for case in v3.cases if case.category == "refine")
        records = [json.loads(line) for line in HISTORICAL.read_text().splitlines()]
        failures = [r for r in records if r["category"] == "refine" and r["memory_body_correct"] is False]
        self.assertEqual(len(failures), 2)
        for record in failures:
            self.assertTrue(_body_matches(self.case, record["actual_deltas"]))
            self.assertFalse(_body_matches(legacy, record["actual_deltas"]))
        self.assertEqual(v3.dataset_digest, "8ebf785c8c579140236521e0b8e93164741d75f9356bd18b26924e7732dc8565")
        self.assertEqual(self.dataset.dataset_digest, FROZEN_DATASET_DIGEST)
        self.assertEqual([c.batch for c in v3.cases], [c.batch for c in self.dataset.cases])

    def test_comparator_requires_allowed_field_and_quoted_reference(self):
        original = yaml.safe_load(DATASET_PATH.read_text())
        for mode in ("wrong_field", "unsupported_reference", "wrong_normalizer"):
            payload = json.loads(json.dumps(original))
            payload.pop("dataset_digest")
            case = next(c for c in payload["cases"] if c["category"] == "refine")
            if mode == "wrong_field":
                case["field_comparators"] = {"fix": "condition-clause-v1"}
            elif mode == "unsupported_reference":
                case["expected_body"]["applies_when"] = "async task is already closed"
            else:
                payload["body_normalizer_version"] = "body-normalizer-v1"
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "dataset.yaml"
                path.write_text(yaml.safe_dump(payload))
                with self.assertRaises(ValueError):
                    load_replay_dataset(path)


class TypeContractTests(unittest.TestCase):
    def test_conflict_type_error_is_not_retried_repaired_or_ignored(self):
        dataset = load_replay_dataset(DATASET_PATH)
        case = next(c for c in dataset.cases if c.category == "conflict")
        wrong = _delta_for(case).model_copy(update={"type": "conflict"})
        client = _AsyncFakeJudge([wrong])
        client.model, client.base_url, client.timeout = "test", "", 300.0
        client.prompt_contract_version, client.response_schema_version = "openai-judge-prompt-v5", "delta-v3"
        adapter = OpenAIProposalJudge(client)
        record, = run_replay(dataset.model_copy(update={"cases": (case,)}), adapter, repeats=1, max_attempts=3)
        self.assertEqual(record.error_class, "judge_conversion_error")
        self.assertEqual(record.attempts_used, 1)
        self.assertEqual(record.actual_deltas[0]["type"], "conflict")
        self.assertEqual(record.actual_proposals, [])
        self.assertEqual(record.status, "error")
        self.assertIsNone(record.memory_body_correct)

    def test_prompt_separates_relation_and_type_without_label_leakage(self):
        case = next(c for c in load_replay_dataset(DATASET_PATH).cases if c.category == "conflict")
        with patch("sagacontext.llm.httpx.AsyncClient", _ResponseClient):
            _ResponseClient.response = _response(200, {"choices": [{"message": {"content": '{"deltas": []}'}}]})
            OpenAIProposalJudge(OpenAIJudge("https://llm.example", "key", "model")).judge(case.batch)
        messages = _ResponseClient.request_kwargs["json"]["messages"]
        self.assertIn("conflict is ONLY a relation, NEVER a memory type", messages[0]["content"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(set(payload), {"anchors", "candidates", "summary"})
        self.assertNotIn("field_comparators", messages[1]["content"])
        self.assertNotIn("expected_relation", messages[1]["content"])


class AdmissionV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = passing_results()

    def test_complete_fixture_passes_shared_report_and_admission_gate(self):
        self.assertEqual(admission_errors(self.records), [])
        self.assertIn("Acceptance: passed", markdown_report(self.records))

    def test_tampered_or_incomplete_artifacts_fail_closed(self):
        mutations = (
            {"memory_body_correct": None}, {"proposal_semantic_correct": None},
            {"case_id": "unknown"}, {"case_digest": "wrong"}, {"run_id": "another-run"},
            {"model": "another-model"}, {"body_normalizer_version": "body-normalizer-v1"},
            {"body_schema_version": "unknown"}, {"sampling": {"temperature": 1.0}},
            {"actual_deltas": []}, {"actual_proposals": []}, {"attempts_used": 2},
            {"expected_relation": "conflict"}, {"conversion_fidelity_correct": False},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                records = [self.records[0].model_copy(update=mutation), *self.records[1:]]
                self.assertTrue(admission_errors(records))
                self.assertIn("Acceptance: blocked", markdown_report(records))
        self.assertTrue(admission_errors(self.records[:-1]))
        self.assertTrue(admission_errors([self.records[0]] * 42))

    def test_wrong_output_cannot_pass_by_claiming_true_scores(self):
        record = self.records[0].model_copy(deep=True)
        record.actual_deltas[0]["fields"]["command"] = "unrelated"
        record.actual_proposals[0]["payload"]["command"] = "unrelated"
        errors = admission_errors([record, *self.records[1:]])
        self.assertIn("memory_body_correct_failure", errors)
        self.assertIn("memory_body_correct_score_mismatch", errors)
        records = [record, *self.records[1:]]
        audit = audit_models(records, records)
        self.assertEqual(audit["aligned_observations"], 42)
        self.assertEqual(audit["semantic_equivalent_observations"], 41)
        self.assertTrue(audit["pro_admission_errors"])

    def test_audit_distinguishes_raw_difference_from_equivalent_condition(self):
        pro = [record.model_copy(deep=True) for record in self.records]
        flash = [record.model_copy(deep=True) for record in self.records]
        for record in pro:
            if record.category == "refine":
                record.actual_deltas[0]["fields"]["applies_when"] = "when async task already closing"
                record.actual_proposals[0]["payload"]["applies_when"] = "when async task already closing"
        audit = audit_models(pro, flash)
        self.assertEqual(audit["aligned_observations"], 39)
        self.assertEqual(audit["semantic_equivalent_observations"], 42)
        self.assertEqual(audit["pro_admission_errors"], [])
        self.assertEqual(pro[2].actual_deltas[0]["fields"]["applies_when"], "when async task already closing")

    def test_shadow_runner_records_blocked_exit_and_cleans_up(self):
        spec = importlib.util.spec_from_file_location("shadow_v4_test", "scripts/run_real_judge_shadow.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = module.run_model(DATASET_PATH, Path(tmp), base_url="", api_key="", model="test", timeout=300, attempts=3)
            self.assertEqual(manifest["temporary_memory_count_before"], 0)
            self.assertEqual(manifest["temporary_memory_count_after"], 0)
            self.assertTrue(all(manifest["cleanup"].values()))
            self.assertIn("unsuccessful_observation", manifest["admission_errors"])
            self.assertIn("Acceptance: blocked", (Path(tmp) / "test/report.md").read_text())
            with patch.object(module, "run_model", return_value=manifest), patch("sys.argv", [
                "shadow", "--output-dir", str(Path(tmp) / "main"), "--models", "test",
            ]):
                self.assertEqual(module.main(), 1)


if __name__ == "__main__":
    unittest.main()
