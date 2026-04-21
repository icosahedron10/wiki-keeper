from __future__ import annotations

import re
from dataclasses import dataclass

from .pages import PageRef, list_all
from .storage import read_text


@dataclass
class Hit:
    page: PageRef
    score: int
    snippet: str

    def to_dict(self) -> dict:
        return {
            "page": self.page.rel,
            "title": self.page.title,
            "category": self.page.category,
            "score": self.score,
            "snippet": self.snippet,
        }


def _snippet(content: str, terms: list[str], width: int = 160) -> str:
    lower = content.lower()
    for t in terms:
        idx = lower.find(t)
        if idx >= 0:
            start = max(0, idx - width // 2)
            end = min(len(content), idx + width // 2)
            chunk = content[start:end].replace("\n", " ")
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(content) else ""
            return f"{prefix}{chunk}{suffix}"
    first_line = next((l for l in content.splitlines() if l.strip()), "")
    return first_line[:width]


def keyword_search(query: str, top_k: int = 5) -> list[Hit]:
    terms = [t for t in re.split(r"\s+", query.lower().strip()) if t]
    if not terms:
        return []
    hits: list[Hit] = []
    for page in list_all():
        content = read_text(page.path)
        lower = content.lower()
        score = 0
        title_lower = page.title.lower()
        for t in terms:
            score += lower.count(t)
            if t in title_lower:
                score += 5
        if score > 0:
            hits.append(Hit(page, score, _snippet(content, terms)))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]
