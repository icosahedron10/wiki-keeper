from __future__ import annotations

import json
import os
from pathlib import Path

from . import index as wiki_index
from .paths import safe_resolve
from .storage import atomic_write


DEFAULT_SCHEMA = """# Wiki Schema

This file is the operating manual for the wiki. It is addressed to the agent that maintains it.

## Page format

Every page under `.wiki-keeper/wiki/` is markdown with these sections:

- `## Summary`
- `## Key Facts`
- `## Details`
- `## Relationships`
- `## Open Questions`

## Frontmatter (optional)

Articles may include optional YAML frontmatter:

```yaml
---
id: auth-overview
title: Authentication Overview
sources:
  - services/auth/**
  - packages/session/**
---
```

Frontmatter `sources` are host-repo globs used by nightly review.

## Invariants

1. All writes stay under `.wiki-keeper/`.
2. Host-repo files are read-only.
3. `update_knowledge` is the only article write path.
4. Every mutation appends one line to `.wiki-keeper/wiki/log.md`.
"""


DEFAULT_ROADMAP = """# Wiki Review Roadmap

# One article id per line, ordered by priority.
"""


DEFAULT_STATE = {
    "cursor": {"article_id": None, "index": -1},
    "last_run": None,
    "history": [],
}


SAMPLE_ARTICLE = """# Repository Overview

## Summary
Starter page for this wiki corpus.

## Key Facts
- This wiki was initialized by wiki-keeper.

## Details
Populate this page with repository-specific architecture knowledge.

## Relationships
- Related to [[Repository Overview]]

## Open Questions
- None yet.
"""


def init_corpus(repo: Path) -> dict:
    repo_root = repo.resolve()
    corpus = repo_root / ".wiki-keeper"
    created: list[str] = []

    for rel_dir in [
        ".wiki-keeper",
        ".wiki-keeper/wiki",
        ".wiki-keeper/wiki/decisions",
        ".wiki-keeper/wiki/modules",
        ".wiki-keeper/wiki/concepts",
        ".wiki-keeper/audits",
    ]:
        path = repo_root / rel_dir
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(rel_dir)

    _write_if_missing(repo_root, ".wiki-keeper/schema.md", DEFAULT_SCHEMA, created)
    _write_if_missing(repo_root, ".wiki-keeper/roadmap.md", DEFAULT_ROADMAP, created)
    _write_if_missing(
        repo_root,
        ".wiki-keeper/state.json",
        json.dumps(DEFAULT_STATE, indent=2) + "\n",
        created,
    )
    _write_if_missing(repo_root, ".wiki-keeper/wiki/log.md", _initial_log(), created)
    _write_if_missing(
        repo_root,
        ".wiki-keeper/wiki/concepts/Repository Overview.md",
        SAMPLE_ARTICLE,
        created,
    )

    # Rebuild index using the target repository as WIKI_KEEPER_ROOT.
    previous = os.environ.get("WIKI_KEEPER_ROOT")
    os.environ["WIKI_KEEPER_ROOT"] = str(repo_root)
    try:
        wiki_index.rebuild()
    finally:
        if previous is None:
            os.environ.pop("WIKI_KEEPER_ROOT", None)
        else:
            os.environ["WIKI_KEEPER_ROOT"] = previous

    return {
        "initialized": True,
        "repo_root": str(repo_root),
        "corpus_root": str(corpus),
        "created": created,
    }


def _initial_log() -> str:
    return (
        "# Wiki Log\n\n"
        "Append-only record of every mutation. One line per event.\n\n"
        "Format: `<iso-timestamp> <tool> <action> <target> [note]`\n\n"
        "---\n"
    )


def _write_if_missing(repo_root: Path, rel: str, content: str, created: list[str]) -> None:
    path = safe_resolve(repo_root, rel)
    if path.exists():
        return
    atomic_write(path, content if content.endswith("\n") else content + "\n")
    created.append(rel)
