from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server import git_delta, nightly, state, tools
from mcp_server.init_corpus import initialize_wiki
from mcp_server.paths import state_path
from mcp_server.storage import read_text


ARTICLE = """---
id: auth-overview
title: Auth Overview
sources:
  - services/auth/**
---
# Auth Service

## Summary
Old summary.

## Key Facts
- Old fact.

## Details
Old details.

## Relationships
- Related to [[Auth Service]]

## Sources
- `repo:services/auth/handler.py`

## Open Questions
- None.
"""

BILLING_ARTICLE = """---
id: billing-overview
title: Billing Overview
sources:
  - services/billing/**
---
# Billing Service

## Summary
Old summary.

## Key Facts
- Old fact.

## Details
Old details.

## Relationships
- Related to [[Auth Service]]

## Sources
- `repo:services/billing/handler.py`

## Open Questions
- None.
"""


class FakeLLM:
    def __init__(self, *, confidence: str = "low", decision: str = "audit_only"):
        self.config = SimpleNamespace(
            reader_model="gpt-5-nano",
            reader_reasoning="low",
            orchestrator_model="gpt-5-nano",
            orchestrator_reasoning="medium",
        )
        self.decision = {
            "confidence": confidence,
            "decision": decision,
            "patch_content": "",
            "rationale": "test decision",
        }

    def complete_text(self, **kwargs):  # noqa: ARG002
        return "reader findings"

    def complete_json_schema(self, **kwargs):  # noqa: ARG002
        return dict(self.decision)


def _require_git() -> None:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git executable not available: {exc}")


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init_git(root: Path) -> None:
    _require_git()
    _git(root, "init")
    _git(root, "config", "user.email", "wiki-keeper@example.invalid")
    _git(root, "config", "user.name", "Wiki Keeper Tests")


