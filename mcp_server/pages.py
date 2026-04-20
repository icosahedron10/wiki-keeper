from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .paths import CATEGORIES, safe_resolve, wiki_dir


@dataclass(frozen=True)
class PageRef:
    title: str
    category: str  # one of CATEGORIES
    path: Path

    @property
    def rel(self) -> str:
        return f"{self.category}/{self.title}.md"


def _category_for_title(title: str) -> str:
    if title.lower().startswith("decision - "):
        return "decisions"
    return "concepts"


def parse_name(name: str) -> tuple[str | None, str]:
    """Split `category/title` or bare `title`. Strips .md if present."""
    name = name.strip()
    if name.endswith(".md"):
        name = name[:-3]
    if "/" in name:
        category, title = name.split("/", 1)
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category {category!r}")
        _validate_title(title)
        return category, title
    _validate_title(name)
    return None, name


def _validate_title(title: str) -> None:
    title = title.strip()
    if not title:
        raise ValueError("Page title cannot be empty")
    if "/" in title or "\\" in title:
        raise ValueError("Page title cannot contain path separators")
    if title in {".", ".."}:
        raise ValueError("Page title cannot be . or ..")
    if any(part in {".", ".."} for part in Path(title).parts):
        raise ValueError("Page title cannot contain traversal segments")


def _page_path(category: str, title: str) -> Path:
    rel = f"{category}/{title}.md"
    return safe_resolve(wiki_dir(), rel)


def find_page(name: str) -> PageRef | None:
    category, title = parse_name(name)
    if category is not None:
        path = _page_path(category, title)
        return PageRef(title, category, path) if path.is_file() else None
    for cat in CATEGORIES:
        path = _page_path(cat, title)
        if path.is_file():
            return PageRef(title, cat, path)
    return None


def resolve_or_plan(name: str) -> PageRef:
    """Return an existing page or an unwritten PageRef with inferred category."""
    existing = find_page(name)
    if existing is not None:
        return existing
    category, title = parse_name(name)
    if category is None:
        category = _category_for_title(title)
    path = _page_path(category, title)
    return PageRef(title, category, path)


def list_all() -> list[PageRef]:
    out: list[PageRef] = []
    for cat in CATEGORIES:
        d = wiki_dir() / cat
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            out.append(PageRef(p.stem, cat, p))
    return out


_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


def extract_wikilinks(content: str) -> list[str]:
    return [m.group(1).strip() for m in _WIKILINK_RE.finditer(content)]


def has_sources_section(content: str) -> bool:
    lines = content.splitlines()
    in_sources = False
    for line in lines:
        if re.match(r"^##\s+Sources\s*$", line):
            in_sources = True
            continue
        if in_sources:
            if line.startswith("## "):
                return False
            if line.strip().startswith(("-", "*")) and line.strip() != "-":
                return True
    return False


def is_stub(content: str) -> bool:
    lines = content.splitlines()
    for line in lines[1:6]:
        if line.strip().lower() == "> stub":
            return True
    return False


def parse_page_frontmatter(
    content: str,
) -> tuple[dict[str, Any] | None, str]:
    """Parse optional YAML frontmatter, returning (frontmatter, markdown_body)."""
    return parse_frontmatter(content)
