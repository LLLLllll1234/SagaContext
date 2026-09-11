import pytest
from sagacontext.console.db import ConsoleReadError


def test_workspace_sessions_do_not_leak(console_case):
    c=console_case
    ids={r['session_id'] for r in c.service.sessions(c.workspace_a)['data']['items']}
    assert ids=={c.session_a}
    with pytest.raises(ConsoleReadError,match='not_found'):
        c.service.tasks(c.project_b,c.workspace_a)
    assert 'foreign-project' not in str(c.service.projects())


def test_cursor_is_bound_to_workspace_and_filter(console_case):
    c=console_case
    first=c.service.activity(c.workspace_a,c.start,c.end,limit=1)['data']
    assert first['next_cursor']
    next_page=c.service.activity(c.workspace_a,c.start,c.end,cursor=first['next_cursor'],limit=1)['data']
    assert next_page['items'][0]['object_id'] != first['items'][0]['object_id']
    with pytest.raises(ConsoleReadError,match='invalid_request'):
        c.service.activity(c.workspace_b,c.start,c.end,cursor=first['next_cursor'])


def test_overview_only_counts_committed_changes(console_case):
    c=console_case
    data=c.service.overview(c.workspace_a,c.start,c.end,{'configured_mode':'guarded','stop_active':False})['data']
    assert data['memory_changes']['value']['operations']=={'new':1}
    assert data['rollout']['value']['batches']=={'awaiting_review':1}
    assert data['rollout']['value']['observations']['wrong_recall']['yes_rate'] is None
    assert data['runtime']['value']['effective_mode']=='guarded'
    other=c.service.overview(c.workspace_b,c.start,c.end,{'configured_mode':'guarded','stop_active':False})['data']
    assert other['runtime']['value']['other_workspace_id']==c.workspace_a
    assert other['rollout']['value'] is None
