from __future__ import annotations

from pathlib import Path

import yaml

from mcp_server import server, validate
from mcp_server.init_corpus import init_corpus


def test_init_scaffolds_and_validate(tmp_path, monkeypatch):
    out = init_corpus(tmp_path)
    assert out["initialized"] is True
    monkeypatch.setenv("WIKI_KEEPER_ROOT", str(tmp_path))
    report = validate.run().to_dict()
    assert report["ok"] is True


def test_server_exposes_13_tools():
    names = [tool.name for tool in server._TOOLS]
    assert len(names) == 13
    assert "validate" in names
    assert "run_review" in names
    assert "run_nightly" in names
    assert "ingest_source" not in names
    assert "propose_ingest" not in names


def test_workflow_yaml_parses():
    root = Path(__file__).resolve().parents[2]
    paths = [
        *root.joinpath(".github").rglob("*.yml"),
        *root.joinpath(".github").rglob("*.yaml"),
        *root.joinpath(".github").rglob("action.yml"),
        *root.joinpath("docs", "workflows").rglob("*.yml"),
    ]
    assert paths
    for path in paths:
        assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None
