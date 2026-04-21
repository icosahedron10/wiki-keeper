from __future__ import annotations

import os
from pathlib import Path

CATEGORIES = ("decisions", "modules", "concepts")


def repo_root() -> Path:
    env = os.environ.get("WIKI_KEEPER_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".wiki-keeper" / "schema.md").is_file() and (
            parent / ".wiki-keeper" / "wiki"
        ).is_dir():
            return parent
    raise RuntimeError(
        "Could not locate wiki-keeper root. Set WIKI_KEEPER_ROOT to the host "
        "repository that contains .wiki-keeper/schema.md."
    )


def corpus_root() -> Path:
    return repo_root() / ".wiki-keeper"


def wiki_dir() -> Path:
    return corpus_root() / "wiki"


def index_path() -> Path:
    return wiki_dir() / "index.md"


def log_path() -> Path:
    return wiki_dir() / "log.md"


def schema_path() -> Path:
    return corpus_root() / "schema.md"


def audits_dir() -> Path:
    return corpus_root() / "audits"


def roadmap_path() -> Path:
    return corpus_root() / "roadmap.md"


def state_path() -> Path:
    return corpus_root() / "state.json"


def safe_resolve(base: Path, relative: str) -> Path:
    """Resolve `relative` under `base`, rejecting paths that escape base."""
    candidate = (base / relative).resolve()
    base_resolved = base.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"Path {relative!r} escapes {base_resolved}") from exc
    return candidate
