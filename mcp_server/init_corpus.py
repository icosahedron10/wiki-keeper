from __future__ import annotations

import contextlib
import json
import os
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from . import git_delta, index as wiki_index
from . import state as state_mod
from . import wikilog
from .init_bootstrap import (
    BootstrapResult,
    InitModelConfig,
    deterministic_bootstrap_plan,
    run_bootstrap,
)
from .llm import LLMClient
from .monorepo_inventory import MonorepoInventory, collect_inventory
from .paths import CATEGORIES, SOURCE_FOLDERS, safe_resolve
from .storage import atomic_write, read_text

DEFAULT_SCHEMA = """# Wiki Schema

This file is the operating manual for the wiki. It is addressed to the agent that maintains it.

## Page format

Every page under `.wiki-keeper/wiki/` is markdown with these sections:

- `## Summary`
- `## Key Facts`
- `## Details`
- `## Relationships`
- `## Sources`
- `## Open Questions`

Pages without at least one list item under `## Sources` must be marked as stubs with `> stub` directly below the H1.

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

Frontmatter `sources` are host-repo globs used by commit-driven nightly review.
Body `## Sources` entries are evidence files under `.wiki-keeper/sources/` or host repo inventory paths.

## Invariants

1. All writes stay under `.wiki-keeper/`.
2. Host-repo files are read-only.
3. `update_knowledge` is the only article write path after initialization.
4. Every mutation appends one line to `.wiki-keeper/wiki/log.md`.

## Nightly review

Nightly runs compare `git.last_processed_commit..HEAD`, map changed paths to
article frontmatter `sources`, and write audit-only notes when no article maps.
Manual source ingestion is deferred until after the V1 commit-driven workflow.
"""

DEFAULT_ROADMAP = """# Wiki Review Roadmap

# One article id per line, ordered by priority.
"""

_MAX_AUDIT_COLLISION_ATTEMPTS = 10_000


def init_corpus(
    repo: Path,
    *,
    offline: bool = True,
    refresh_bootstrap: bool = False,
    max_subagents: int = 12,
    dry_run: bool = False,
    since: str | None = None,
    llm_client: LLMClient | None = None,
    model_config: InitModelConfig | None = None,
) -> dict[str, Any]:
    return initialize_wiki(
        repo_root=repo,
        offline=offline,
        refresh_bootstrap=refresh_bootstrap,
        max_subagents=max_subagents,
        dry_run=dry_run,
        since=since,
        llm_client=llm_client,
        model_config=model_config,
    )


def initialize_wiki(
    *,
    repo_root: Path | str | None = None,
    offline: bool = False,
    refresh_bootstrap: bool = False,
    max_subagents: int = 12,
    dry_run: bool = False,
    since: str | None = None,
    llm_client: LLMClient | None = None,
    model_config: InitModelConfig | None = None,
    cwd: Path | None = None,
    git_runner: Callable[[list[str], Path], str | None] | None = None,
) -> dict[str, Any]:
    resolved_root = detect_host_repo_root(explicit_repo=repo_root, cwd=cwd, git_runner=git_runner)
    model_config = model_config or InitModelConfig.from_env()
    scaffold = _plan_or_apply_scaffold(resolved_root, dry_run=dry_run)
    git_baseline = _detect_git_baseline(resolved_root, since=since)
    inventory = collect_inventory(
        resolved_root, tool_checkout=Path(__file__).resolve().parents[1]
    )

    current_state = _load_state(resolved_root)
    init_state = current_state.get("initialization", {})
    already_completed = str(init_state.get("status", "")).strip().lower() == "completed"

    if already_completed and not refresh_bootstrap and not dry_run:
        return {
            "initialized": True,
            "status": "already_completed",
            "repo_root": str(resolved_root),
            "corpus_root": str(resolved_root / ".wiki-keeper"),
            "inventory_hash": inventory.inventory_hash,
            "scaffold": scaffold,
            "created_pages": [],
            "skipped_pages": [],
            "subagent_count": int(init_state.get("subagent_count", 0) or 0),
            "manager_model": init_state.get("manager_model"),
            "worker_model": init_state.get("worker_model"),
            "git": current_state.get("git", {}),
            "dry_run": False,
        }

    if offline:
        bootstrap = deterministic_bootstrap_plan(inventory)
    else:
        _require_init_api_key()
        llm = llm_client or LLMClient()
        bootstrap = run_bootstrap(
            llm=llm,
            repo_root=resolved_root,
            inventory=inventory,
            max_subagents=max_subagents,
            model_config=model_config,
        )

    if dry_run:
        return _dry_run_payload(
            root=resolved_root,
            inventory=inventory,
            scaffold=scaffold,
            bootstrap=bootstrap,
            offline=offline,
            refresh_bootstrap=refresh_bootstrap,
            git_baseline=git_baseline,
        )

    write_result = _apply_bootstrap(
        resolved_root,
        bootstrap=bootstrap,
        inventory=inventory,
        refresh_bootstrap=refresh_bootstrap,
        git_baseline=git_baseline,
    )

    return {
        "initialized": True,
        "status": write_result["status"],
        "repo_root": str(resolved_root),
        "corpus_root": str(resolved_root / ".wiki-keeper"),
        "inventory_hash": inventory.inventory_hash,
        "subagent_count": bootstrap.subagent_count,
        "manager_model": bootstrap.manager_model,
        "worker_model": bootstrap.worker_model,
        "created_pages": write_result["created_pages"],
        "skipped_pages": write_result["skipped_pages"],
        "audit_path": write_result["audit_path"],
        "roadmap_entries": write_result["roadmap_entries"],
        "open_questions": bootstrap.open_questions,
        "truncated_areas": bootstrap.truncated_areas,
        "scaffold": scaffold,
        "git": write_result["git"],
        "dry_run": False,
    }


