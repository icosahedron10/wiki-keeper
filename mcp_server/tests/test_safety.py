from __future__ import annotations

import pytest

from mcp_server import git_delta, tools
from mcp_server.paths import roadmap_path
from mcp_server.storage import atomic_write


def _seed(wiki_root):
    (wiki_root / "services" / "auth").mkdir(parents=True, exist_ok=True)
    (wiki_root / "services" / "auth" / "handler.go").write_text("ok", encoding="utf-8")


def test_update_knowledge_rejects_path_escape(wiki_root):
    with pytest.raises(ValueError):
        tools.update_knowledge("modules/../Escape", "# bad\n")


def test_git_delta_glob_escape_does_not_match_host_paths(wiki_root):
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
    assert git_delta.map_changed_paths_to_articles(["services/auth/handler.go"]) == []
