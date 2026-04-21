from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.init_bootstrap import (
    validate_manager_packet_plan,
    validate_synthesis_payload,
    validate_worker_report,
)
from mcp_server.init_corpus import detect_host_repo_root, initialize_wiki
from mcp_server.monorepo_inventory import bounded_slice_previews, collect_inventory
from mcp_server.pages import parse_page_frontmatter
from mcp_server.storage import read_text
from mcp_server.validate import page_is_schema_compliant


class FakeBootstrapLLM:
    def __init__(self, *, requested_subagents: int = 9):
        self.requested_subagents = requested_subagents
        self.worker_calls = 0

    def complete_json_schema(self, **kwargs):
        schema_name = kwargs["schema_name"]
        if schema_name == "init_packet_plan":
            return {
                "subagent_count": self.requested_subagents,
                "packets": [
                    {
                        "packet_id": "apps-1",
                        "focus": "apps",
                        "paths": ["apps/web/package.json"],
                    },
                    {
                        "packet_id": "packages-1",
                        "focus": "packages",
                        "paths": ["packages/core/pyproject.toml"],
                    },
                    {
                        "packet_id": "docs-1",
                        "focus": "docs",
                        "paths": ["docs/README.md"],
                    },
                ],
            }
        if schema_name == "init_worker_report":
            self.worker_calls += 1
            user_prompt = kwargs.get("user_prompt", "")
            marker = '"packet_id": "'
            start = user_prompt.find(marker)
            packet_id = "apps-1"
            if start >= 0:
                start += len(marker)
                end = user_prompt.find('"', start)
                if end > start:
                    packet_id = user_prompt[start:end]
            return {
                "packet_id": packet_id,
                "facts": [{"statement": "web app exists", "sources": ["apps/web/package.json"]}],
                "module_candidates": [
                    {
                        "name": "Web",
                        "paths": ["apps/web/package.json"],
                        "confidence": "medium",
                        "sources": ["apps/web/package.json"],
                    }
                ],
                "entrypoints": ["apps/web/package.json"],
                "dependencies": [{"statement": "depends on npm", "sources": ["apps/web/package.json"]}],
                "risks": [{"statement": "unknown test coverage", "sources": ["apps/web/package.json"]}],
                "open_questions": [{"statement": "missing deployment docs", "sources": ["apps/web/package.json"]}],
            }
        if schema_name == "init_synthesis":
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
        raise AssertionError(f"unexpected schema_name {schema_name}")


def _seed_monorepo(root: Path) -> None:
    (root / "apps" / "web").mkdir(parents=True)
    (root / "packages" / "core").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "apps" / "web" / "package.json").write_text('{"name":"web"}\n', encoding="utf-8")
    (root / "packages" / "core" / "pyproject.toml").write_text(
        "[project]\nname='core'\n",
        encoding="utf-8",
    )
    (root / "docs" / "README.md").write_text("# docs\n", encoding="utf-8")
    (root / "node_modules" / "leftpad").mkdir(parents=True)
    (root / "node_modules" / "leftpad" / "index.js").write_text("module.exports=1\n", encoding="utf-8")