def detect_host_repo_root(
    *,
    explicit_repo: Path | str | None,
    cwd: Path | None = None,
    git_runner: Callable[[list[str], Path], str | None] | None = None,
) -> Path:
    if explicit_repo is not None:
        return Path(explicit_repo).resolve()
    current = (cwd or Path.cwd()).resolve()
    runner = git_runner or _run_git_capture
    superproject = runner(["git", "rev-parse", "--show-superproject-working-tree"], current)
    if superproject:
        value = superproject.strip()
        if value:
            return Path(value).resolve()
    return current


def _run_git_capture(command: list[str], cwd: Path) -> str | None:
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def _detect_git_baseline(repo_root: Path, *, since: str | None) -> dict[str, str | None]:
    commit = since.strip() if isinstance(since, str) and since.strip() else None
    if commit is None:
        commit = git_delta.current_head(repo_root)
    return {
        "commit": commit,
        "default_branch": git_delta.default_branch(repo_root),
    }


def _dry_run_payload(
    *,
    root: Path,
    inventory: MonorepoInventory,
    scaffold: dict[str, Any],
    bootstrap: BootstrapResult,
    offline: bool,
    refresh_bootstrap: bool,
    git_baseline: dict[str, str | None],
) -> dict[str, Any]:
    return {
        "initialized": False,
        "status": "dry_run",
        "repo_root": str(root),
        "corpus_root": str(root / ".wiki-keeper"),
        "offline": offline,
        "refresh_bootstrap": refresh_bootstrap,
        "git": git_baseline,
        "inventory_hash": inventory.inventory_hash,
        "inventory_totals": inventory.totals,
        "scaffold": scaffold,
        "planned_pages": [page.rel_path for page in bootstrap.pages],
        "planned_roadmap_entries": bootstrap.roadmap_entries,
        "subagent_count": bootstrap.subagent_count,
        "manager_model": bootstrap.manager_model,
        "worker_model": bootstrap.worker_model,
        "open_questions": bootstrap.open_questions,
        "truncated_areas": bootstrap.truncated_areas,
        "dry_run": True,
    }


def _plan_or_apply_scaffold(repo_root: Path, *, dry_run: bool) -> dict[str, Any]:
    created: list[str] = []
    planned: list[str] = []
    dirs = [
        ".wiki-keeper",
        ".wiki-keeper/wiki",
        ".wiki-keeper/wiki/decisions",
        ".wiki-keeper/wiki/modules",
        ".wiki-keeper/wiki/concepts",
        ".wiki-keeper/sources",
        ".wiki-keeper/audits",
    ]
    dirs.extend(f".wiki-keeper/sources/{folder}" for folder in SOURCE_FOLDERS)
    files = {
        ".wiki-keeper/schema.md": DEFAULT_SCHEMA,
        ".wiki-keeper/roadmap.md": DEFAULT_ROADMAP,
        ".wiki-keeper/state.json": json.dumps(state_mod.DEFAULT_STATE, indent=2),
        ".wiki-keeper/wiki/index.md": "# Wiki Index\n",
        ".wiki-keeper/wiki/log.md": _initial_log(),
    }

    for rel in dirs:
        planned.append(rel)
        path = repo_root / rel
        if dry_run:
            continue
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(rel)
    for rel, content in files.items():
        planned.append(rel)
        if dry_run:
            continue
        _write_if_missing(repo_root, rel, content, created)
    if not dry_run:
        _ensure_state_has_initialization(repo_root)
    return {"created": sorted(created), "planned": sorted(set(planned))}


