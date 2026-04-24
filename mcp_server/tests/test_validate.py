from __future__ import annotations

from mcp_server import tools
from mcp_server.paths import roadmap_path
from mcp_server.storage import atomic_write
from mcp_server.validate import page_is_schema_compliant


def test_validate_passes_for_fresh_fixture(wiki_root):
    report = tools.validate()
    assert report["ok"] is True


def test_validate_flags_bad_frontmatter(wiki_root):
    tools.update_knowledge(
        "modules/Auth Service",
        "---\nfoo: [bar\n---\n# Auth Service\n\n## Summary\nx\n\n## Open Questions\n- None.\n",
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
            "## Open Questions\n- None.\n"
        ),
        mode="replace",
    )
    atomic_write(roadmap_path(), "- modules/Auth Service\n")
    report = tools.validate()
    assert report["ok"] is False
    assert any("matched no files" in e for e in report["errors"])


def test_schema_compliance_requires_real_headings_not_code_fences():
    content = (
        "# Example\n\n"
        "```md\n"
        "## Summary\n"
        "## Key Facts\n"
        "## Details\n"
        "## Relationships\n"
        "## Sources\n"
        "## Open Questions\n"
        "```\n"
    )
    assert page_is_schema_compliant(content) is False


def test_schema_compliance_accepts_all_required_headings():
    content = (
        "# Example\n\n"
        "## Summary\nx\n\n"
        "## Key Facts\n- x\n\n"
        "## Details\nx\n\n"
        "## Relationships\n- [[Example]]\n\n"
        "## Sources\n- [x](../../sources/misc/x.md)\n\n"
        "## Open Questions\n- None.\n"
    )
    assert page_is_schema_compliant(content) is True


def test_schema_compliance_rejects_non_stub_without_source_items():
    content = (
        "# Example\n\n"
        "## Summary\nx\n\n"
        "## Key Facts\n- x\n\n"
        "## Details\nx\n\n"
        "## Relationships\n- [[Example]]\n\n"
        "## Sources\n\n"
        "## Open Questions\n- None.\n"
    )
    assert page_is_schema_compliant(content) is False


def test_schema_compliance_allows_stub_without_sources_section():
    content = (
        "# Example\n"
        "> stub\n\n"
        "## Summary\nx\n\n"
        "## Key Facts\n- x\n\n"
        "## Details\nx\n\n"
        "## Relationships\n- [[Example]]\n\n"
        "## Open Questions\n- None.\n"
    )
    assert page_is_schema_compliant(content) is True


def test_validate_skips_source_scan_when_sources_type_invalid(wiki_root):
    tools.update_knowledge(
        "modules/Auth Service",
        (
            "---\n"
            "id: auth-overview\n"
            "sources: services/auth/**\n"
            "---\n"
            "# Auth Service\n\n"
            "## Summary\nx\n\n"
            "## Sources\n- [x](../../sources/prs/pr_184_summary.md)\n"
        ),
        mode="replace",
    )
    report = tools.validate()
    assert report["ok"] is False
    assert any(
        "frontmatter.sources must be a list of glob strings" in e for e in report["errors"]
    )
    assert not any("matched no files" in e for e in report["errors"])


def test_validate_requires_sections_and_sources_for_non_stub(wiki_root):
    tools.update_knowledge(
        "concepts/Bad Page",
        "# Bad Page\n\n## Summary\nNo sources.\n",
        mode="replace",
    )
    report = tools.validate()
    assert report["ok"] is False
    assert any("missing required section ## Key Facts" in e for e in report["errors"])
    assert any("non-stub page must include" in e for e in report["errors"])


def test_validate_allows_stub_without_sources_section(wiki_root):
    tools.update_knowledge(
        "concepts/Stub Page",
        (
            "# Stub Page\n"
            "> stub\n\n"
            "## Summary\nStub.\n\n"
            "## Key Facts\n- Unknown.\n\n"
            "## Details\nPending.\n\n"
            "## Relationships\n- None yet.\n\n"
            "## Open Questions\n- What should be documented first?\n"
        ),
        mode="replace",
    )
    report = tools.validate()
    assert not any("Stub Page.md: missing required section ## Sources" in e for e in report["errors"])
