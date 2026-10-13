from sagacontext.console.serialize import safe_text, safe_payload


def test_output_hides_sensitive_fields_and_free_text():
    body = safe_payload({"rule":"使用 pytest", "api_key":"a-test-secret",
        "nested":{"note":"服务 https://private.example.invalid/path token=sample-secret"}})
    assert body["rule"] == "使用 pytest"
    assert body["api_key"] == "[已隐藏]"
    assert "private.example" not in body["nested"]["note"]
    assert "sample-secret" not in body["nested"]["note"]


def test_multiline_credentials_are_hidden():
    assert "private-content" not in safe_text("-----BEGIN PRIVATE KEY-----\nprivate-content\n-----END PRIVATE KEY-----")
    assert "person@example.invalid" not in safe_text("person@example.invalid")
    assert "sk-abcdefghijklm" not in safe_text("key sk-abcdefghijklm")


def test_payload_cannot_export_raw_config_or_transcript():
    cleaned = safe_payload({"transcript_path":"/tmp/private", "raw":{"message":"private"},
                            "Authorization":"Bearer abcdef", "config":{"password":"private"}})
    assert set(cleaned.values()) == {"[已隐藏]"}


def test_payload_summary_prefers_memfile_fields_and_truncates():
    from sagacontext.console.serialize import HIDDEN, SUMMARY_LIMIT, payload_summary
    assert payload_summary({'text': 'b', 'rule': 'a'}) == 'a'
    assert payload_summary({'rule': HIDDEN, 'text': '  多行\n正文 '}) == '多行 正文'
    assert payload_summary({'rule': ''}) is None
    assert payload_summary(['rule']) is None
    long = payload_summary({'rule': 'x' * 500})
    assert len(long) == SUMMARY_LIMIT and long.endswith('…')


def test_payload_summary_reads_judge_structured_payloads():
    from sagacontext.console.serialize import payload_summary
    assert payload_summary({'key': 'workspace-decision', 'command': 'uv run --locked pytest -q'}) \
        == 'workspace-decision：uv run --locked pytest -q'
    assert payload_summary({'key': 'async-cancel', 'symptom': 'task hangs', 'fix': 'cancel task'}) == 'task hangs'
    assert payload_summary({'key': 'output-style'}) == 'output-style'
    assert payload_summary({'weight': 3}) is None


def test_every_judge_dataset_payload_has_a_summary():
    from pathlib import Path
    import yaml
    from sagacontext.console.serialize import payload_summary
    cases = yaml.safe_load(Path('bench/cases/real_judge/cases-v4.yaml').read_text())['cases']
    bodies = [case['expected_body'] for case in cases if case.get('expected_body')]
    assert bodies
    assert [b for b in bodies if payload_summary(b) is None] == []
