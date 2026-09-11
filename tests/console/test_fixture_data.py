import sqlite3
import socket

import pytest

from scripts.console_fixture import create_fixture
from sagacontext.config import Config


def test_fixture_has_no_external_dependencies(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("fixture attempted external access")
    monkeypatch.setattr(Config, "load", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    case = create_fixture(tmp_path)
    with sqlite3.connect(case.path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT COUNT(*) FROM rollout_commits").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM proposals WHERE status='awaiting_review'").fetchone()[0] == 1
    with pytest.raises(ValueError, match="fixture_database_exists"):
        create_fixture(tmp_path)


def test_empty_fixture_has_workspaces_without_activity(tmp_path):
    case = create_fixture(tmp_path, populated=False)
    with sqlite3.connect(case.path) as db:
        assert db.execute("SELECT COUNT(*) FROM project_locations").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
