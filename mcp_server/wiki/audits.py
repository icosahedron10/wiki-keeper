from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.paths import audits_dir, safe_resolve
from ..core.storage import atomic_write, read_text


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-").lower()
    return cleaned or "article"


def _run_time_iso(run_at: datetime | None = None) -> str:
    run_at = run_at or datetime.now(timezone.utc)
    return run_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_folder(run_at: datetime | None = None) -> str:
    run_at = run_at or datetime.now(timezone.utc)
    return run_at.strftime("%Y-%m-%d")


def write_audit(
    *,
    article_id: str,
    article_path: str,
    source_globs: list[str],
    inspected_files: list[str],
    reader_a: str,
    reader_b: str,
    confidence: str,
    decision: str,
    rationale: str,
    diff_text: str,
    notes: list[str] | None = None,
    run_at: datetime | None = None,
) -> Path:
    run_at = run_at or datetime.now(timezone.utc)
    day = _date_folder(run_at)
    slug = _slug(article_id)
    run_iso = _run_time_iso(run_at)
    run_suffix = run_at.strftime("%H%M%S")
    base_name = f"{slug}-{run_suffix}"
    for attempt in range(1, 10_000):
        seq = "" if attempt == 1 else f"-{attempt:02d}"
        rel = f"{day}/{base_name}{seq}.md"
        path = safe_resolve(audits_dir(), rel)
        if not path.exists():
            break
    else:  # pragma: no cover - defensive guard
        raise RuntimeError(f"Failed to allocate unique audit filename for {article_id!r}")
    truncated_suffix = ""
    if len(inspected_files) >= 200:
        truncated_suffix = " (truncated at limit)"

    lines: list[str] = [
        f"# Audit: {article_id}",
        f"Run: {run_iso}",
        f"Article: {article_path}",
        "",
        "## Source globs",
    ]
    for glob_item in source_globs:
        lines.append(f"- {glob_item}")
    if not source_globs:
        lines.append("- _none_")

    lines.extend(
        [
            "",
            f"## Files inspected ({len(inspected_files)}{truncated_suffix})",
        ]
    )
    for item in inspected_files:
        lines.append(f"- {item}")
    if not inspected_files:
        lines.append("- _none_")

    if notes:
        lines.extend(["", "## Notes"])
        for note in notes:
            lines.append(f"- {note}")

    lines.extend(
        [
            "",
            "## Reader A (article -> sources)",
            reader_a.strip() or "_no output_",
            "",
            "## Reader B (sources -> article)",
            reader_b.strip() or "_no output_",
            "",
            "## Orchestrator decision",
            f"Confidence: {confidence}",
            f"Changes: {'yes' if decision == 'patch' else 'no'}",
            f"Rationale: {rationale}",
            "",
            "## Diff applied (if any)",
            "```diff",
            (diff_text or "").rstrip(),
            "```",
            "",
        ]
    )
    atomic_write(path, "\n".join(lines) + "\n")
    return path


def list_audits(article_id: str, limit: int = 10) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    slug = _slug(article_id)
    base = audits_dir()
    if not base.is_dir():
        return []
    hits: list[Path] = []
    for p in base.glob("*/*.md"):
        if not p.is_file():
            continue
        stem = p.stem
        if stem == slug or stem.startswith(f"{slug}-"):
            hits.append(p)
    hits.sort(
        key=lambda p: str(p.relative_to(base)).replace("\\", "/"),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for p in hits[:limit]:
        out.append(
            {
                "path": str(p.relative_to(base)).replace("\\", "/"),
                "content": read_text(p),
            }
        )
    return out


def latest_audit(article_id: str) -> dict[str, Any] | None:
    audits = list_audits(article_id, limit=1)
    return audits[0] if audits else None
