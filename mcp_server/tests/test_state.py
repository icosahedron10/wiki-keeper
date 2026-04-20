from __future__ import annotations

import json

import pytest

from mcp_server import state
from mcp_server.paths import state_path
from mcp_server.storage import atomic_write


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