def _apply_bootstrap(
    repo_root: Path,
    *,
    bootstrap: BootstrapResult,
    inventory: MonorepoInventory,
    refresh_bootstrap: bool,
    git_baseline: dict[str, str | None],
) -> dict[str, Any]:
    corpus = repo_root / ".wiki-keeper"
    existing_entries = _read_roadmap_entries(corpus / "roadmap.md")
    writes: dict[Path, str] = {}
    created_pages: list[str] = []
    skipped_pages: list[str] = []
    for page in bootstrap.pages:
        path = safe_resolve(corpus, f"wiki/{page.category}/{page.title}.md")
        rel = str(path.relative_to(repo_root)).replace("\\", "/")
        if path.exists():
            skipped_pages.append(rel)
            continue
        writes[path] = page.content if page.content.endswith("\n") else page.content + "\n"
        created_pages.append(rel)

    merged_roadmap = _merge_roadmap(existing_entries, bootstrap.roadmap_entries)
    writes[safe_resolve(corpus, "roadmap.md")] = _render_roadmap(merged_roadmap)

    updated_state = _load_state(repo_root)
    updated_state["initialization"] = {
        "completed_at": _now_iso(),
        "inventory_hash": inventory.inventory_hash,
        "manager_model": bootstrap.manager_model,
        "worker_model": bootstrap.worker_model,
        "subagent_count": int(bootstrap.subagent_count),
        "status": "completed",
    }
    updated_state = state_mod.set_git_baseline(
        updated_state,
        commit=git_baseline.get("commit"),
        default_branch=git_baseline.get("default_branch"),
    )
    writes[safe_resolve(corpus, "state.json")] = json.dumps(updated_state, indent=2) + "\n"

    audit_path = _next_init_audit_path(repo_root)
    audit_rel = str(audit_path.relative_to(repo_root)).replace("\\", "/")
    writes[audit_path] = _render_initialization_audit(
        bootstrap=bootstrap,
        inventory=inventory,
        created_pages=created_pages,
        skipped_pages=skipped_pages,
        refresh_bootstrap=refresh_bootstrap,
    )

    _apply_transactional_writes(corpus=corpus, writes=writes)
    with _repo_env(repo_root):
        wiki_index.rebuild()
        wikilog.append(
            "initialize_wiki",
            "refresh" if refresh_bootstrap else "bootstrap",
            ".wiki-keeper/wiki",
            f"pages_created={len(created_pages)} pages_skipped={len(skipped_pages)}",
        )

    return {
        "status": "refreshed" if refresh_bootstrap else "completed",
        "created_pages": sorted(created_pages),
        "skipped_pages": sorted(skipped_pages),
        "audit_path": audit_rel,
        "roadmap_entries": merged_roadmap,
        "git": updated_state.get("git", {}),
    }


def _apply_transactional_writes(*, corpus: Path, writes: dict[Path, str]) -> None:
    backups: dict[Path, str] = {}
    created: list[Path] = []
    for path in writes:
        try:
            path.resolve().relative_to(corpus.resolve())
        except ValueError as exc:
            raise ValueError(f"Write path escapes corpus root: {path}") from exc
    try:
        for path, content in writes.items():
            if path.exists():
                backups[path] = read_text(path)
            else:
                created.append(path)
            atomic_write(path, content)
    except Exception:
        for path, prior in backups.items():
            atomic_write(path, prior if prior.endswith("\n") else prior + "\n")
        for path in created:
            if path.exists():
                path.unlink()
        raise


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


def _load_state(repo_root: Path) -> dict[str, Any]:
    path = repo_root / ".wiki-keeper" / "state.json"
    if not path.exists():
        return deepcopy(state_mod.DEFAULT_STATE)
    raw = json.loads(read_text(path))
    if not isinstance(raw, dict):
        raise ValueError("state.json must contain an object")
    normalized = dict(state_mod.DEFAULT_STATE)
    normalized.update(raw)
    return state_mod.normalize(normalized)


