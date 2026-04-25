from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..core.paths import state_path
from ..core.storage import atomic_write, read_text


DEFAULT_STATE: dict[str, Any] = {
    "cursor": {"article_id": None, "index": -1},
    "last_run": None,
    "history": [],
    "git": {
        "last_processed_commit": None,
        "last_seen_commit": None,
        "default_branch": None,
        "runs": [],
    },
    "initialization": {
        "completed_at": None,
        "inventory_hash": None,
        "model": None,
        "status": "not_started",
    },
}


def normalize(state: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(DEFAULT_STATE)
    if isinstance(state.get("cursor"), dict):
        out["cursor"]["article_id"] = state["cursor"].get("article_id")
        try:
            out["cursor"]["index"] = int(state["cursor"].get("index", -1))
        except (TypeError, ValueError):
            out["cursor"]["index"] = -1
    if state.get("last_run") is not None:
        out["last_run"] = state["last_run"]
    if isinstance(state.get("history"), list):
        out["history"] = state["history"]
    if isinstance(state.get("git"), dict):
        git = state["git"]
        out["git"]["last_processed_commit"] = _optional_str(
            git.get("last_processed_commit")
        )
        out["git"]["last_seen_commit"] = _optional_str(git.get("last_seen_commit"))
        out["git"]["default_branch"] = _optional_str(git.get("default_branch"))
        if isinstance(git.get("runs"), list):
            out["git"]["runs"] = git["runs"]
    if isinstance(state.get("initialization"), dict):
        init = state["initialization"]
        out["initialization"]["completed_at"] = init.get("completed_at")
        out["initialization"]["inventory_hash"] = init.get("inventory_hash")
        out["initialization"]["model"] = init.get("model") or init.get("manager_model")
        status = init.get("status")
        if isinstance(status, str) and status.strip():
            out["initialization"]["status"] = status.strip()
    return out


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load() -> dict[str, Any]:
    path = state_path()
    if not path.is_file():
        raise FileNotFoundError(f"Missing state file at {path}")
    try:
        raw = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("state.json must contain a JSON object")
    return normalize(raw)


def save(state: dict[str, Any]) -> None:
    normalized = normalize(state)
    atomic_write(state_path(), json.dumps(normalized, indent=2) + "\n")


def now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_run(
    state: dict[str, Any],
    *,
    article_id: str,
    index: int,
    outcome: str,
    audit_path: str | None,
    date: str | None = None,
    commit_range: dict[str, Any] | None = None,
    changed_paths: list[str] | None = None,
    patch_status: str | None = None,
) -> dict[str, Any]:
    date = date or now_date()
    normalized = normalize(state)
    last_run: dict[str, Any] = {
        "date": date,
        "article_id": article_id,
        "outcome": outcome,
        "audit_path": audit_path,
    }
    history_entry: dict[str, Any] = {
        "date": date,
        "article_id": article_id,
        "outcome": outcome,
    }
    if commit_range:
        last_run["commit_range"] = commit_range
        history_entry["commit_range"] = commit_range
    if changed_paths is not None:
        clean_paths = [str(path) for path in changed_paths]
        last_run["changed_paths"] = clean_paths
        history_entry["changed_paths"] = clean_paths
    if patch_status:
        last_run["patch_status"] = patch_status
        history_entry["patch_status"] = patch_status

    normalized["cursor"] = {"article_id": article_id, "index": index}
    normalized["last_run"] = last_run
    normalized["history"].append(history_entry)
    return normalized


def set_git_baseline(
    state: dict[str, Any],
    *,
    commit: str | None,
    default_branch: str | None = None,
) -> dict[str, Any]:
    normalized = normalize(state)
    if commit:
        normalized["git"]["last_processed_commit"] = commit
        normalized["git"]["last_seen_commit"] = commit
    if default_branch:
        normalized["git"]["default_branch"] = default_branch
    return normalized


def record_git_run(
    state: dict[str, Any],
    *,
    since: str | None,
    until: str | None,
    default_branch: str | None,
    changed_paths: list[str],
    outcome: str,
    audit_paths: list[str],
    patch_status: str,
    date: str | None = None,
) -> dict[str, Any]:
    date = date or now_date()
    normalized = normalize(state)
    if until:
        normalized["git"]["last_seen_commit"] = until
        if outcome not in {"dry_run", "error", "partial"}:
            normalized["git"]["last_processed_commit"] = until
    if default_branch:
        normalized["git"]["default_branch"] = default_branch
    normalized["git"]["runs"].append(
        {
            "date": date,
            "since": since,
            "until": until,
            "changed_paths": [str(path) for path in changed_paths],
            "outcome": outcome,
            "audit_paths": list(audit_paths),
            "patch_status": patch_status,
        }
    )
    return normalized
