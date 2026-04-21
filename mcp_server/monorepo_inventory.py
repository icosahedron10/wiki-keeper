from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_OVERSIZED_BYTES = 512 * 1024
MAX_PREVIEW_CHARS = 4000
MAX_WORKER_SLICE_CHARS = 24000

_EXCLUDED_DIRS = {
    ".git",
    ".wiki-keeper",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "third_party",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    "dist",
    "build",
    "out",
    "target",
    ".next",
    ".nuxt",
    ".gradle",
    ".terraform",
    "coverage",
}

_BINARY_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".jar",
    ".war",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".class",
    ".pyc",
    ".pyo",
    ".pyd",
    ".o",
    ".a",
    ".obj",
    ".lib",
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".wav",
}

_BUILD_MANIFESTS = {
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "gradle.properties",
    "Gemfile",
    "Gemfile.lock",
    "composer.json",
    "composer.lock",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
    "justfile",
    "WORKSPACE",
    "WORKSPACE.bazel",
    "BUILD",
    "BUILD.bazel",
}


@dataclass(frozen=True)
class InventoryPreview:
    path: str
    preview: str
    kind: str


@dataclass
class MonorepoInventory:
    repo_root: str
    discovered_paths: list[str]
    classifications: dict[str, list[str]]
    previews: list[InventoryPreview]
    oversized_paths: list[str] = field(default_factory=list)
    binary_paths: list[str] = field(default_factory=list)
    traversal_source: str = "filesystem"
    inventory_hash: str = ""

    @property
    def totals(self) -> dict[str, int]:
        return {
            "discovered_paths": len(self.discovered_paths),
            "preview_paths": len(self.previews),
            "oversized_paths": len(self.oversized_paths),
            "binary_paths": len(self.binary_paths),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "traversal_source": self.traversal_source,
            "inventory_hash": self.inventory_hash,
            "totals": self.totals,
            "discovered_paths": list(self.discovered_paths),
            "classifications": {k: list(v) for k, v in self.classifications.items()},
            "previews": [
                {"path": item.path, "kind": item.kind, "preview": item.preview}
                for item in self.previews
            ],
            "oversized_paths": list(self.oversized_paths),
            "binary_paths": list(self.binary_paths),
        }


def collect_inventory(repo_root: Path, *, tool_checkout: Path | None = None) -> MonorepoInventory:
    root = repo_root.resolve()
    tool_prefix_parts = _tool_prefix_parts(root, tool_checkout)
    git_paths = _discover_with_git(root, tool_prefix_parts=tool_prefix_parts)
    if git_paths is None or not git_paths[0]:
        discovered, source = _discover_with_walk(root, tool_prefix_parts=tool_prefix_parts)
    else:
        discovered, source = git_paths

    discovered = sorted(set(discovered))
    classifications = _classify(discovered)
    previews: list[InventoryPreview] = []
    oversized: list[str] = []
    binary: list[str] = []

    preview_budget = 0
    for rel in discovered:
        abs_path = _resolve_within_root(root, rel)
        if abs_path is None:
            continue
        if not abs_path.is_file():
            continue
        try:
            size = abs_path.stat().st_size
        except OSError:
            continue
        if size > MAX_OVERSIZED_BYTES:
            oversized.append(rel)
            continue
        if _looks_binary(abs_path):
            binary.append(rel)
            continue
        preview_kind = _preview_kind(rel)
        if preview_kind is None:
            continue
        text = _read_preview(abs_path)
        if not text.strip():
            continue
        preview_budget += len(text)
        if preview_budget > 350_000:
            break
        previews.append(InventoryPreview(path=rel, preview=text, kind=preview_kind))

    inventory_hash = _hash_inventory(discovered, oversized, binary)
    return MonorepoInventory(
        repo_root=str(root),
        discovered_paths=discovered,
        classifications=classifications,
        previews=previews,
        oversized_paths=sorted(oversized),
        binary_paths=sorted(binary),
        traversal_source=source,
        inventory_hash=inventory_hash,
    )


def bounded_slice_previews(
    repo_root: Path,
    rel_paths: list[str],
    *,
    max_total_chars: int = MAX_WORKER_SLICE_CHARS,
) -> list[InventoryPreview]:
    root = repo_root.resolve()
    out: list[InventoryPreview] = []
    total = 0
    for rel in rel_paths:
        abs_path = _resolve_within_root(root, rel)
        if abs_path is None:
            continue
        if not abs_path.is_file():
            continue
        try:
            if abs_path.stat().st_size > MAX_OVERSIZED_BYTES:
                continue
        except OSError:
            continue
        if _looks_binary(abs_path):
            continue
        text = _read_preview(abs_path)
        if not text:
            continue
        total += len(text)
        if total > max_total_chars:
            break
        out.append(InventoryPreview(path=rel, preview=text, kind=_preview_kind(rel) or "file"))
    return out


def _tool_prefix_parts(repo_root: Path, tool_checkout: Path | None) -> tuple[str, ...] | None:
    if tool_checkout is None:
        return None
    try:
        rel = tool_checkout.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    parts = tuple(part for part in rel.parts if part)
    return parts or None


