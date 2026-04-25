from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest

from mcp_server.integrations.site_scaffold import GENERATED_SITE_CONFIG, init_site


def test_site_init_dry_run_lists_template_and_vercel_config(tmp_path: Path):
    out = init_site(tmp_path, dry_run=True)

    assert out["status"] == "dry_run"
    assert out["dry_run"] is True
    assert "site/package.json" in out["planned_files"]
    assert "site/lib/wiki.ts" in out["planned_files"]
    assert "vercel.json" in out["planned_files"]
    assert out["vercel"]["required_config"]["installCommand"] == "cd site && npm ci"
    assert not (tmp_path / "site").exists()
    assert not (tmp_path / "vercel.json").exists()


def test_site_init_writes_template_config_and_vercel_json(tmp_path: Path):
    out = init_site(tmp_path)

    assert out["status"] == "completed"
    assert (tmp_path / "site" / "package.json").is_file()
    assert (tmp_path / "site" / "lib" / "wiki.ts").is_file()
    config = (tmp_path / "site" / GENERATED_SITE_CONFIG).read_text(encoding="utf-8")
    assert 'export const wikiKeeperWikiDir = "../.wiki-keeper/wiki";' in config
    vercel = json.loads((tmp_path / "vercel.json").read_text(encoding="utf-8"))
    assert vercel["framework"] is None
    assert vercel["buildCommand"] == "cd site && npm run build"
    assert vercel["outputDirectory"] == "site/out"


def test_site_init_refuses_existing_site_without_force(tmp_path: Path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "existing.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        init_site(tmp_path)

    out = init_site(tmp_path, force=True)
    assert out["would_overwrite"] == []
    assert (site / "existing.txt").read_text(encoding="utf-8") == "keep me\n"
    assert (site / "package.json").is_file()


def test_site_init_reports_existing_vercel_json_without_overwriting(tmp_path: Path):
    (tmp_path / "vercel.json").write_text('{"buildCommand": "custom"}\n', encoding="utf-8")

    out = init_site(tmp_path)

    assert out["vercel"]["status"] == "manual_merge_required"
    assert out["vercel"]["required_config"]["outputDirectory"] == "site/out"
    assert json.loads((tmp_path / "vercel.json").read_text(encoding="utf-8")) == {"buildCommand": "custom"}


def test_site_init_supports_custom_site_dir(tmp_path: Path):
    out = init_site(tmp_path, site_dir="docs/wiki-site")

    assert out["site_dir"] == "docs/wiki-site"
    config = (tmp_path / "docs" / "wiki-site" / GENERATED_SITE_CONFIG).read_text(encoding="utf-8")
    assert 'export const wikiKeeperWikiDir = "../../.wiki-keeper/wiki";' in config
    vercel = json.loads((tmp_path / "vercel.json").read_text(encoding="utf-8"))
    assert vercel["installCommand"] == "cd docs/wiki-site && npm ci"
    assert vercel["outputDirectory"] == "docs/wiki-site/out"


def test_site_init_rejects_unsafe_site_dir(tmp_path: Path):
    for value in ("../site", ".wiki-keeper/site", "/tmp/site", "C:/site"):
        with pytest.raises(ValueError):
            init_site(tmp_path, site_dir=value)


def test_site_template_package_data_is_accessible():
    root = resources.files("mcp_server").joinpath("site_template")

    assert root.joinpath("package.json").is_file()
    assert root.joinpath("lib", "wiki.ts").is_file()
