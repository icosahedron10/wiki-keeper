from __future__ import annotations

from datetime import datetime, timezone

from ..core.paths import log_path
from ..core.storage import atomic_append


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _single_line(value: str) -> str:
    return " ".join(str(value).replace("\r", "\n").split()).strip()


def append(tool: str, action: str, target: str, note: str = "") -> None:
    tool_text = _single_line(tool) or "-"
    action_text = _single_line(action) or "-"
    target_text = _single_line(target) or "-"
    note_text = _single_line(note)
    line = f"{_now()} {tool_text} {action_text} {target_text}"
    if note_text:
        line += f" | {note_text}"
    atomic_append(log_path(), line)
