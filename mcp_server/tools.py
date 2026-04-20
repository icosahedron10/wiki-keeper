from __future__ import annotations

from pathlib import Path
from typing import Any

from . import audits as audits_mod
from . import index as wiki_index
from . import lint as lint_mod
from . import nightly as nightly_mod
from . import roadmap as roadmap_mod
from . import search as search_mod
from . import state as state_mod
from . import validate as validate_mod
from . import wikilog
from .pages import (
    PageRef,
    extract_wikilinks,
    find_page,
    has_sources_section,
    is_stub,
    list_all,
    parse_page_frontmatter,
    resolve_or_plan,
)
from .paths import CATEGORIES, schema_path, sources_dir, safe_resolve
from .storage import atomic_write, read_text


def _page_to_dict(p: PageRef) -> dict[str, Any]:
    return {
        "title": p.title,
        "category": p.category,
        "path": p.rel,
        "exists": p.path.is_file(),
    }


def _article_id(frontmatter: dict[str, Any] | None, page: PageRef) -> str:
    if frontmatter and isinstance(frontmatter.get("id"), str):
        if frontmatter["id"].strip():
            return frontmatter["id"].strip()
    return f"{page.category}/{page.title}"


# ------------------------------------------------------------------ reads


def get_page(page_name: str) -> dict[str, Any]:
    ref = find_page(page_name)
    if ref is None:
        return {"found": False, "page_name": page_name}
    content = read_text(ref.path)
    return {
        "found": True,
        **_page_to_dict(ref),
        "content": content,
        "is_stub": is_stub(content),
        "has_sources": has_sources_section(content),
        "wikilinks": extract_wikilinks(content),
    }


def read_article(page_name: str) -> dict[str, Any]:
    base = get_page(page_name)
    if not base.get("found"):
        return base
    ref = find_page(page_name)
    assert ref is not None  # guarded by found check above
    frontmatter, body = parse_page_frontmatter(base["content"])
    article_id = _article_id(frontmatter, ref)
    return {
        **base,
        "article_id": article_id,
        "frontmatter": frontmatter,
        "body": body,
        "last_audit": audits_mod.latest_audit(article_id),
    }


def read_audits(article_id: str, limit: int = 5) -> dict[str, Any]:
    audits = audits_mod.list_audits(article_id, limit=limit)
    return {
        "article_id": article_id,
        "count": len(audits),
        "audits": audits,
    }


def list_pages(category: str | None = None) -> dict[str, Any]:
    pages = list_all()
    if category:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category {category!r}")
        pages = [p for p in pages if p.category == category]
    return {"count": len(pages), "pages": [_page_to_dict(p) for p in pages]}


def list_articles(category: str | None = None) -> dict[str, Any]:
    pages = list_all()
    if category:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category {category!r}")
        pages = [p for p in pages if p.category == category]

    article_rows: list[dict[str, Any]] = []
    for page in pages:
        frontmatter = None
        frontmatter_error = None
        try:
            frontmatter, _ = parse_page_frontmatter(read_text(page.path))
        except ValueError as exc:
            frontmatter_error = str(exc)
        article_id = _article_id(frontmatter, page)
        article_rows.append(
            {
                **_page_to_dict(page),
                "article_id": article_id,
                "has_frontmatter": bool(frontmatter),
                "frontmatter": frontmatter,
                "frontmatter_error": frontmatter_error,
                "last_audit": audits_mod.latest_audit(article_id),
            }
        )
    return {"count": len(article_rows), "articles": article_rows}


def next_review() -> dict[str, Any]:
    entries = roadmap_mod.load_entries()
    current_state = state_mod.load()
    cursor = current_state.get("cursor", {})
    next_item = roadmap_mod.next_entry(entries, int(cursor.get("index", -1)))
    if next_item is None:
        return {"has_next": False, "entry": None}
    idx, entry = next_item
    return {"has_next": True, "entry": entry, "index": idx}


