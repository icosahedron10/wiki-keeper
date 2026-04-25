from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.bootstrap.init_bootstrap import validate_synthesis_payload
from mcp_server.bootstrap.init_corpus import detect_host_repo_root, initialize_wiki
from mcp_server.bootstrap.monorepo_inventory import bounded_slice_previews, collect_inventory
from mcp_server.core.pages import parse_page_frontmatter
from mcp_server.core.storage import read_text
from mcp_server.wiki.validate import page_is_schema_compliant


class FakeResponses:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"output_text": json.dumps(self.payload)}


class FakeClient:
    def __init__(self, payload: dict):
        self.responses = FakeResponses(payload)


def _seed_monorepo(root: Path) -> None:
    (root / "apps" / "web").mkdir(parents=True)
    (root / "packages" / "core").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "apps" / "web" / "package.json").write_text('{"name":"web"}\n', encoding="utf-8")
    (root / "packages" / "core" / "pyproject.toml").write_text("[project]\nname='core'\n", encoding="utf-8")
    (root / "docs" / "README.md").write_text("# docs\n", encoding="utf-8")
    (root / "node_modules" / "leftpad").mkdir(parents=True)
    (root / "node_modules" / "leftpad" / "index.js").write_text("module.exports=1\n", encoding="utf-8")


def _bootstrap_payload() -> dict:
    return {
        "pages": [
            {
                "category": "concepts",
                "title": "Repository Overview",
                "summary": "Repo summary",
                "key_facts": ["Fact"],
                "details": ["Detail"],
                "relationships": ["Related to [[Monorepo Map]]"],
                "sources": ["apps/web/package.json"],
                "open_questions": ["Question"],
                "confidence": "medium",
                "frontmatter_sources": [],
            },
            {
                "category": "modules",
                "title": "Web",
                "summary": "Web module",
                "key_facts": ["Has package manifest"],
                "details": ["Need more data"],
                "relationships": ["Related to [[Repository Overview]]"],
                "sources": ["apps/web/package.json"],
                "open_questions": ["Needs runtime docs"],
                "confidence": "low",
                "frontmatter_sources": ["apps/web/package.json"],
            },
        ],
        "roadmap_entries": ["concepts/Repository Overview", "modules/Web"],
        "open_questions": ["What owns deployments?"],
        "truncated_areas": [],
    }