def _resolve_within_root(root: Path, rel: str) -> Path | None:
    try:
        resolved = (root / rel).resolve()
    except OSError:
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _discover_with_git(
    repo_root: Path, *, tool_prefix_parts: tuple[str, ...] | None
) -> tuple[list[str], str] | None:
    command = ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    try:
        proc = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.decode("utf-8", errors="replace")
    entries = [item for item in raw.split("\x00") if item]
    filtered = [
        _normalize_rel(path)
        for path in entries
        if not _is_excluded(_normalize_rel(path), tool_prefix_parts)
    ]
    return filtered, "git"


def _discover_with_walk(
    repo_root: Path, *, tool_prefix_parts: tuple[str, ...] | None
) -> tuple[list[str], str]:
    collected: list[str] = []
    for current, dirs, files in os.walk(repo_root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(repo_root)
        rel_parts = tuple(part for part in rel_dir.parts if part not in {".", ""})
        dirs[:] = [
            name
            for name in dirs
            if not _is_excluded(_normalize_rel("/".join((*rel_parts, name))), tool_prefix_parts)
        ]
        for name in files:
            rel = _normalize_rel("/".join((*rel_parts, name)))
            if _is_excluded(rel, tool_prefix_parts):
                continue
            collected.append(rel)
    return collected, "filesystem"


def _normalize_rel(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized == ".":
        return ""
    if normalized.startswith("./"):
        return normalized[2:]
    return normalized


def _is_excluded(rel_path: str, tool_prefix_parts: tuple[str, ...] | None) -> bool:
    parts = tuple(part for part in rel_path.split("/") if part)
    if not parts:
        return True
    if tool_prefix_parts and len(parts) >= len(tool_prefix_parts):
        if parts[: len(tool_prefix_parts)] == tool_prefix_parts:
            return True
    for part in parts[:-1]:
        if part in _EXCLUDED_DIRS:
            return True
    return parts[0] in {".wiki-keeper", ".git"}


def _classify(paths: list[str]) -> dict[str, list[str]]:
    package_roots: set[str] = set()
    apps_services: set[str] = set()
    libraries: set[str] = set()
    tests: set[str] = set()
    docs: set[str] = set()
    ci: set[str] = set()
    infra: set[str] = set()
    entrypoints: set[str] = set()
    manifests: set[str] = set()
    for rel in paths:
        p = Path(rel)
        parts = p.parts
        name = p.name
        lower = rel.lower()
        if name in _BUILD_MANIFESTS:
            manifests.add(rel)
            package_roots.add(str(p.parent).replace("\\", "/") if str(p.parent) != "." else ".")
        if parts and parts[0] in {"apps", "services"} and len(parts) >= 2:
            apps_services.add("/".join(parts[:2]))
        if parts and parts[0] in {"packages", "libs", "lib"} and len(parts) >= 2:
            libraries.add("/".join(parts[:2]))
        if any(part.lower() in {"test", "tests", "__tests__", "spec", "specs"} for part in parts):
            tests.add(rel)
        if parts and (
            parts[0].lower() == "docs"
            or name.lower().startswith("readme")
            or name.lower().endswith(".md")
        ):
            docs.add(rel)
        if lower.startswith(".github/workflows/") or lower.startswith(".circleci/"):
            ci.add(rel)
        if any(part.lower() in {"infra", "terraform", "k8s", "kubernetes", "helm"} for part in parts):
            infra.add(rel)
        if _is_entrypoint(p):
            entrypoints.add(rel)
    return {
        "package_roots": sorted(package_roots),
        "apps_services": sorted(apps_services),
        "libraries": sorted(libraries),
        "tests": sorted(tests),
        "docs": sorted(docs),
        "ci": sorted(ci),
        "infra": sorted(infra),
        "entrypoints": sorted(entrypoints),
        "build_manifests": sorted(manifests),
    }


def _is_entrypoint(path: Path) -> bool:
    name = path.name.lower()
    if name in {"main.py", "__main__.py", "app.py", "server.py", "main.go", "main.rs"}:
        return True
    if name in {"index.js", "index.ts"} and any(part in {"src", "app"} for part in path.parts):
        return True
    if len(path.parts) >= 3 and path.parts[0] == "cmd" and path.name == "main.go":
        return True
    return False


def _read_preview(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= MAX_PREVIEW_CHARS:
        return text
    return text[:MAX_PREVIEW_CHARS] + "\n...[truncated preview]...\n"


def _looks_binary(path: Path) -> bool:
    if path.suffix.lower() in _BINARY_EXTS:
        return True
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError:
        return True
    if b"\x00" in head:
        return True
    return False


def _preview_kind(rel: str) -> str | None:
    name = Path(rel).name
    if name in _BUILD_MANIFESTS:
        return "manifest"
    if _is_entrypoint(Path(rel)):
        return "entrypoint"
    if rel.lower().endswith(".md"):
        return "docs"
    return "file"


def _hash_inventory(paths: list[str], oversized: list[str], binary: list[str]) -> str:
    h = hashlib.sha256()
    for seq in (paths, oversized, binary):
        for item in seq:
            h.update(item.encode("utf-8", errors="replace"))
            h.update(b"\n")
    return h.hexdigest()
