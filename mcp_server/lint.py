from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pages import (
    PageRef,
    extract_wikilinks,
    find_page,
    is_stub,
    list_all,
)
from .paths import index_path, log_path
from .roadmap import load_entries
from .storage import read_text


@dataclass
class LintReport:
    orphans: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    broken_links: list[tuple[str, str]] = field(default_factory=list)
    not_in_index: list[str] = field(default_factory=list)
    malformed_log_lines: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.orphans
            or self.missing_sources
            or self.broken_links
            or self.not_in_index
            or self.malformed_log_lines
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "orphans": self.orphans,
            "missing_sources": self.missing_sources,
            "broken_links": [
                {"page": p, "link": l} for p, l in self.broken_links
            ],
            "not_in_index": self.not_in_index,
            "malformed_log_lines": self.malformed_log_lines,
        }


_LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s+\S+\s+\S+\s+\S+"
)


def _check_index(pages: list[PageRef]) -> list[str]:
    if not index_path().is_file():
        return [p.rel for p in pages]
    text = read_text(index_path())
    missing: list[str] = []
    for p in pages:
        needle = f"]({p.category}/{p.title}.md)"
        if needle not in text:
            missing.append(p.rel)
    return missing


def _check_links(pages: list[PageRef]) -> tuple[list[tuple[str, str]], set[str]]:
    broken: list[tuple[str, str]] = []
    referenced: set[str] = set()
    for p in pages:
        content = read_text(p.path)
        for link in extract_wikilinks(content):
            referenced.add(link)
            if find_page(link) is None:
                broken.append((p.rel, link))
    return broken, referenced


def _check_orphans(pages: list[PageRef], referenced: set[str], roadmap_entries: set[str]) -> list[str]:
    referenced_titles = {r.split("/")[-1] for r in referenced}
    orphans: list[str] = []
    for p in pages:
        if p.title in referenced_titles:
            continue
        if f"{p.category}/{p.title}" in roadmap_entries:
            continue
        orphans.append(p.rel)
    return orphans


def _check_log() -> list[int]:
    if not log_path().is_file():
        return []
    bad: list[int] = []
    for i, line in enumerate(read_text(log_path()).splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-", ">")):
            continue
        if stripped.startswith("Format:") or stripped.startswith("Append-only"):
            continue
        if not _LOG_LINE_RE.match(stripped):
            bad.append(i)
    return bad


def run() -> LintReport:
    report = LintReport()
    pages = list_all()
    try:
        roadmap_entries = set(load_entries())
    except FileNotFoundError:
        roadmap_entries = set()
    report.not_in_index = _check_index(pages)
    report.broken_links, referenced = _check_links(pages)
    report.orphans = _check_orphans(pages, referenced, roadmap_entries)
    report.malformed_log_lines = _check_log()
    return report
