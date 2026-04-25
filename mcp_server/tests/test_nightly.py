from __future__ import annotations

import json

import pytest

from mcp_server import git_delta, nightly, tools
from mcp_server.storage import read_text


ARTICLE_WITH_FRONTMATTER = """---
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
- [pr_184_summary.md](../../sources/prs/pr_184_summary.md)

## Open Questions
- None.
"""


PATCHED_BODY = """# Auth Service

## Summary
Updated summary from nightly pass.

## Key Facts
- Updated fact.

## Details
Updated details.

## Relationships
- Related to [[Auth Service]]

## Sources
- [pr_184_summary.md](../../sources/prs/pr_184_summary.md)

## Open Questions
- None.
"""


class FakeResponses:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"output_text": json.dumps(self.payload)}


class FakeClient:
    def __init__(self, payload: dict):
        self.responses = FakeResponses(payload)


def _seed_review_target(wiki_root):
    (wiki_root / "services" / "auth").mkdir(parents=True, exist_ok=True)
    (wiki_root / "services" / "auth" / "handler.go").write_text("package auth\n\nfunc Login() {}\n", encoding="utf-8")
    tools.update_knowledge("modules/Auth Service", ARTICLE_WITH_FRONTMATTER, mode="replace")


def _range():
    return git_delta.GitRange(since="abc", until="def", default_branch="main", changed_paths=["services/auth/handler.go"])


def _patch_payload(confidence: str = "high", patch_content: str = PATCHED_BODY) -> dict:
    return {
        "article_decisions": [
            {
                "article_id": "auth-overview",
                "decision": "patch",
                "confidence": confidence,
                "rationale": "synthetic decision",
                "patch_content": patch_content,
                "audit_notes": ["reviewed once"],
            }
        ]
    }


def test_run_review_scopes_model_input_to_requested_article(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _seed_review_target(wiki_root)
    tools.update_knowledge(
        "modules/Billing Service",
        ARTICLE_WITH_FRONTMATTER.replace("auth-overview", "billing-overview")
        .replace("Auth Overview", "Billing Overview")
        .replace("services/auth/**", "services/billing/**")
        .replace("# Auth Service", "# Billing Service"),
        mode="replace",
    )
    (wiki_root / ".wiki-keeper" / "roadmap.md").write_text(
        "# Wiki Review Roadmap\n- modules/Auth Service\n- modules/Billing Service\n",
        encoding="utf-8",
    )
    commit_range = git_delta.GitRange(
        since="abc",
        until="def",
        default_branch="main",
        changed_paths=["services/auth/handler.go", "services/billing/handler.go"],
    )
    monkeypatch.setattr(git_delta, "build_range", lambda **_kwargs: (commit_range, None))
    monkeypatch.setattr(git_delta, "diff_source_files", lambda *_args, **_kwargs: [])
    fake = FakeClient(_patch_payload())
    out = nightly.run_review(article_id="auth-overview", client=fake, update_knowledge_fn=tools.update_knowledge)
    assert out["outcome"] == "patched"
    assert [match["article_id"] for match in out["matches"]] == ["auth-overview"]
    assert len(fake.responses.calls) == 1
    assert "auth-overview" in fake.responses.calls[0]["input"]
    assert "billing-overview" not in fake.responses.calls[0]["input"]


def test_run_review_with_unmatched_article_does_not_call_model(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _seed_review_target(wiki_root)
    monkeypatch.setattr(git_delta, "build_range", lambda **_kwargs: (_range(), None))
    fake = FakeClient(_patch_payload())
    out = nightly.run_review(article_id="billing-overview", client=fake, update_knowledge_fn=tools.update_knowledge)
    assert out["outcome"] == "no_matching_article"
    assert out["article_id"] == "billing-overview"
    assert out["matches"] == []
    assert fake.responses.calls == []


def test_nightly_whole_range_call_applies_high_confidence_patch(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _seed_review_target(wiki_root)
    monkeypatch.setattr(git_delta, "build_range", lambda **_kwargs: (_range(), None))
    monkeypatch.setattr(git_delta, "diff_source_files", lambda *_args, **_kwargs: [])
    fake = FakeClient(_patch_payload())
    out = nightly.run_nightly(client=fake, update_knowledge_fn=tools.update_knowledge)
    assert out["outcome"] == "patched"
    assert out["patch_status"] == "patched"
    assert len(fake.responses.calls) == 1
    assert fake.responses.calls[0]["model"] == "gpt-5.4-nano"
    assert "Updated summary from nightly pass." in tools.get_page("modules/Auth Service")["content"]


def test_nightly_low_confidence_is_audit_only(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _seed_review_target(wiki_root)
    monkeypatch.setattr(git_delta, "build_range", lambda **_kwargs: (_range(), None))
    monkeypatch.setattr(git_delta, "diff_source_files", lambda *_args, **_kwargs: [])
    before = read_text(wiki_root / ".wiki-keeper" / "wiki" / "modules" / "Auth Service.md")
    out = nightly.run_nightly(client=FakeClient(_patch_payload(confidence="low")), update_knowledge_fn=tools.update_knowledge)
    after = read_text(wiki_root / ".wiki-keeper" / "wiki" / "modules" / "Auth Service.md")
    assert out["outcome"] == "audit_only"
    assert before == after


def test_nightly_rejects_non_schema_patch(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _seed_review_target(wiki_root)
    monkeypatch.setattr(git_delta, "build_range", lambda **_kwargs: (_range(), None))
    monkeypatch.setattr(git_delta, "diff_source_files", lambda *_args, **_kwargs: [])
    out = nightly.run_nightly(
        client=FakeClient(_patch_payload(patch_content="# Auth Service\n\nNo required sections.\n")),
        update_knowledge_fn=tools.update_knowledge,
    )
    assert out["outcome"] == "audit_only"


def test_nightly_no_changed_paths_avoids_model_call(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _seed_review_target(wiki_root)
    empty = git_delta.GitRange(since="abc", until="def", default_branch="main", changed_paths=[])
    monkeypatch.setattr(git_delta, "build_range", lambda **_kwargs: (empty, None))
    fake = FakeClient(_patch_payload())
    out = nightly.run_nightly(client=fake, update_knowledge_fn=tools.update_knowledge)
    assert out["outcome"] == "no_changes"
    assert fake.responses.calls == []


def test_nightly_unmapped_changes_write_audit_without_model_call(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    change = git_delta.GitRange(since="abc", until="def", default_branch="main", changed_paths=["other/file.py"])
    monkeypatch.setattr(git_delta, "build_range", lambda **_kwargs: (change, None))
    fake = FakeClient(_patch_payload())
    out = nightly.run_nightly(client=fake, update_knowledge_fn=tools.update_knowledge)
    assert out["outcome"] == "audit_only"
    assert out["audit_paths"]
    assert fake.responses.calls == []


def test_nightly_missing_api_key_fails_before_model_call(wiki_root, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed_review_target(wiki_root)
    monkeypatch.setattr(git_delta, "build_range", lambda **_kwargs: (_range(), None))
    fake = FakeClient(_patch_payload())
    with pytest.raises(RuntimeError):
        nightly.run_nightly(client=fake, update_knowledge_fn=tools.update_knowledge)
    assert fake.responses.calls == []
