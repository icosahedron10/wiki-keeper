from __future__ import annotations

from .pages import list_all
from .paths import CATEGORIES, index_path
from .storage import atomic_write

HEADER = (
    "# Wiki Index\n\n"
    "This index lists every page in the wiki. Every file under "
    "`.wiki-keeper/wiki/` (excluding `index.md` and `log.md`) must appear here.\n"
)

_SECTION_TITLES = {
    "decisions": "Decisions",
    "modules": "Modules",
    "concepts": "Concepts",
}


def render_index() -> str:
    pages = list_all()
    by_cat: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for page in pages:
        by_cat[page.category].append(page.title)
    parts = [HEADER]
    for cat in CATEGORIES:
        parts.append(f"\n## {_SECTION_TITLES[cat]}\n\n")
        titles = by_cat[cat]
        if not titles:
            parts.append("_none yet_\n")
            continue
        for title in sorted(titles):
            parts.append(f"- [{title}]({cat}/{title}.md)\n")
    return "".join(parts)


def rebuild() -> int:
    rendered = render_index()
    atomic_write(index_path(), rendered)
    return sum(1 for _ in list_all())
