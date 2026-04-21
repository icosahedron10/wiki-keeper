from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server import state as state_mod
from mcp_server.init_corpus import DEFAULT_SCHEMA

@pytest.fixture()
def wiki_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Spin up an isolated wiki-keeper root with .wiki-keeper corpus."""
    corpus = tmp_path / ".wiki-keeper"
    (corpus / "wiki" / "decisions").mkdir(parents=True)
    (corpus / "wiki" / "modules").mkdir(parents=True)
    (corpus / "wiki" / "concepts").mkdir(parents=True)
    for sub in ("architecture", "debugging", "prs", "docs", "meetings", "misc"):
        (corpus / "sources" / sub).mkdir(parents=True)
    (corpus / "audits").mkdir(parents=True)

    (corpus / "schema.md").write_text(DEFAULT_SCHEMA + "\n", encoding="utf-8")
    (corpus / "roadmap.md").write_text("# Wiki Review Roadmap\n", encoding="utf-8")
    (corpus / "state.json").write_text(
        json.dumps(state_mod.DEFAULT_STATE, indent=2) + "\n",
        encoding="utf-8",
    )

    (corpus / "wiki" / "index.md").write_text(
        "# Wiki Index\n", encoding="utf-8"
    )
    (corpus / "wiki" / "log.md").write_text(
        "# Wiki Log\n\nFormat: <iso-timestamp> <tool> <action> <target>\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("WIKI_KEEPER_ROOT", str(tmp_path))

    # Force fresh path resolution (paths.repo_root reads env each call, so this is fine).
    yield tmp_path