def _ensure_state_has_initialization(repo_root: Path) -> None:
    path = repo_root / ".wiki-keeper" / "state.json"
    current = _load_state(repo_root)
    atomic_write(path, json.dumps(current, indent=2) + "\n")


def _read_roadmap_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for line in read_text(path).splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("-"):
            text = text[1:].strip()
        out.append(text)
    return out


def _render_roadmap(entries: list[str]) -> str:
    lines = ["# Wiki Review Roadmap", ""]
    for entry in entries:
        lines.append(f"- {entry}")
    lines.append("")
    return "\n".join(lines)


def _merge_roadmap(existing: list[str], generated: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for seq in (existing, generated):
        for item in seq:
            text = item.strip()
            if not text or text in seen:
                continue
            cat, _ = text.split("/", 1) if "/" in text else ("", text)
            if cat and cat not in CATEGORIES:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def _next_init_audit_path(repo_root: Path) -> Path:
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    base = repo_root / ".wiki-keeper" / "audits" / day
    base.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%H%M%S")
    candidate = base / f"initialization-{stamp}.md"
    if not candidate.exists():
        return candidate
    for idx in range(2, _MAX_AUDIT_COLLISION_ATTEMPTS):
        maybe = base / f"initialization-{stamp}-{idx:02d}.md"
        if not maybe.exists():
            return maybe
    raise RuntimeError("Failed to allocate initialization audit filename")


def _render_initialization_audit(
    *,
    bootstrap: BootstrapResult,
    inventory: MonorepoInventory,
    created_pages: list[str],
    skipped_pages: list[str],
    refresh_bootstrap: bool,
) -> str:
    lines = [
        "# Initialization Audit",
        "",
        f"Run: {_now_iso()}",
        f"Mode: {'refresh-bootstrap' if refresh_bootstrap else 'initial-bootstrap'}",
        f"Inventory hash: `{inventory.inventory_hash}`",
        f"Manager model: `{bootstrap.manager_model}`",
        f"Worker model: `{bootstrap.worker_model}`",
        f"Subagent count: {bootstrap.subagent_count}",
        "",
        "## Inventory Totals",
        f"- Discovered paths: {inventory.totals['discovered_paths']}",
        f"- Preview paths: {inventory.totals['preview_paths']}",
        f"- Oversized skipped: {inventory.totals['oversized_paths']}",
        f"- Binary skipped: {inventory.totals['binary_paths']}",
        "",
        "## Packet Plan",
    ]
    if bootstrap.packet_plan:
        for packet in bootstrap.packet_plan:
            lines.append(
                f"- `{packet['packet_id']}` {packet['focus']} ({len(packet['paths'])} paths)"
            )
    else:
        lines.append("- offline deterministic plan (no model packets)")
    lines.extend(["", "## Pages Created"])
    if created_pages:
        lines.extend(f"- `{item}`" for item in created_pages)
    else:
        lines.append("- _none_")
    lines.extend(["", "## Pages Skipped (already existed)"])
    if skipped_pages:
        lines.extend(f"- `{item}`" for item in skipped_pages)
    else:
        lines.append("- _none_")
    lines.extend(["", "## Truncated Areas"])
    if bootstrap.truncated_areas:
        lines.extend(f"- {item}" for item in bootstrap.truncated_areas)
    else:
        lines.append("- _none_")
    lines.extend(["", "## Open Questions"])
    if bootstrap.open_questions:
        lines.extend(f"- {item}" for item in bootstrap.open_questions)
    else:
        lines.append("- _none_")
    lines.extend(["", "## Sources", "- `inventory:monorepo`", ""])
    return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_init_api_key() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return
    raise RuntimeError(
        "OPENAI_API_KEY is required for initialization bootstrap. "
        "Run with --offline to scaffold without model calls."
    )


@contextlib.contextmanager
def _repo_env(repo_root: Path) -> Iterator[None]:
    previous = os.environ.get("WIKI_KEEPER_ROOT")
    os.environ["WIKI_KEEPER_ROOT"] = str(repo_root.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("WIKI_KEEPER_ROOT", None)
        else:
            os.environ["WIKI_KEEPER_ROOT"] = previous
