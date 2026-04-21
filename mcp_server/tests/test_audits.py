from __future__ import annotations

from datetime import datetime, timezone

from mcp_server import audits


def _write(wiki_root, article_id: str, run_at: datetime):
    return audits.write_audit(
        article_id=article_id,
        article_path=".wiki-keeper/wiki/modules/Auth Service.md",
        source_globs=["services/auth/**"],
        inspected_files=["services/auth/handler.go"],
        reader_a="reader a",
        reader_b="reader b",
        confidence="low",
        decision="audit_only",
        rationale="test",
        diff_text="",
        notes=[],
        run_at=run_at,
    )


def test_write_audit_same_second_is_unique(wiki_root):  # noqa: ARG001
    run_at = datetime(2026, 4, 20, 8, 15, 30, tzinfo=timezone.utc)
    first = _write(wiki_root, "modules/Auth Service", run_at)
    second = _write(wiki_root, "modules/Auth Service", run_at)

    assert first != second
    assert first.is_file()
    assert second.is_file()

    rows = audits.list_audits("modules/Auth Service", limit=10)
    assert len(rows) == 2


def test_list_audits_zero_limit_returns_empty(wiki_root):  # noqa: ARG001
    run_at = datetime(2026, 4, 20, 8, 15, 30, tzinfo=timezone.utc)
    _write(wiki_root, "modules/Auth Service", run_at)
    assert audits.list_audits("modules/Auth Service", limit=0) == []
