from __future__ import annotations

from mcp_server import server, validate
from mcp_server.init_corpus import init_corpus


def test_init_scaffolds_and_validate(tmp_path, monkeypatch):
    out = init_corpus(tmp_path)
    assert out["initialized"] is True
    monkeypatch.setenv("WIKI_KEEPER_ROOT", str(tmp_path))
    report = validate.run().to_dict()
    assert report["ok"] is True


def test_server_exposes_12_tools():
    names = [tool.name for tool in server._TOOLS]
    assert len(names) == 12
    assert "validate" in names
    assert "run_review" in names
    assert "ingest_source" not in names
    assert "propose_ingest" not in names
