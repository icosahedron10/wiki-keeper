from __future__ import annotations

from mcp_server import server, validate
from mcp_server.init_corpus import init_corpus


def test_init_scaffolds_and_validate(tmp_path, monkeypatch):
    out = init_corpus(tmp_path, offline=True)
    assert out["initialized"] is True
    monkeypatch.setenv("WIKI_KEEPER_ROOT", str(tmp_path))
    report = validate.run().to_dict()
    assert report["ok"] is True
    assert out["created_pages"]


def test_server_exposes_15_tools():
    names = [tool.name for tool in server._TOOLS]
    assert len(names) == 15
    assert "validate" in names
    assert "run_review" in names
    assert "initialize_wiki" in names
