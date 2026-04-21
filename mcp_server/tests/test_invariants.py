from __future__ import annotations

from pathlib import Path

from mcp_server import tools
from mcp_server.paths import index_path, log_path
from mcp_server.storage import atomic_append, atomic_write


PAGE_BODY = """# Auth Service

## Summary
Handles login, session, and token issuance.

## Key Facts
- Issues JWTs with a 15 minute TTL.

## Details
The service fronts Postgres via a session table.

## Relationships
- Related to [[Checkout Pipeline]]

## Sources
- [pr_184_summary.md](../../sources/prs/pr_184_summary.md)

## Open Questions
- None.
"""


def _log_lines(wiki_root: Path) -> list[str]:
    return log_path().read_text(encoding="utf-8").splitlines()


def test_update_creates_page_and_updates_index_and_log(wiki_root: Path):
    before_log = _log_lines(wiki_root)
    result = tools.update_knowledge("modules/Auth Service", PAGE_BODY, mode="create_only")
    assert result["created"] is True
    page_path = wiki_root / ".wiki-keeper" / "wiki" / "modules" / "Auth Service.md"
    assert page_path.is_file()

    index_text = index_path().read_text(encoding="utf-8")
    assert "modules/Auth Service.md" in index_text

    after_log = _log_lines(wiki_root)
    assert len(after_log) == len(before_log) + 1
    assert "Auth Service" in after_log[-1]
    assert "update_knowledge" in after_log[-1]


def test_update_replace_preserves_one_log_line(wiki_root: Path):
    tools.update_knowledge("modules/Auth Service", PAGE_BODY, mode="create_only")
    baseline = len(_log_lines(wiki_root))
    tools.update_knowledge("modules/Auth Service", PAGE_BODY + "\nextra.\n")
    assert len(_log_lines(wiki_root)) == baseline + 1


def test_create_only_rejects_existing(wiki_root: Path):
    tools.update_knowledge("concepts/Feature Flags", PAGE_BODY)
    try:
        tools.update_knowledge("concepts/Feature Flags", PAGE_BODY, mode="create_only")
    except ValueError:
        return
    raise AssertionError("create_only should have rejected existing page")


def test_lint_flags_missing_sources(wiki_root: Path):
    atomic_write(
        wiki_root / ".wiki-keeper" / "wiki" / "concepts" / "Bad Page.md",
        "# Bad Page\n\n## Summary\nNo sources.\n",
    )
    tools.rebuild_index()
    report = tools.lint_wiki()
    assert any("Bad Page.md" in p for p in report["missing_sources"])


def test_lint_flags_broken_wikilink(wiki_root: Path):
    content = PAGE_BODY.replace("[[Checkout Pipeline]]", "[[Nonexistent Page]]")
    tools.update_knowledge("modules/Auth Service", content, mode="create_only")
    report = tools.lint_wiki()
    assert any(
        link["link"] == "Nonexistent Page" for link in report["broken_links"]
    )


def test_lint_flags_orphan(wiki_root: Path):
    atomic_write(
        wiki_root / ".wiki-keeper" / "wiki" / "concepts" / "Stub Page.md",
        "# Stub Page\n> stub\n",
    )
    # deliberately skip rebuild_index so the page is an orphan
    report = tools.lint_wiki()
    assert any("Stub Page.md" in o for o in report["orphans"])
    assert any("Stub Page.md" in m for m in report["not_in_index"])


def test_ingest_source_records_log_entry(wiki_root: Path):
    src = wiki_root / ".wiki-keeper" / "sources" / "prs" / "pr_184_summary.md"
    src.write_text("# PR 184\n\nBumped retry limit from 3 to 5.\n", encoding="utf-8")
    before = len(_log_lines(wiki_root))
    out = tools.ingest_source("prs/pr_184_summary.md", context="review")
    assert out["log_updated"] is True
    assert "Bumped retry limit" in out["source_content"]
    assert len(_log_lines(wiki_root)) == before + 1


def test_propose_ingest_does_not_log(wiki_root: Path):
    src = wiki_root / ".wiki-keeper" / "sources" / "prs" / "pr_200.md"
    src.write_text("# PR 200\n\nRefactored auth.\n", encoding="utf-8")
    before = len(_log_lines(wiki_root))
    out = tools.propose_ingest("prs/pr_200.md")
    assert "source_content" in out
    assert len(_log_lines(wiki_root)) == before


def test_query_wiki_keyword_ranks_title_matches(wiki_root: Path):
    tools.update_knowledge("modules/Auth Service", PAGE_BODY, mode="create_only")
    tools.update_knowledge(
        "concepts/Retry Policy",
        "# Retry Policy\n\n## Summary\nRetries.\n\n## Sources\n- [x](../sources/misc/x.md)\n",
        mode="create_only",
    )
    out = tools.query_wiki("auth")
    titles = [h["title"] for h in out["hits"]]
    assert titles and titles[0] == "Auth Service"


def test_query_wiki_non_positive_top_k_returns_no_hits(wiki_root: Path):
    tools.update_knowledge("modules/Auth Service", PAGE_BODY, mode="create_only")
    assert tools.query_wiki("auth", top_k=0)["hits"] == []
    assert tools.query_wiki("auth", top_k=-1)["hits"] == []


def test_path_traversal_rejected(wiki_root: Path):
    try:
        tools.ingest_source("../schema.md")
    except ValueError:
        return
    raise AssertionError("path traversal should have been rejected")


def test_ingest_source_multiline_context_stays_single_log_event(wiki_root: Path):
    src = wiki_root / ".wiki-keeper" / "sources" / "prs" / "pr_201.md"
    src.write_text("# PR 201\n\nChanged context formatting.\n", encoding="utf-8")
    before = len(_log_lines(wiki_root))
    tools.ingest_source("prs/pr_201.md", context="line one\nline two\r\nline three")
    after = _log_lines(wiki_root)
    assert len(after) == before + 1
    assert "line one line two line three" in after[-1]


def test_atomic_append_preserves_existing_content(wiki_root: Path):
    p = log_path()
    atomic_write(p, "# Header")
    atomic_append(p, "new event")
    assert p.read_text(encoding="utf-8") == "# Header\nnew event\n"
