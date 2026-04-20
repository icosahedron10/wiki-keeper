from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture()
def wiki_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Spin up an isolated wiki-keeper root with .wiki-keeper corpus."""
    corpus = tmp_path / ".wiki-keeper"
    (corpus / "wiki" / "decisions").mkdir(parents=True)
    (corpus / "wiki" / "modules").mkdir(parents=True)
    (corpus / "wiki" / "concepts").mkdir(parents=True)
    (corpus / "audits").mkdir(parents=True)

    repo_schema = Path(__file__).resolve().parents[2] / ".wiki-keeper" / "schema.md"
    shutil.copy(repo_schema, corpus / "schema.md")
    (corpus / "roadmap.md").write_text("# Wiki Review Roadmap\n", encoding="utf-8")
    (corpus / "state.json").write_text(
        '{\n  "cursor": {"article_id": null, "index": -1},\n  "last_run": null,\n  "history": []\n}\n',
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
