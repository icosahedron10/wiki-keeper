from __future__ import annotations

from datetime import datetime, timezone

from .paths import log_path
from .storage import atomic_append


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append(tool: str, action: str, target: str, note: str = "") -> None:
    line = f"{_now()} {tool} {action} {target}"
    if note:
        line += f" | {note}"
    atomic_append(log_path(), line)
