from __future__ import annotations

import json

import pytest

from mcp_server.core.paths import state_path
from mcp_server.core.storage import atomic_write
from mcp_server.wiki import state


def test_state_round_trip(wiki_root):
    current = state.load()
    updated = state.record_run(
        current,
        article_id="modules/Auth Service",
        index=0,
        outcome="patched",
        audit_path=".wiki-keeper/audits/2026-04-19/auth-service.md",
        date="2026-04-19",
    )
    state.save(updated)
    reloaded = state.load()
    assert reloaded["cursor"]["index"] == 0
    assert reloaded["last_run"]["outcome"] == "patched"


def test_state_missing_file_errors(wiki_root):
    state_path().unlink()
    with pytest.raises(FileNotFoundError):
        state.load()


def test_state_corrupt_json_errors(wiki_root):
    atomic_write(state_path(), "{broken json")
    with pytest.raises(ValueError):
        state.load()


def test_state_normalizes_shape(wiki_root):
    atomic_write(state_path(), json.dumps({"cursor": {"index": "1"}}))
    loaded = state.load()
    assert loaded["cursor"]["index"] == 1
    assert "history" in loaded
    assert loaded["git"]["last_processed_commit"] is None


def test_state_records_git_run(wiki_root):
    current = state.load()
    updated = state.record_git_run(
        current,
        since="abc",
        until="def",
        default_branch="main",
        changed_paths=["services/auth/app.py"],
        outcome="audit_only",
        audit_paths=[".wiki-keeper/audits/2026-04-23/git-delta.md"],
        patch_status="audit_only",
        date="2026-04-23",
    )
    assert updated["git"]["last_processed_commit"] == "def"
    assert updated["git"]["last_seen_commit"] == "def"
    assert updated["git"]["default_branch"] == "main"
    assert updated["git"]["runs"][0]["changed_paths"] == ["services/auth/app.py"]
