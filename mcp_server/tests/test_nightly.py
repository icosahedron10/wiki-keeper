from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_server import nightly, tools
from mcp_server.paths import roadmap_path
from mcp_server.storage import atomic_write, read_text


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


class FakeLLM:
    def __init__(self, *, confidence: str, decision: str, patch_content: str):
        self.config = SimpleNamespace(
            reader_model="gpt-5-nano",
            reader_reasoning="low",
            orchestrator_model="gpt-5-mini",
            orchestrator_reasoning="medium",
        )
        self._decision = {
            "confidence": confidence,
            "decision": decision,
            "patch_content": patch_content,
            "rationale": "synthetic decision",
        }

    def complete_text(self, **kwargs):  # noqa: ARG002
        return "synthetic reader findings"

    def complete_json(self, **kwargs):  # noqa: ARG002
        return dict(self._decision)

    def complete_json_schema(self, **kwargs):  # noqa: ARG002
        return dict(self._decision)


def _seed_review_target(wiki_root):
    atomic_write(roadmap_path(), "- modules/Auth Service\n")
    (wiki_root / "services" / "auth").mkdir(parents=True, exist_ok=True)
    (wiki_root / "services" / "auth" / "handler.go").write_text(
        "package auth\n\nfunc Login() {}\n",
        encoding="utf-8",
    )
    tools.update_knowledge("modules/Auth Service", ARTICLE_WITH_FRONTMATTER, mode="replace")


def test_nightly_high_confidence_applies_patch(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _seed_review_target(wiki_root)
    fake = FakeLLM(confidence="high", decision="patch", patch_content=PATCHED_BODY)
    out = nightly.run_review(
        article_id=None,
        llm_client=fake,
        update_knowledge_fn=tools.update_knowledge,
    )
    assert out["outcome"] == "patched"
    page = tools.get_page("modules/Auth Service")
    assert "Updated summary from nightly pass." in page["content"]
    assert out["audit_path"]


def test_nightly_low_confidence_is_audit_only(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _seed_review_target(wiki_root)
    fake = FakeLLM(confidence="low", decision="patch", patch_content=PATCHED_BODY)
    before = read_text((wiki_root / ".wiki-keeper" / "wiki" / "modules" / "Auth Service.md"))
    out = nightly.run_review(
        article_id=None,
        llm_client=fake,
        update_knowledge_fn=tools.update_knowledge,
    )
    after = read_text((wiki_root / ".wiki-keeper" / "wiki" / "modules" / "Auth Service.md"))
    assert out["outcome"] == "audit_only"
    assert before == after


def test_nightly_rejects_non_schema_patch(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _seed_review_target(wiki_root)
    fake = FakeLLM(
        confidence="high",
        decision="patch",
        patch_content="# Auth Service\n\nNo required sections.\n",
    )
    out = nightly.run_review(
        article_id=None,
        llm_client=fake,
        update_knowledge_fn=tools.update_knowledge,
    )
    assert out["outcome"] == "audit_only"


def test_nightly_missing_api_key_fails_before_model_call(wiki_root, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed_review_target(wiki_root)
    fake = FakeLLM(confidence="high", decision="patch", patch_content=PATCHED_BODY)
    with pytest.raises(RuntimeError):
        nightly.run_review(
            article_id=None,
            llm_client=fake,
            update_knowledge_fn=tools.update_knowledge,
        )
