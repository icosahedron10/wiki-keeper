from __future__ import annotations

import re

from .pages import PageRef, find_page
from .paths import roadmap_path
from .storage import read_text


_LEADING_BULLET_RE = re.compile(r"^(-|\*|\d+\.)\s+")


def load_entries() -> list[str]:
    path = roadmap_path()
    if not path.is_file():
        raise FileNotFoundError(f"Missing roadmap at {path}")

    entries: list[str] = []
    seen: set[str] = set()
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = _LEADING_BULLET_RE.sub("", line).strip()
        if not line:
            continue
        if line in seen:
            continue
        seen.add(line)
        entries.append(line)
    return entries


def resolve_entries(entries: list[str]) -> tuple[list[PageRef], list[str]]:
    resolved: list[PageRef] = []
    unknown: list[str] = []
    for entry in entries:
        ref = find_page(entry)
        if ref is None:
            unknown.append(entry)
            continue
        resolved.append(ref)
    return resolved, unknown


def next_entry(entries: list[str], cursor_index: int) -> tuple[int, str] | None:
    if not entries:
        return None
    if cursor_index < -1 or cursor_index >= len(entries):
        cursor_index = -1
    next_idx = (cursor_index + 1) % len(entries)
    return next_idx, entries[next_idx]
