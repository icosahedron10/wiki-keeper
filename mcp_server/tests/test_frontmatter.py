from __future__ import annotations

import pytest

from mcp_server.core.frontmatter import parse_frontmatter, validate_frontmatter


def test_parse_frontmatter_valid():
    content = (
        "---\n"
        "id: auth-overview\n"
        "title: Authentication Overview\n"
        "sources:\n"
        "  - services/auth/**\n"
        "---\n"
        "# Authentication Overview\n"
    )
    fm, body = parse_frontmatter(content)
    assert fm is not None
    assert fm["id"] == "auth-overview"
    assert fm["sources"] == ["services/auth/**"]
    assert body.startswith("# Authentication Overview")


def test_parse_frontmatter_missing_block_returns_none():
    fm, body = parse_frontmatter("# No frontmatter\n")
    assert fm is None
    assert body == "# No frontmatter\n"


def test_parse_frontmatter_malformed_yaml():
    with pytest.raises(ValueError):
        parse_frontmatter("---\nfoo: [bar\n---\n# Title\n")


def test_validate_frontmatter_sources_must_be_list():
    errors = validate_frontmatter({"sources": "services/auth/**"})
    assert errors
    assert "must be a list" in errors[0]
