from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path

from ..core.paths import safe_resolve


@dataclass
class SourceFile:
    rel_path: str
    content: str
    size_bytes: int


@dataclass
class ScanResult:
    files: list[SourceFile]
    truncated: bool
    total_bytes: int
    errors: list[str]


def _ensure_relative_glob(pattern: str) -> str | None:
    if not pattern.strip():
        return "Glob pattern cannot be empty"
    if Path(pattern).is_absolute():
        return f"Glob pattern {pattern!r} must be relative to repo root"
    if "\\" in pattern:
        return f"Glob pattern {pattern!r} must use '/' separators"
    if any(part == ".." for part in Path(pattern).parts):
        return f"Glob pattern {pattern!r} must not include '..'"
    return None


def resolve_source_globs(
    *,
    repo_root: Path,
    patterns: list[str],
    max_files: int = 200,
    max_bytes: int = 1_000_000,
) -> ScanResult:
    root = repo_root.resolve()
    files: list[SourceFile] = []
    errors: list[str] = []
    seen: set[str] = set()
    total_bytes = 0
    truncated = False

    for pattern in patterns:
        err = _ensure_relative_glob(pattern)
        if err:
            errors.append(err)
            continue

        matches = sorted(glob.glob(str(root / pattern), recursive=True))
        for match in matches:
            path = Path(match)
            if not path.is_file():
                continue
            try:
                rel = path.resolve().relative_to(root)
            except ValueError:
                errors.append(f"Resolved path escapes repo root: {path}")
                continue

            # Keep safe_resolve as the single path-boundary guard.
            safe = safe_resolve(root, str(rel))
            rel_posix = str(rel).replace("\\", "/")
            if rel_posix in seen:
                continue

            raw = safe.read_bytes()
            if len(files) >= max_files:
                truncated = True
                break
            if total_bytes + len(raw) > max_bytes:
                truncated = True
                break

            seen.add(rel_posix)
            total_bytes += len(raw)
            files.append(
                SourceFile(
                    rel_path=rel_posix,
                    content=raw.decode("utf-8", errors="replace"),
                    size_bytes=len(raw),
                )
            )
        if truncated:
            break
    return ScanResult(files=files, truncated=truncated, total_bytes=total_bytes, errors=errors)
