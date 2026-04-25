from __future__ import annotations

import json
import os
import shlex
from importlib import resources
from pathlib import Path
from typing import Any

from .paths import safe_resolve
from .storage import atomic_write

GENERATED_SITE_CONFIG = "lib/generated-config.ts"
TEMPLATE_DIR = "site_template"
_TEMPLATE_SKIP_DIRS = {"node_modules", ".next", "out"}
_TEMPLATE_SKIP_FILES = {"tsconfig.tsbuildinfo"}


def init_site(
    repo: Path | str,
    *,
    site_dir: str = "site",
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Scaffold the read-only static wiki site into a host repository."""

    repo_root = Path(repo).resolve()
    normalized_site_dir = _normalize_site_dir(site_dir)
    site_root = safe_resolve(repo_root, normalized_site_dir)
    template_files = _load_template_files()
    template_targets = [
        f"{normalized_site_dir}/{rel_path}" for rel_path, _ in template_files
    ]
    generated_config_rel = f"{normalized_site_dir}/{GENERATED_SITE_CONFIG}"
    planned_files = sorted(set([*template_targets, generated_config_rel]))
    conflicts = _find_conflicts(repo_root, normalized_site_dir, planned_files)
    if conflicts and not force:
        raise FileExistsError(
            "site scaffold target is not empty; pass --force to overwrite "
            f"template files: {', '.join(conflicts[:12])}"
        )

    required_vercel_config = _vercel_config(normalized_site_dir)
    vercel_path = repo_root / "vercel.json"
    vercel_status = "manual_merge_required" if vercel_path.exists() else "create"

    result: dict[str, Any] = {
        "status": "dry_run" if dry_run else "completed",
        "repo_root": str(repo_root),
        "site_dir": normalized_site_dir,
        "site_root": str(site_root),
        "dry_run": dry_run,
        "force": force,
        "planned_files": planned_files
        + ([] if vercel_status == "manual_merge_required" else ["vercel.json"]),
        "would_overwrite": sorted(path for path in planned_files if (repo_root / path).exists()),
        "vercel": {
            "status": vercel_status,
            "path": "vercel.json",
            "required_config": required_vercel_config,
        },
    }
    if dry_run:
        return result

    for rel_path, content in template_files:
        atomic_write(site_root / rel_path, content)

    atomic_write(
        site_root / GENERATED_SITE_CONFIG,
        _generated_site_config(_relative_posix(site_root, repo_root / ".wiki-keeper" / "wiki")),
    )
    if vercel_status == "create":
        atomic_write(vercel_path, json.dumps(required_vercel_config, indent=2) + "\n")
    return result


def _normalize_site_dir(value: str) -> str:
    raw = value.replace("\\", "/").strip()
    if raw.startswith("/") or ":" in raw:
        raise ValueError("site_dir must be relative to the repository root")
    normalized = raw.strip("/")
    if not normalized or normalized in {".", ".."}:
        raise ValueError("site_dir must be a non-empty relative path")
    if normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise ValueError("site_dir cannot contain traversal segments")
    if normalized == ".wiki-keeper" or normalized.startswith(".wiki-keeper/"):
        raise ValueError("site_dir cannot be inside .wiki-keeper")
    return normalized


def _load_template_files() -> list[tuple[str, str]]:
    root = resources.files("mcp_server").joinpath(TEMPLATE_DIR)
    files: list[tuple[str, str]] = []
    for item in _walk_template(root):
        rel = item.relative_path
        files.append((rel, item.content))
    return sorted(files, key=lambda row: row[0])


class _TemplateFile:
    def __init__(self, relative_path: str, content: str) -> None:
        self.relative_path = relative_path
        self.content = content


def _walk_template(root: Any, prefix: str = "") -> list[_TemplateFile]:
    out: list[_TemplateFile] = []
    for child in root.iterdir():
        rel = f"{prefix}/{child.name}" if prefix else child.name
        if child.is_dir():
            if child.name in _TEMPLATE_SKIP_DIRS:
                continue
            out.extend(_walk_template(child, rel))
        elif child.is_file():
            if child.name in _TEMPLATE_SKIP_FILES:
                continue
            out.append(_TemplateFile(rel, child.read_text(encoding="utf-8")))
    return out


def _find_conflicts(repo_root: Path, site_dir: str, planned_files: list[str]) -> list[str]:
    site_root = repo_root / site_dir
    conflicts = [path for path in planned_files if (repo_root / path).exists()]
    if site_root.exists():
        extra = [
            str(path.relative_to(repo_root)).replace("\\", "/")
            for path in site_root.rglob("*")
            if path.is_file()
            and str(path.relative_to(repo_root)).replace("\\", "/") not in planned_files
        ]
        conflicts.extend(extra)
    return sorted(set(conflicts))


def _vercel_config(site_dir: str) -> dict[str, Any]:
    quoted_site_dir = shlex.quote(site_dir)
    return {
        "$schema": "https://openapi.vercel.sh/vercel.json",
        "framework": None,
        "installCommand": f"cd {quoted_site_dir} && npm ci",
        "buildCommand": f"cd {quoted_site_dir} && npm run build",
        "outputDirectory": f"{site_dir}/out",
    }


def _generated_site_config(wiki_dir: str) -> str:
    return (
        "// Generated by `wiki-keeper site init`. Keep this path relative to the site root.\n"
        f"export const wikiKeeperWikiDir = {json.dumps(wiki_dir)};\n"
    )


def _relative_posix(from_dir: Path, target: Path) -> str:
    return os.path.relpath(target.resolve(), from_dir.resolve()).replace("\\", "/")
