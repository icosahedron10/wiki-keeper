from __future__ import annotations

from mcp_server import tools
from mcp_server.paths import roadmap_path
from mcp_server.storage import atomic_write


def test_validate_passes_for_fresh_fixture(wiki_root):
    report = tools.validate()
    assert report["ok"] is True


def test_validate_flags_bad_frontmatter(wiki_root):
    tools.update_knowledge(
        "modules/Auth Service",
        "---\nfoo: [bar\n---\n# Auth Service\n\n## Summary\nx\n\n## Sources\n- [x](../../sources/prs/pr_184_summary.md)\n",
        mode="replace",
    )
    report = tools.validate()
    assert report["ok"] is False
    assert any("invalid frontmatter" in e for e in report["errors"])


def test_validate_flags_unknown_roadmap_entry(wiki_root):
    atomic_write(roadmap_path(), "- modules/Does Not Exist\n")
    report = tools.validate()
    assert report["ok"] is False
    assert any("Roadmap entry does not resolve" in e for e in report["errors"])


def test_validate_flags_unresolvable_frontmatter_sources(wiki_root):
    tools.update_knowledge(
        "modules/Auth Service",
        (
            "---\n"
            "id: auth-overview\n"
            "sources:\n"
            "  - does/not/exist/**\n"
            "---\n"
            "# Auth Service\n\n"
            "## Summary\nx\n\n"
            "## Sources\n- [x](../../sources/prs/pr_184_summary.md)\n"
        ),
        mode="replace",
    )
    atomic_write(roadmap_path(), "- modules/Auth Service\n")
    report = tools.validate()
    assert report["ok"] is False
    assert any("matched no files" in e for e in report["errors"])
