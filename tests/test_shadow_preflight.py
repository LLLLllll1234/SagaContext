import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "shadow_runner", Path(__file__).parents[1] / "scripts/run_real_judge_shadow.py")
SHADOW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHADOW)


class ShadowPreflightTests(unittest.TestCase):
    def test_missing_configuration_makes_no_semantic_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = SHADOW.Config(state_path=root / "state.db", ledger_path=root / "ledger.db")
            with patch.object(SHADOW.Config, "load", return_value=config), \
                    patch.object(SHADOW, "run_model") as run, \
                    patch("sys.argv", ["shadow", "--output-dir", str(root / "artifacts")]):
                self.assertEqual(SHADOW.main(), 2)
            run.assert_not_called()
            files = list((root / "artifacts").iterdir())
            self.assertEqual([f.name for f in files], ["preflight.json"])
            report = json.loads(files[0].read_text())
            self.assertEqual(report["model_requests_started"], 0)
            self.assertEqual(report["semantic_admission"], "not_evaluated")

    def test_ready_configuration_never_discloses_values(self):
        config = SHADOW.Config(state_path=Path("state"), ledger_path=Path("ledger"),
            llm_base_url="https://synthetic.invalid", llm_api_key="synthetic-secret")
        report = SHADOW.configuration_preflight(config)
        self.assertEqual(report["status"], "ready")
        self.assertNotIn(config.llm_base_url, json.dumps(report))
        self.assertNotIn(config.llm_api_key, json.dumps(report))