def _symlink_or_skip(link_path: Path, target_path: Path) -> None:
    try:
        link_path.symlink_to(target_path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation not supported in this environment: {exc}")


def test_detect_host_repo_root_explicit(tmp_path):
    explicit = tmp_path / "host"
    explicit.mkdir()
    assert detect_host_repo_root(explicit_repo=explicit, cwd=tmp_path) == explicit.resolve()


def test_detect_host_repo_root_superproject(tmp_path):
    cwd = tmp_path / "submodule"
    cwd.mkdir()

    def runner(command, _cwd):  # noqa: ANN001
        assert command[-1] == "--show-superproject-working-tree"
        return str(tmp_path / "super")

    assert detect_host_repo_root(explicit_repo=None, cwd=cwd, git_runner=runner) == (tmp_path / "super").resolve()


def test_detect_host_repo_root_falls_back_to_cwd(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    assert detect_host_repo_root(explicit_repo=None, cwd=cwd, git_runner=lambda _command, _cwd: None) == cwd.resolve()


def test_inventory_excludes_generated_and_vendor_paths(tmp_path):
    _seed_monorepo(tmp_path)
    (tmp_path / ".wiki-keeper" / "wiki").mkdir(parents=True)
    (tmp_path / ".wiki-keeper" / "wiki" / "index.md").write_text("# index\n", encoding="utf-8")
    inventory = collect_inventory(tmp_path)
    assert "apps/web/package.json" in inventory.discovered_paths
    assert "packages/core/pyproject.toml" in inventory.discovered_paths
    assert all(not path.startswith("node_modules/") for path in inventory.discovered_paths)
    assert all(not path.startswith(".wiki-keeper/") for path in inventory.discovered_paths)


def test_inventory_skips_symlink_that_resolves_outside_repo(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_monorepo(repo_root)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("top-secret", encoding="utf-8")
    _symlink_or_skip(repo_root / "apps" / "web" / "outside-link.txt", outside)

    inventory = collect_inventory(repo_root)
    preview_paths = {item.path for item in inventory.previews}
    assert "apps/web/outside-link.txt" not in preview_paths
    assert all("top-secret" not in item.preview for item in inventory.previews)


def test_bounded_slice_previews_skips_symlink_that_resolves_outside_repo(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_monorepo(repo_root)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("top-secret", encoding="utf-8")
    _symlink_or_skip(repo_root / "apps" / "web" / "outside-link.txt", outside)
    assert bounded_slice_previews(repo_root, ["apps/web/outside-link.txt"]) == []


def test_synthesis_validation_rejects_speculative_page_sources(tmp_path):
    _seed_monorepo(tmp_path)
    inventory = collect_inventory(tmp_path)
    bad = _bootstrap_payload()
    bad["pages"][0]["sources"] = ["imaginary/path.py"]
    with pytest.raises(ValueError):
        validate_synthesis_payload(bad, available_paths=set(inventory.discovered_paths), inventory=inventory)


def test_synthesis_validation_rejects_path_traversal_title(tmp_path):
    _seed_monorepo(tmp_path)
    inventory = collect_inventory(tmp_path)
    bad = _bootstrap_payload()
    bad["pages"][1]["title"] = "../Escape"
    with pytest.raises(ValueError):
        validate_synthesis_payload(bad, available_paths=set(inventory.discovered_paths), inventory=inventory)


def test_initialize_wiki_requires_api_key_when_bootstrapping(tmp_path, monkeypatch):
    _seed_monorepo(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        initialize_wiki(repo_root=tmp_path)
    assert not (tmp_path / ".wiki-keeper").exists()


def test_initialize_wiki_uses_one_model_call(tmp_path, monkeypatch):
    _seed_monorepo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake = FakeClient(_bootstrap_payload())
    out = initialize_wiki(repo_root=tmp_path, client=fake)
    assert out["status"] == "completed"
    assert out["model"] == "gpt-5.4-mini"
    assert len(fake.responses.calls) == 1
    assert fake.responses.calls[0]["model"] == "gpt-5.4-mini"


def test_initialize_wiki_dry_run_writes_nothing(tmp_path, monkeypatch):
    _seed_monorepo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    out = initialize_wiki(repo_root=tmp_path, client=FakeClient(_bootstrap_payload()), dry_run=True)
    assert out["status"] == "dry_run"
    assert not (tmp_path / ".wiki-keeper").exists()


def test_initialize_wiki_idempotent_and_refresh_audit(tmp_path, monkeypatch):
    _seed_monorepo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    first = initialize_wiki(repo_root=tmp_path, client=FakeClient(_bootstrap_payload()))
    assert first["status"] == "completed"
    first_audit = first["audit_path"]
    second = initialize_wiki(repo_root=tmp_path)
    assert second["status"] == "already_completed"
    refreshed = initialize_wiki(repo_root=tmp_path, client=FakeClient(_bootstrap_payload()), refresh_bootstrap=True)
    assert refreshed["status"] == "refreshed"
    assert refreshed["audit_path"] != first_audit


def test_initialization_generates_expected_pages_and_module_frontmatter(tmp_path, monkeypatch):
    _seed_monorepo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    out = initialize_wiki(repo_root=tmp_path, client=FakeClient(_bootstrap_payload()))
    assert out["status"] == "completed"
    wiki_root = tmp_path / ".wiki-keeper" / "wiki"
    assert (wiki_root / "concepts" / "Repository Overview.md").is_file()
    assert (wiki_root / "concepts" / "Monorepo Map.md").is_file()
    assert (wiki_root / "concepts" / "Build and Test.md").is_file()
    module_page = wiki_root / "modules" / "Web.md"
    frontmatter, body = parse_page_frontmatter(read_text(module_page))
    assert isinstance(frontmatter, dict)
    assert frontmatter["sources"] == ["apps/web/package.json"]
    assert page_is_schema_compliant(body)
    state_json = json.loads(read_text(tmp_path / ".wiki-keeper" / "state.json"))
    assert state_json["initialization"]["model"] == "gpt-5.4-mini"


def test_initialization_audit_contains_inventory_and_model_only(tmp_path, monkeypatch):
    _seed_monorepo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    out = initialize_wiki(repo_root=tmp_path, client=FakeClient(_bootstrap_payload()))
    text = read_text(tmp_path / out["audit_path"])
    assert "## Inventory Totals" in text
    assert "Model:" in text
    assert "Manager model:" not in text
    assert "Worker model:" not in text
    assert "Subagent count:" not in text
