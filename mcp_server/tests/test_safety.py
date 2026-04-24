from __future__ import annotations

import pytest

from mcp_server import nightly, tools
from mcp_server.paths import roadmap_path
from mcp_server.storage import atomic_write


class CountingLLM:
    def __init__(self):
        self.calls = 0
        self.config = type(
            "Cfg",
            (),
            {
                "reader_model": "gpt-5-nano",
                "reader_reasoning": "low",
                "orchestrator_model": "gpt-5-mini",
                "orchestrator_reasoning": "medium",
            },
        )()

    def complete_text(self, **kwargs):  # noqa: ARG002
        self.calls += 1
        return "reader"

    def complete_json(self, **kwargs):  # noqa: ARG002
        self.calls += 1
        return {
            "confidence": "low",
            "decision": "audit_only",
            "patch_content": "",
            "rationale": "test",
        }

    def complete_json_schema(self, **kwargs):  # noqa: ARG002
        return self.complete_json(**kwargs)


def _seed(wiki_root):
    (wiki_root / "services" / "auth").mkdir(parents=True, exist_ok=True)
    (wiki_root / "services" / "auth" / "handler.go").write_text("ok", encoding="utf-8")


def test_update_knowledge_rejects_path_escape(wiki_root):
    with pytest.raises(ValueError):
        tools.update_knowledge("modules/../Escape", "# bad\n")


def test_nightly_rejects_glob_escape_before_llm(wiki_root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    _seed(wiki_root)
    atomic_write(roadmap_path(), "- modules/Auth Service\n")
    tools.update_knowledge(
        "modules/Auth Service",
        (
            "---\n"
            "id: auth-overview\n"
            "sources:\n"
            "  - ../**\n"
            "---\n"
            "# Auth Service\n\n## Summary\nx\n\n## Open Questions\n- None.\n"
        ),
    )
    llm = CountingLLM()
    with pytest.raises(RuntimeError):
        nightly.run_review(
            article_id=None,
            llm_client=llm,
            update_knowledge_fn=tools.update_knowledge,
        )
    assert llm.calls == 0
