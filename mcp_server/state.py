from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .paths import state_path
from .storage import atomic_write, read_text


DEFAULT_STATE: dict[str, Any] = {
    "cursor": {"article_id": None, "index": -1},
    "last_run": None,
    "history": [],
}


def _normalize(state: dict[str, Any]) -> dict[str, Any]:
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
    return out


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
    return _normalize(raw)


def save(state: dict[str, Any]) -> None:
    normalized = _normalize(state)
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
) -> dict[str, Any]:
    date = date or now_date()
    normalized = _normalize(state)
    normalized["cursor"] = {"article_id": article_id, "index": index}
    normalized["last_run"] = {
        "date": date,
        "article_id": article_id,
        "outcome": outcome,
        "audit_path": audit_path,
    }
    normalized["history"].append(
        {"date": date, "article_id": article_id, "outcome": outcome}
    )
    return normalized
