from pathlib import Path
from sagacontext.config import Config
from sagacontext.hook import prepare
from sagacontext.event_content import event_payload


def test_same_prompt_occurrences_are_distinct_and_retries_can_keep_identity(tmp_path):
    config = Config(state_path=tmp_path/'state',ledger_path=tmp_path/'ledger',rollout_workspaces=(str(tmp_path),))
    payload={'session_id':'one','cwd':str(tmp_path),'prompt':'Remember uv run pytest.'}
    first=prepare(payload,config,'codex','UserPromptSubmit')
    second=prepare(payload,config,'codex','UserPromptSubmit')
    assert first['source_event_ref'] != second['source_event_ref']
    assert prepare({**payload,'event_id':'native-1'},config,'codex','UserPromptSubmit')['source_event_ref']=='native-1'
    assert prepare({**payload,'cwd':str(tmp_path/'other')},config,'codex','UserPromptSubmit') is None
    assert prepare(payload,config,'codex','PreToolUse') is None
    assert 'transcript_path' not in prepare({**payload,'transcript_path':'private'},config,'codex','UserPromptSubmit')


def test_content_admission_drops_sensitive_and_nondurable_text():
    for text in ['Remember sk-synthetic-keyvalue','Remember api_key=private','Remember https://private.invalid','Remember me@example.invalid','hello']:
        assert 'text' not in event_payload({'prompt':text},'UserPromptSubmit')
    assert 'text' in event_payload({'prompt':'For this project, use pytest.'},'UserPromptSubmit')
