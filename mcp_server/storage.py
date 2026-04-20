from __future__ import annotations

import os
import tempfile
from pathlib import Path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def atomic_write(path: Path, content: str) -> None:
    """Write `content` to `path` atomically via tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp-", suffix=path.suffix or ".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_append(path: Path, line: str) -> None:
    """Append a single line atomically by rewriting the file."""
    existing = read_text(path) if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    atomic_write(path, existing + line.rstrip("\n") + "\n")
