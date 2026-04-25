from __future__ import annotations

import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mcp_server import server, tools
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


def test_update_append_serializes_concurrent_writes(wiki_root: Path):
    tools.update_knowledge("concepts/Concurrent Notes", "# Concurrent Notes\n", mode="create_only")

    def _append(idx: int) -> None:
        tools.update_knowledge("concepts/Concurrent Notes", f"line-{idx}\n", mode="append")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_append, range(20)))

    lines = tools.get_page("concepts/Concurrent Notes")["content"].splitlines()
    for idx in range(20):
        assert lines.count(f"line-{idx}") == 1


def test_create_only_rejects_existing(wiki_root: Path):
    tools.update_knowledge("concepts/Feature Flags", PAGE_BODY)
    try:
        tools.update_knowledge("concepts/Feature Flags", PAGE_BODY, mode="create_only")
    except ValueError:
        return
    raise AssertionError("create_only should have rejected existing page")


def test_lint_does_not_require_sources_section(wiki_root: Path):
    atomic_write(
        wiki_root / ".wiki-keeper" / "wiki" / "concepts" / "Bad Page.md",
        "# Bad Page\n\n## Summary\nNo sources.\n",
    )
    tools.rebuild_index()
    report = tools.lint_wiki()
    assert report["missing_sources"] == []


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
    tools.rebuild_index()
    report = tools.lint_wiki()
    assert any("Stub Page.md" in o for o in report["orphans"])
    assert not any("Stub Page.md" in m for m in report["not_in_index"])


def test_removed_ingest_tools_are_unavailable():
    assert not hasattr(tools, "ingest_source")
    assert not hasattr(tools, "propose_ingest")
    with pytest.raises(ValueError):
        asyncio.run(server.dispatch_tool("ingest_source", {"source_path": "prs/pr_1.md"}))


def test_query_wiki_keyword_ranks_title_matches(wiki_root: Path):
    tools.update_knowledge("modules/Auth Service", PAGE_BODY, mode="create_only")
    tools.update_knowledge(
        "concepts/Retry Policy",
        "# Retry Policy\n\n## Summary\nRetries.\n\n## Open Questions\n- None.\n",
        mode="create_only",
    )
    out = tools.query_wiki("auth")
    titles = [h["title"] for h in out["hits"]]
    assert titles and titles[0] == "Auth Service"


def test_query_wiki_non_positive_top_k_returns_no_hits(wiki_root: Path):
    tools.update_knowledge("modules/Auth Service", PAGE_BODY, mode="create_only")
    assert tools.query_wiki("auth", top_k=0)["hits"] == []
    assert tools.query_wiki("auth", top_k=-1)["hits"] == []


def test_server_tool_schema_allows_zero_limits():
    query_tool = server.TOOLS_BY_NAME["query_wiki"]
    audits_tool = server.TOOLS_BY_NAME["read_audits"]
    assert query_tool.input_schema["properties"]["top_k"]["minimum"] == 0
    assert audits_tool.input_schema["properties"]["limit"]["minimum"] == 0


def test_atomic_append_preserves_existing_content(wiki_root: Path):
    p = log_path()
    atomic_write(p, "# Header")
    atomic_append(p, "new event")
    assert p.read_text(encoding="utf-8") == "# Header\nnew event\n"


def test_atomic_append_handles_partial_os_write(wiki_root: Path, monkeypatch: pytest.MonkeyPatch):
    p = log_path()
    atomic_write(p, "")
    original_write = os.write

    def _partial_write(fd: int, data: bytes) -> int:
        return original_write(fd, data[:3])

    monkeypatch.setattr("mcp_server.storage.os.write", _partial_write)
    atomic_append(p, "partial-write-safe")
    assert p.read_text(encoding="utf-8") == "partial-write-safe\n"
