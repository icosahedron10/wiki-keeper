from __future__ import annotations

from mcp_server.app import tools
from mcp_server.core.paths import roadmap_path
from mcp_server.core.storage import atomic_write
from mcp_server.wiki import roadmap


def test_load_entries_parses_bullets_and_dedupes(wiki_root):
    atomic_write(
        roadmap_path(),
        "# Wiki Review Roadmap\n\n- modules/Auth Service\n- modules/Auth Service\n",
    )
    tools.update_knowledge(
        "modules/Auth Service",
        "# Auth Service\n\n## Summary\nx\n\n## Open Questions\n- None.\n",
        mode="replace",
    )
    entries = roadmap.load_entries()
    assert entries == ["modules/Auth Service"]


def test_next_entry_wraps():
    entries = ["a", "b", "c"]
    assert roadmap.next_entry(entries, -1) == (0, "a")
    assert roadmap.next_entry(entries, 2) == (0, "a")


def test_resolve_entries_reports_unknown(wiki_root):
    atomic_write(roadmap_path(), "- modules/Unknown\n")
    resolved, unknown = roadmap.resolve_entries(roadmap.load_entries())
    assert not resolved
    assert unknown == ["modules/Unknown"]
