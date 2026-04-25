from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .frontmatter import serialize_frontmatter
from .llm import AsyncOpenAIClient, complete_json_schema
from .monorepo_inventory import MonorepoInventory
from .pages import parse_name
from .validate import page_is_schema_compliant

_CONFIDENCE_LEVELS = {"high", "medium", "low"}
_CATEGORIES = {"concepts", "modules", "decisions"}


@dataclass(frozen=True)
class GeneratedPage:
    category: str
    title: str
    content: str
    confidence: str
    evidence_sources: list[str]
    frontmatter_sources: list[str]

    @property
    def rel_path(self) -> str:
        return f".wiki-keeper/wiki/{self.category}/{self.title}.md"


@dataclass(frozen=True)
class BootstrapResult:
    pages: list[GeneratedPage]
    roadmap_entries: list[str]
    model: str
    open_questions: list[str]
    truncated_areas: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "page_count": len(self.pages),
            "pages": [page.rel_path for page in self.pages],
            "roadmap_entries": list(self.roadmap_entries),
            "open_questions": list(self.open_questions),
            "truncated_areas": list(self.truncated_areas),
        }


async def run_bootstrap(
    *,
    client: AsyncOpenAIClient,
    inventory: MonorepoInventory,
    model: str,
) -> BootstrapResult:
    payload = await complete_json_schema(
        client,
        model=model,
        instructions=(
            "You initialize a repository wiki from bounded inventory evidence. "
            "Only make claims supported by provided paths. Return strict JSON."
        ),
        input_text=(
            "Repository inventory:\n"
            f"{json.dumps(_inventory_payload(inventory), indent=2)}\n\n"
            "Generate initial wiki pages including repository overview, monorepo map, "
            "build/test, and major modules/packages. Use low-confidence stubs where "
            "evidence is thin."
        ),
        schema_name="wiki_keeper_init",
        schema=_bootstrap_schema(),
    )
    pages, roadmap, open_questions, truncated_areas = validate_synthesis_payload(
        payload,
        available_paths=set(inventory.discovered_paths),
        inventory=inventory,
    )
    return BootstrapResult(
        pages=pages,
        roadmap_entries=roadmap,
        model=model,
        open_questions=open_questions,
        truncated_areas=truncated_areas,
    )


def validate_synthesis_payload(
    payload: dict[str, Any],
    *,
    available_paths: set[str],
    inventory: MonorepoInventory,
) -> tuple[list[GeneratedPage], list[str], list[str], list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("Synthesis payload must be an object")
    pages_raw = payload.get("pages")
    if not isinstance(pages_raw, list):
        raise ValueError("Synthesis payload must include pages[]")
    pages = [_validate_generated_page(row, available_paths=available_paths) for row in pages_raw]
    pages = _ensure_core_pages(pages, inventory=inventory, available_paths=available_paths)
    pages = _ensure_module_pages(pages, inventory=inventory, available_paths=available_paths)
    roadmap = _normalize_roadmap(payload.get("roadmap_entries", []), pages)
    open_questions = _normalize_string_list(payload.get("open_questions", []), "open_questions")
    truncated_areas = _normalize_string_list(payload.get("truncated_areas", []), "truncated_areas")
    if inventory.oversized_paths:
        truncated_areas.append(f"Oversized files omitted: {len(inventory.oversized_paths)}")
    if inventory.binary_paths:
        truncated_areas.append(f"Binary files omitted: {len(inventory.binary_paths)}")
    return pages, roadmap, sorted(set(open_questions)), sorted(set(truncated_areas))


def _bootstrap_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["pages", "roadmap_entries", "open_questions", "truncated_areas"],
        "properties": {
            "pages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "category",
                        "title",
                        "summary",
                        "key_facts",
                        "details",
                        "relationships",
                        "sources",
                        "open_questions",
                        "confidence",
                        "frontmatter_sources",
                    ],
                    "properties": {
                        "category": {"type": "string", "enum": ["concepts", "modules", "decisions"]},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "key_facts": {"type": "array", "items": {"type": "string"}},
                        "details": {"type": "array", "items": {"type": "string"}},
                        "relationships": {"type": "array", "items": {"type": "string"}},
                        "sources": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                        "open_questions": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "frontmatter_sources": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "roadmap_entries": {"type": "array", "items": {"type": "string"}},
            "open_questions": {"type": "array", "items": {"type": "string"}},
            "truncated_areas": {"type": "array", "items": {"type": "string"}},
        },
    }