def _symlink_or_skip(link_path: Path, target_path: Path) -> None:
    try:
        link_path.symlink_to(target_path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation not supported in this environment: {exc}")


def test_detect_host_repo_root_explicit(tmp_path):
    explicit = tmp_path / "host"
    explicit.mkdir()
    out = detect_host_repo_root(explicit_repo=explicit, cwd=tmp_path)
    assert out == explicit.resolve()


def test_detect_host_repo_root_superproject(tmp_path):
    cwd = tmp_path / "submodule"
    cwd.mkdir()

    def runner(command, _cwd):  # noqa: ANN001
        assert command[-1] == "--show-superproject-working-tree"
        return str(tmp_path / "super")

    out = detect_host_repo_root(explicit_repo=None, cwd=cwd, git_runner=runner)
    assert out == (tmp_path / "super").resolve()


def test_detect_host_repo_root_falls_back_to_cwd(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    out = detect_host_repo_root(
        explicit_repo=None, cwd=cwd, git_runner=lambda _command, _cwd: None
    )
    assert out == cwd.resolve()


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
    link_path = repo_root / "apps" / "web" / "outside-link.txt"
    _symlink_or_skip(link_path, outside)

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
    link_path = repo_root / "apps" / "web" / "outside-link.txt"
    _symlink_or_skip(link_path, outside)

    out = bounded_slice_previews(repo_root, ["apps/web/outside-link.txt"])
    assert out == []


def test_manager_packet_plan_respects_max_subagents():
    packet_plan, count = validate_manager_packet_plan(
        {
            "subagent_count": 100,
            "packets": [
                {"packet_id": "p1", "focus": "a", "paths": ["apps/web/package.json"]},
                {"packet_id": "p2", "focus": "b", "paths": ["packages/core/pyproject.toml"]},
            ],
        },
        available_paths={"apps/web/package.json", "packages/core/pyproject.toml"},
        max_subagents=1,
    )
    assert count == 1
    assert len(packet_plan) == 1


def test_manager_packet_plan_subagent_count_matches_validated_packets():
    packet_plan, count = validate_manager_packet_plan(
        {
            "subagent_count": 9,
            "packets": [
                {"packet_id": "p1", "focus": "a", "paths": ["apps/web/package.json"]},
                {"packet_id": "p2", "focus": "b", "paths": ["packages/core/pyproject.toml"]},
            ],
        },
        available_paths={"apps/web/package.json", "packages/core/pyproject.toml"},
        max_subagents=10,
    )
    assert len(packet_plan) == 2
    assert count == 2


def test_worker_validation_rejects_unknown_source():
    with pytest.raises(ValueError):
        validate_worker_report(
            {
                "packet_id": "apps-1",
                "facts": [{"statement": "x", "sources": ["unknown/file.py"]}],
                "module_candidates": [],
                "entrypoints": [],
                "dependencies": [],
                "risks": [],
                "open_questions": [],
            },
            available_paths={"apps/web/package.json"},
        )


def test_synthesis_validation_rejects_speculative_page_sources(tmp_path):
    _seed_monorepo(tmp_path)
    inventory = collect_inventory(tmp_path)
    with pytest.raises(ValueError):
        validate_synthesis_payload(
            {
                "pages": [
                    {
                        "category": "concepts",
                        "title": "Repository Overview",
                        "summary": "x",
                        "key_facts": ["x"],
                        "details": ["x"],
                        "relationships": ["x"],
                        "sources": ["imaginary/path.py"],
                        "open_questions": ["x"],
                        "confidence": "medium",
                        "frontmatter_sources": [],
                    }
                ],
                "roadmap_entries": [],
                "open_questions": [],
                "truncated_areas": [],
            },
            available_paths=set(inventory.discovered_paths),
            inventory=inventory,
        )


def test_synthesis_validation_rejects_path_traversal_title(tmp_path):
    _seed_monorepo(tmp_path)
    inventory = collect_inventory(tmp_path)
    with pytest.raises(ValueError):
        validate_synthesis_payload(
            {
                "pages": [
                    {
                        "category": "modules",
                        "title": "../Escape",
                        "summary": "x",
                        "key_facts": ["x"],
                        "details": ["x"],
                        "relationships": ["x"],
                        "sources": ["apps/web/package.json"],
                        "open_questions": ["x"],
                        "confidence": "low",
                        "frontmatter_sources": ["apps/web/package.json"],
                    }
                ],
                "roadmap_entries": [],
                "open_questions": [],
                "truncated_areas": [],
            },
            available_paths=set(inventory.discovered_paths),
            inventory=inventory,
        )


def test_initialize_wiki_requires_api_key_when_online(tmp_path, monkeypatch):
    _seed_monorepo(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        initialize_wiki(repo_root=tmp_path, offline=False)


def test_online_bootstrap_caps_subagents(tmp_path, monkeypatch):
    _seed_monorepo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake = FakeBootstrapLLM(requested_subagents=12)
    out = initialize_wiki(
        repo_root=tmp_path,
        offline=False,
        llm_client=fake,
        max_subagents=2,
    )
    assert out["subagent_count"] == 2
    assert fake.worker_calls == 2


def test_online_bootstrap_reports_executed_subagents(tmp_path, monkeypatch):
    _seed_monorepo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake = FakeBootstrapLLM(requested_subagents=12)
    out = initialize_wiki(
        repo_root=tmp_path,
        offline=False,
        llm_client=fake,
        max_subagents=12,
    )
    assert fake.worker_calls == 3
    assert out["subagent_count"] == fake.worker_calls


def test_initialize_wiki_dry_run_writes_nothing(tmp_path):
    _seed_monorepo(tmp_path)
    out = initialize_wiki(repo_root=tmp_path, offline=True, dry_run=True)
    assert out["status"] == "dry_run"
    assert not (tmp_path / ".wiki-keeper").exists()


def test_initialize_wiki_idempotent_and_refresh_audit(tmp_path):
    _seed_monorepo(tmp_path)
    first = initialize_wiki(repo_root=tmp_path, offline=True)
    assert first["status"] == "completed"
    first_audit = first["audit_path"]
    second = initialize_wiki(repo_root=tmp_path, offline=True)
    assert second["status"] == "already_completed"
    refreshed = initialize_wiki(repo_root=tmp_path, offline=True, refresh_bootstrap=True)
    assert refreshed["status"] == "refreshed"
    assert refreshed["audit_path"] != first_audit


def test_offline_initialization_generates_expected_pages_and_module_frontmatter(tmp_path):
    _seed_monorepo(tmp_path)
    out = initialize_wiki(repo_root=tmp_path, offline=True)
    assert out["status"] == "completed"
    wiki_root = tmp_path / ".wiki-keeper" / "wiki"
    assert (wiki_root / "concepts" / "Repository Overview.md").is_file()
    assert (wiki_root / "concepts" / "Monorepo Map.md").is_file()
    assert (wiki_root / "concepts" / "Build and Test.md").is_file()
    module_pages = sorted((wiki_root / "modules").glob("*.md"))
    assert module_pages
    for page in module_pages:
        content = read_text(page)
        frontmatter, body = parse_page_frontmatter(content)
        assert isinstance(frontmatter, dict)
        assert isinstance(frontmatter.get("sources"), list)
        assert frontmatter["sources"]
        assert page_is_schema_compliant(body)
        if "> stub" not in body:
            assert "## Sources" in body
    state_json = json.loads(read_text(tmp_path / ".wiki-keeper" / "state.json"))
    init_state = state_json["initialization"]
    assert init_state["manager_model"] == "offline"
    assert init_state["worker_model"] == "offline"


def test_initialization_audit_contains_inventory_and_models(tmp_path):
    _seed_monorepo(tmp_path)
    out = initialize_wiki(repo_root=tmp_path, offline=True)
    audit = tmp_path / out["audit_path"]
    text = read_text(audit)
    assert "## Inventory Totals" in text
    assert "Subagent count:" in text
    assert "Manager model:" in text
    assert "Worker model:" in text
