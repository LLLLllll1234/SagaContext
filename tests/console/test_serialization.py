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