def _inventory_payload(inventory: MonorepoInventory) -> dict[str, Any]:
    return {
        "repo_root": inventory.repo_root,
        "totals": inventory.totals,
        "inventory_hash": inventory.inventory_hash,
        "classifications": inventory.classifications,
        "previews": [
            {"path": item.path, "kind": item.kind, "preview": item.preview}
            for item in inventory.previews[:160]
        ],
    }


def _validate_generated_page(row: Any, *, available_paths: set[str]) -> GeneratedPage:
    if not isinstance(row, dict):
        raise ValueError("page entry must be an object")
    category = str(row.get("category", "")).strip()
    title = str(row.get("title", "")).strip()
    if category not in _CATEGORIES:
        raise ValueError(f"Invalid category {category!r}")
    if not title:
        raise ValueError("Page title is required")
    parse_name(f"{category}/{title}")
    confidence = str(row.get("confidence", "low")).lower()
    if confidence not in _CONFIDENCE_LEVELS:
        confidence = "low"
    evidence_sources = _normalize_sources(row.get("sources", []), available_paths=available_paths)
    frontmatter_sources = _normalize_frontmatter_sources(
        row.get("frontmatter_sources", []),
        available_paths=available_paths,
        fallback_sources=evidence_sources,
    )
    content = _render_page_markdown(
        category=category,
        title=title,
        summary=str(row.get("summary", "")).strip() or "No summary was generated.",
        key_facts=_normalize_string_list(row.get("key_facts", []), "key_facts"),
        details=_normalize_string_list(row.get("details", []), "details"),
        relationships=_normalize_string_list(row.get("relationships", []), "relationships"),
        evidence_sources=evidence_sources,
        open_questions=_normalize_string_list(row.get("open_questions", []), "open_questions"),
        confidence=confidence,
        frontmatter_sources=frontmatter_sources,
    )
    body = content
    if category == "modules":
        _, body = _split_frontmatter(content)
    if not page_is_schema_compliant(body):
        raise ValueError(f"Generated page {category}/{title} is missing required sections")
    return GeneratedPage(category, title, content, confidence, evidence_sources, frontmatter_sources)


def _split_frontmatter(content: str) -> tuple[dict[str, Any] | None, str]:
    from .frontmatter import parse_frontmatter

    return parse_frontmatter(content)