def query_wiki(query: str, mode: str = "keyword", top_k: int = 5) -> dict[str, Any]:
    if mode not in ("keyword", "hybrid"):
        raise ValueError(
            f"mode {mode!r} not supported yet (semantic search is phase C)"
        )
    hits = search_mod.keyword_search(query, top_k=top_k)
    return {
        "query": query,
        "mode": mode,
        "hits": [h.to_dict() for h in hits],
    }


# ------------------------------------------------------------------ writes

_VALID_WRITE_MODES = ("replace", "append", "create_only")


def update_knowledge(page_name: str, content: str, mode: str = "replace") -> dict[str, Any]:
    if mode not in _VALID_WRITE_MODES:
        raise ValueError(f"mode must be one of {_VALID_WRITE_MODES}, got {mode!r}")
    ref = resolve_or_plan(page_name)
    created = not ref.path.is_file()
    if mode == "create_only" and not created:
        raise ValueError(f"Page {ref.rel} already exists")

    if mode == "append" and not created:
        existing = read_text(ref.path)
        if existing and not existing.endswith("\n"):
            existing += "\n"
        new_content = existing + content
    else:
        new_content = content

    if not new_content.endswith("\n"):
        new_content += "\n"

    atomic_write(ref.path, new_content)
    wiki_index.rebuild()

    action = "create" if created else ("append" if mode == "append" else "replace")
    wikilog.append("update_knowledge", action, ref.rel)

    return {
        "created": created,
        "mode": mode,
        **_page_to_dict(ref),
        "index_updated": True,
        "log_updated": True,
    }


def rebuild_index() -> dict[str, Any]:
    count = wiki_index.rebuild()
    wikilog.append(
        "rebuild_index",
        "rebuild",
        ".wiki-keeper/wiki/index.md",
        f"pages={count}",
    )
    return {"pages": count, "index_updated": True}


def run_review(article_id: str | None = None) -> dict[str, Any]:
    return nightly_mod.run_review(
        article_id=article_id,
        update_knowledge_fn=update_knowledge,
    )


def run_nightly(budget: int = 1) -> dict[str, Any]:
    return nightly_mod.run_nightly(
        budget=budget,
        update_knowledge_fn=update_knowledge,
    )


# ------------------------------------------------------------------ ingestion


def _read_source(source_path: str) -> tuple[str, str]:
    abs_path = safe_resolve(sources_dir(), source_path)
    if not abs_path.is_file():
        raise FileNotFoundError(f"No source at {source_path!r}")
    return str(abs_path.relative_to(sources_dir())).replace("\\", "/"), read_text(abs_path)


def _candidate_pages(text: str, top_k: int = 8) -> list[dict[str, Any]]:
    snippet = " ".join(text.split()[:200])
    if not snippet.strip():
        return []
    hits = search_mod.keyword_search(snippet, top_k=top_k)
    return [h.to_dict() for h in hits]


def _schema_reminder() -> str:
    if schema_path().is_file():
        return read_text(schema_path())
    return ""


def propose_ingest(source_path: str, context: str | None = None) -> dict[str, Any]:
    rel, content = _read_source(source_path)
    return {
        "source_path": f"sources/{rel}",
        "source_content": content,
        "context": context,
        "candidate_pages": _candidate_pages(content),
        "schema": _schema_reminder(),
        "instructions": (
            "Dry run. Inspect candidate_pages, decide which to update or create, "
            "then call update_knowledge per page and ingest_source to record it."
        ),
    }


def ingest_source(source_path: str, context: str | None = None) -> dict[str, Any]:
    rel, content = _read_source(source_path)
    wikilog.append("ingest_source", "ingest", f"sources/{rel}", context or "")
    return {
        "source_path": f"sources/{rel}",
        "source_content": content,
        "context": context,
        "candidate_pages": _candidate_pages(content),
        "schema": _schema_reminder(),
        "log_updated": True,
        "instructions": (
            "Source ingestion recorded. For each impacted concept/module/decision, "
            "call update_knowledge. Prefer updating existing pages. Cite this "
            f"source under '## Sources' as [{Path(rel).name}](../../sources/{rel})."
        ),
    }


def lint_wiki() -> dict[str, Any]:
    return lint_mod.run().to_dict()


def validate() -> dict[str, Any]:
    return validate_mod.run().to_dict()