def _commit_all(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _seed_git_review_repo(wiki_root: Path) -> str:
    (wiki_root / "services" / "auth").mkdir(parents=True, exist_ok=True)
    (wiki_root / "services" / "auth" / "handler.py").write_text(
        "def login():\n    return 'old'\n",
        encoding="utf-8",
    )
    tools.update_knowledge("modules/Auth Service", ARTICLE, mode="replace")
    _init_git(wiki_root)
    baseline = _commit_all(wiki_root, "baseline")
    current = state.load()
    state.save(state.set_git_baseline(current, commit=baseline, default_branch="master"))
    return baseline


def test_initialize_records_current_head_as_git_baseline(tmp_path):
    _init_git(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    head = _commit_all(tmp_path, "baseline")

    out = initialize_wiki(repo_root=tmp_path, offline=True)

    assert out["git"]["last_processed_commit"] == head
    raw = json.loads(read_text(tmp_path / ".wiki-keeper" / "state.json"))
    assert raw["git"]["last_seen_commit"] == head


def test_git_range_selection_and_empty_range(wiki_root):
    baseline = _seed_git_review_repo(wiki_root)

    commit_range, reason = git_delta.build_range(
        repo_root=wiki_root,
        since=None,
        until=None,
        state_git=state.load()["git"],
    )

    assert reason is None
    assert commit_range is not None
    assert commit_range.since == baseline
    assert commit_range.until == baseline
    assert commit_range.changed_paths == []


def test_changed_paths_map_to_frontmatter_sources(wiki_root):
    _seed_git_review_repo(wiki_root)
    (wiki_root / "services" / "auth" / "handler.py").write_text(
        "def login():\n    return 'new'\n",
        encoding="utf-8",
    )
    _commit_all(wiki_root, "change auth")
    commit_range, _ = git_delta.build_range(
        repo_root=wiki_root,
        since=None,
        until=None,
        state_git=state.load()["git"],
    )

    assert commit_range is not None
    matches = git_delta.map_changed_paths_to_articles(commit_range.changed_paths)
    assert [match.page_name for match in matches] == ["modules/Auth Service"]
    assert matches[0].changed_paths == ["services/auth/handler.py"]


def test_deleted_paths_map_to_frontmatter_sources(wiki_root):
    _seed_git_review_repo(wiki_root)
    (wiki_root / "services" / "auth" / "handler.py").unlink()
    _commit_all(wiki_root, "delete auth handler")
    commit_range, _ = git_delta.build_range(
        repo_root=wiki_root,
        since=None,
        until=None,
        state_git=state.load()["git"],
    )

    assert commit_range is not None
    assert "services/auth/handler.py" in commit_range.changed_paths
    matches = git_delta.map_changed_paths_to_articles(commit_range.changed_paths)
    assert [match.page_name for match in matches] == ["modules/Auth Service"]


def test_run_nightly_reviews_mapped_git_delta_and_updates_state(wiki_root, monkeypatch):
    baseline = _seed_git_review_repo(wiki_root)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    (wiki_root / "services" / "auth" / "handler.py").write_text(
        "def login():\n    return 'new'\n",
        encoding="utf-8",
    )
    head = _commit_all(wiki_root, "change auth")

    out = nightly.run_nightly(
        budget=1,
        llm_client=FakeLLM(),
        update_knowledge_fn=tools.update_knowledge,
    )

    assert out["outcome"] == "audit_only"
    assert out["commit_range"]["since"] == baseline
    assert out["commit_range"]["until"] == head
    assert out["results"][0]["changed_paths"] == ["services/auth/handler.py"]
    assert state.load()["git"]["last_processed_commit"] == head

    second = nightly.run_nightly(
        budget=1,
        llm_client=FakeLLM(),
        update_knowledge_fn=tools.update_knowledge,
    )
    assert second["outcome"] == "no_changes"


def test_run_nightly_partial_budget_does_not_advance_processed_commit(wiki_root, monkeypatch):
    _seed_git_review_repo(wiki_root)
    (wiki_root / "services" / "billing").mkdir(parents=True, exist_ok=True)
    (wiki_root / "services" / "billing" / "handler.py").write_text(
        "def charge():\n    return 'old'\n",
        encoding="utf-8",
    )
    tools.update_knowledge("modules/Billing Service", BILLING_ARTICLE, mode="replace")
    baseline = _commit_all(wiki_root, "add billing baseline")
    current = state.load()
    state.save(state.set_git_baseline(current, commit=baseline, default_branch="master"))

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    (wiki_root / "services" / "auth" / "handler.py").write_text(
        "def login():\n    return 'new'\n",
        encoding="utf-8",
    )
    (wiki_root / "services" / "billing" / "handler.py").write_text(
        "def charge():\n    return 'new'\n",
        encoding="utf-8",
    )
    head = _commit_all(wiki_root, "change auth and billing")

    out = nightly.run_nightly(
        budget=1,
        llm_client=FakeLLM(),
        update_knowledge_fn=tools.update_knowledge,
    )

    git_state = state.load()["git"]
    assert out["outcome"] == "partial"
    assert out["skipped_matches"]
    assert git_state["last_seen_commit"] == head
    assert git_state["last_processed_commit"] == baseline


def test_run_nightly_unmapped_delta_writes_audit_without_api_key(wiki_root, monkeypatch):
    _seed_git_review_repo(wiki_root)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (wiki_root / "docs").mkdir(exist_ok=True)
    (wiki_root / "docs" / "readme.md").write_text("# docs\n", encoding="utf-8")
    _commit_all(wiki_root, "change docs")

    out = nightly.run_nightly(budget=1, update_knowledge_fn=tools.update_knowledge)

    assert out["outcome"] == "audit_only"
    assert out["matches"] == []
    assert out["audit_path"]
    assert (wiki_root / out["audit_path"]).is_file()


def test_missing_baseline_commit_recovers_by_rebaselining(wiki_root):
    _seed_git_review_repo(wiki_root)
    current = state.load()
    current["git"]["last_processed_commit"] = "deadbeef"
    state.save(current)

    out = nightly.run_nightly(
        budget=1,
        dry_run=False,
        llm_client=FakeLLM(),
        update_knowledge_fn=tools.update_knowledge,
    )

    assert out["outcome"] == "baseline_initialized"
    assert str(out["reason"]).startswith("missing_baseline_commit:")
    assert state.load()["git"]["last_processed_commit"] == _git(wiki_root, "rev-parse", "HEAD")


def test_run_nightly_dry_run_leaves_state_unchanged(wiki_root):
    baseline = _seed_git_review_repo(wiki_root)
    (wiki_root / "services" / "auth" / "handler.py").write_text(
        "def login():\n    return 'new'\n",
        encoding="utf-8",
    )
    _commit_all(wiki_root, "change auth")
    before = read_text(state_path())

    out = nightly.run_nightly(
        budget=1,
        dry_run=True,
        llm_client=FakeLLM(),
        update_knowledge_fn=tools.update_knowledge,
    )

    assert out["outcome"] == "dry_run"
    assert out["planned_reviews"][0]["page_name"] == "modules/Auth Service"
    assert read_text(state_path()) == before
    assert state.load()["git"]["last_processed_commit"] == baseline