def _normalize_sources(value: Any, *, available_paths: set[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("Generated page must include at least one source")
    out: list[str] = []
    for item in value:
        src = str(item).replace("\\", "/").strip()
        if not src:
            continue
        if src.startswith("inventory:") or src in available_paths:
            out.append(src)
        else:
            raise ValueError(f"Speculative source not found in inventory: {src!r}")
    if not out:
        raise ValueError("Generated page must include at least one valid source")
    return sorted(set(out))


def _normalize_frontmatter_sources(value: Any, *, available_paths: set[str], fallback_sources: list[str]) -> list[str]:
    out: list[str] = []
    if isinstance(value, list):
        for item in value:
            src = str(item).replace("\\", "/").strip()
            if src and src in available_paths:
                out.append(src)
    if not out:
        out = [src for src in fallback_sources if src in available_paths][:4]
    return sorted(set(out))


def _normalize_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [text for item in value if (text := str(item).strip())]


def _render_page_markdown(
    *,
    category: str,
    title: str,
    summary: str,
    key_facts: list[str],
    details: list[str],
    relationships: list[str],
    evidence_sources: list[str],
    open_questions: list[str],
    confidence: str,
    frontmatter_sources: list[str],
) -> str:
    lines = [f"# {title}", ""]
    if confidence == "low":
        lines.extend(["> stub", ""])
    lines.extend(
        [
            "## Summary",
            summary,
            "",
            "## Key Facts",
            *[f"- {item}" for item in (key_facts or ["Evidence is limited; this page is a bootstrap stub."])],
            "",
            "## Details",
            *(details or ["No additional detail yet."]),
            "",
            "## Relationships",
            *[f"- {item}" for item in (relationships or ["Related pages will be refined after review."])],
            "",
            "## Sources",
            *[_format_source_line(item) for item in evidence_sources],
            "",
            "## Open Questions",
            *[f"- {item}" for item in (open_questions or ["What should be documented here first?"])],
            "",
        ]
    )
    body = "\n".join(lines).rstrip() + "\n"
    if category != "modules":
        return body
    return serialize_frontmatter(
        {"id": _slug(title), "title": title, "sources": frontmatter_sources},
        body,
    )


def _format_source_line(source: str) -> str:
    return f"- `{source}`" if source.startswith("inventory:") else f"- `repo:{source}`"


def _ensure_core_pages(pages: list[GeneratedPage], *, inventory: MonorepoInventory, available_paths: set[str]) -> list[GeneratedPage]:
    wanted = {
        ("concepts", "Repository Overview"): "Repository structure and major subsystems.",
        ("concepts", "Monorepo Map"): "Repository package and application topology.",
        ("concepts", "Build and Test"): "Build and test commands inferred from manifests and CI.",
    }
    existing = {(page.category, page.title) for page in pages}
    for (category, title), summary in wanted.items():
        if (category, title) not in existing:
            pages.append(_fallback_page(category=category, title=title, summary=summary, evidence_sources=_default_sources(available_paths)))
    return _dedupe_pages(pages)


def _ensure_module_pages(pages: list[GeneratedPage], *, inventory: MonorepoInventory, available_paths: set[str]) -> list[GeneratedPage]:
    if any(page.category == "modules" for page in pages):
        return _dedupe_pages(pages)
    for title in _module_title_candidates(inventory)[:8]:
        pages.append(
            _fallback_page(
                category="modules",
                title=title,
                summary=f"Bootstrap module stub for {title}.",
                evidence_sources=_module_evidence_sources(title=title, inventory=inventory) or _default_sources(available_paths),
            )
        )
    return _dedupe_pages(pages)


def _fallback_page(*, category: str, title: str, summary: str, evidence_sources: list[str]) -> GeneratedPage:
    content = _render_page_markdown(
        category=category,
        title=title,
        summary=summary,
        key_facts=["Generated from inventory signals only."],
        details=["Deeper details require additional source scans."],
        relationships=["Related module/concept links will be refined after review."],
        evidence_sources=evidence_sources,
        open_questions=["What are the most important correctness and reliability concerns here?"],
        confidence="low",
        frontmatter_sources=[src for src in evidence_sources if not src.startswith("inventory:")][:4],
    )
    return GeneratedPage(category, title, content, "low", evidence_sources, [src for src in evidence_sources if not src.startswith("inventory:")][:4])


def _default_sources(available_paths: set[str]) -> list[str]:
    if not available_paths:
        return ["inventory:no-files-discovered"]
    return sorted(available_paths)[:4]


def _module_title_candidates(inventory: MonorepoInventory) -> list[str]:
    raw = list(inventory.classifications.get("apps_services", [])) + list(inventory.classifications.get("libraries", []))
    if not raw:
        raw = [item for item in inventory.classifications.get("package_roots", []) if item != "."]
    titles = []
    for item in raw:
        name = item.strip("/").split("/")[-1]
        title = _humanize(name)
        if title:
            titles.append(title)
    return sorted(set(titles))


def _module_evidence_sources(*, title: str, inventory: MonorepoInventory) -> list[str]:
    needle = title.lower().replace(" ", "")
    matches: list[str] = []
    for path in inventory.discovered_paths:
        if needle in re.sub(r"[^a-z0-9]+", "", path.lower()):
            matches.append(path)
        if len(matches) >= 4:
            break
    return matches


def _normalize_roadmap(value: Any, pages: list[GeneratedPage]) -> list[str]:
    valid = {f"{page.category}/{page.title}" for page in pages}
    out = [str(item).strip() for item in value] if isinstance(value, list) else []
    out = [item for item in out if item in valid]
    out.extend(f"{page.category}/{page.title}" for page in pages)
    if not out:
        out = [f"{page.category}/{page.title}" for page in pages[:10]]
    return list(dict.fromkeys(out))


def _dedupe_pages(pages: list[GeneratedPage]) -> list[GeneratedPage]:
    return list({(page.category, page.title): page for page in pages}.values())


def _humanize(value: str) -> str:
    words = [word for word in re.split(r"[-_.]+", value.strip()) if word]
    return " ".join(word[:1].upper() + word[1:] for word in words)


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-").lower() or "module"
