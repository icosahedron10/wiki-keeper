from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .frontmatter import serialize_frontmatter
from .llm import LLMClient
from .monorepo_inventory import MonorepoInventory, bounded_slice_previews
from .pages import parse_name
from .validate import page_is_schema_compliant

DEFAULT_INIT_MANAGER_MODEL = "gpt-5.4"
DEFAULT_INIT_WORKER_MODEL = "gpt-5.4-mini"
DEFAULT_INIT_MANAGER_REASONING = "high"
DEFAULT_INIT_WORKER_REASONING = "medium"

_CONFIDENCE_LEVELS = {"high", "medium", "low"}
_CATEGORIES = {"concepts", "modules", "decisions"}


@dataclass(frozen=True)
class InitModelConfig:
    manager_model: str
    worker_model: str
    manager_reasoning: str
    worker_reasoning: str

    @classmethod
    def from_env(cls) -> "InitModelConfig":
        import os

        return cls(
            manager_model=os.environ.get(
                "WIKI_KEEPER_INIT_MANAGER_MODEL", DEFAULT_INIT_MANAGER_MODEL
            ),
            worker_model=os.environ.get(
                "WIKI_KEEPER_INIT_WORKER_MODEL", DEFAULT_INIT_WORKER_MODEL
            ),
            manager_reasoning=os.environ.get(
                "WIKI_KEEPER_INIT_MANAGER_REASONING", DEFAULT_INIT_MANAGER_REASONING
            ),
            worker_reasoning=os.environ.get(
                "WIKI_KEEPER_INIT_WORKER_REASONING", DEFAULT_INIT_WORKER_REASONING
            ),
        )


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
    subagent_count: int
    manager_model: str
    worker_model: str
    open_questions: list[str]
    truncated_areas: list[str]
    packet_plan: list[dict[str, Any]]
    worker_reports: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subagent_count": self.subagent_count,
            "manager_model": self.manager_model,
            "worker_model": self.worker_model,
            "page_count": len(self.pages),
            "pages": [page.rel_path for page in self.pages],
            "roadmap_entries": list(self.roadmap_entries),
            "open_questions": list(self.open_questions),
            "truncated_areas": list(self.truncated_areas),
            "packet_plan": self.packet_plan,
            "worker_reports": self.worker_reports,
        }


def run_bootstrap(
    *,
    llm: LLMClient,
    repo_root: Path,
    inventory: MonorepoInventory,
    max_subagents: int,
    model_config: InitModelConfig,
) -> BootstrapResult:
    if max_subagents < 1:
        raise ValueError("max_subagents must be >= 1")
    candidates = _build_candidate_packets(inventory)
    manager_plan = _manager_select_packets(
        llm=llm,
        inventory=inventory,
        candidates=candidates,
        max_subagents=max_subagents,
        model_config=model_config,
    )
    packet_plan, subagent_count = validate_manager_packet_plan(
        manager_plan,
        available_paths=set(inventory.discovered_paths),
        max_subagents=max_subagents,
    )
    worker_reports = _run_workers(
        llm=llm,
        repo_root=repo_root,
        packet_plan=packet_plan,
        inventory=inventory,
        model_config=model_config,
        max_workers=subagent_count,
    )
    synthesis = _manager_synthesize_pages(
        llm=llm,
        inventory=inventory,
        packet_plan=packet_plan,
        worker_reports=worker_reports,
        model_config=model_config,
    )
    pages, roadmap_entries, open_questions, truncated_areas = validate_synthesis_payload(
        synthesis,
        available_paths=set(inventory.discovered_paths),
        inventory=inventory,
    )
    return BootstrapResult(
        pages=pages,
        roadmap_entries=roadmap_entries,
        subagent_count=subagent_count,
        manager_model=model_config.manager_model,
        worker_model=model_config.worker_model,
        open_questions=open_questions,
        truncated_areas=truncated_areas,
        packet_plan=packet_plan,
        worker_reports=worker_reports,
    )


def deterministic_bootstrap_plan(inventory: MonorepoInventory, *, max_modules: int = 8) -> BootstrapResult:
    paths = set(inventory.discovered_paths)
    module_titles = _module_title_candidates(inventory)[:max_modules]
    pages: list[GeneratedPage] = [
        _fallback_page(
            category="concepts",
            title="Repository Overview",
            summary="Repository structure and ownership overview generated from local inventory only.",
            evidence_sources=_default_sources(paths),
            confidence="low",
        ),
        _fallback_page(
            category="concepts",
            title="Monorepo Map",
            summary="High-level app/library/package map inferred from top-level folders and manifests.",
            evidence_sources=_default_sources(paths),
            confidence="low",
        ),
        _fallback_page(
            category="concepts",
            title="Build and Test",
            summary="Build and test commands were inferred from discovered manifests and CI configuration.",
            evidence_sources=_default_sources(paths),
            confidence="low",
        ),
    ]
    for title in module_titles:
        evidence = _module_evidence_sources(title=title, inventory=inventory)
        pages.append(
            _fallback_page(
                category="modules",
                title=title,
                summary=f"Module page stub for {title}, pending deeper synthesis.",
                evidence_sources=evidence or _default_sources(paths),
                confidence="low",
            )
        )
    roadmap = [f"concepts/{pages[0].title}", f"concepts/{pages[1].title}", f"concepts/{pages[2].title}"] + [
        f"modules/{title}" for title in module_titles
    ]
    return BootstrapResult(
        pages=pages,
        roadmap_entries=roadmap,
        subagent_count=0,
        manager_model="offline",
        worker_model="offline",
        open_questions=[
            "Which modules are highest priority for deep documentation?",
            "Which build/test flows are authoritative in CI versus local developer scripts?",
        ],
        truncated_areas=[
            f"Oversized files skipped: {len(inventory.oversized_paths)}",
            f"Binary files skipped: {len(inventory.binary_paths)}",
        ],
        packet_plan=[],
        worker_reports=[],
    )


def validate_manager_packet_plan(
    payload: dict[str, Any],
    *,
    available_paths: set[str],
    max_subagents: int,
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(payload, dict):
        raise ValueError("Manager packet plan must be an object")
    try:
        requested = int(payload.get("subagent_count", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("Manager subagent_count must be an integer") from exc
    if requested < 1:
        requested = 1
    subagent_limit = min(requested, max_subagents)
    packet_rows = payload.get("packets")
    if not isinstance(packet_rows, list) or not packet_rows:
        raise ValueError("Manager packet plan must include at least one packet")
    normalized: list[dict[str, Any]] = []
    for row in packet_rows[:subagent_limit]:
        if not isinstance(row, dict):
            raise ValueError("Manager packet item must be an object")
        packet_id = str(row.get("packet_id", "")).strip()
        focus = str(row.get("focus", "")).strip()
        paths = row.get("paths")
        if not packet_id:
            raise ValueError("Packet missing packet_id")
        if not focus:
            raise ValueError("Packet missing focus")
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"Packet {packet_id!r} missing paths")
        clean_paths: list[str] = []
        for item in paths:
            rel = str(item).replace("\\", "/").strip()
            if rel not in available_paths:
                raise ValueError(f"Packet {packet_id!r} references unknown path {rel!r}")
            clean_paths.append(rel)
        normalized.append({"packet_id": packet_id, "focus": focus, "paths": clean_paths[:80]})
    return normalized, len(normalized)


def validate_worker_report(report: dict[str, Any], *, available_paths: set[str]) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("Worker report must be an object")
    packet_id = str(report.get("packet_id", "")).strip()
    if not packet_id:
        raise ValueError("Worker report missing packet_id")
    normalized = {"packet_id": packet_id}
    normalized["facts"] = _normalize_evidenced_rows(report.get("facts", []), available_paths, field_name="facts")
    normalized["module_candidates"] = _normalize_modules(report.get("module_candidates", []), available_paths)
    normalized["entrypoints"] = _normalize_path_list(report.get("entrypoints", []), available_paths, "entrypoints")
    normalized["dependencies"] = _normalize_evidenced_rows(
        report.get("dependencies", []), available_paths, field_name="dependencies"
    )
    normalized["risks"] = _normalize_evidenced_rows(report.get("risks", []), available_paths, field_name="risks")
    normalized["open_questions"] = _normalize_evidenced_rows(
        report.get("open_questions", []), available_paths, field_name="open_questions"
    )
    return normalized


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
    pages: list[GeneratedPage] = []
    for row in pages_raw:
        page = _validate_generated_page(row, available_paths=available_paths)
        pages.append(page)
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


def _manager_select_packets(
    *,
    llm: LLMClient,
    inventory: MonorepoInventory,
    candidates: list[dict[str, Any]],
    max_subagents: int,
    model_config: InitModelConfig,
) -> dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["subagent_count", "packets"],
        "properties": {
            "subagent_count": {"type": "integer", "minimum": 1, "maximum": max_subagents},
            "packets": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["packet_id", "focus", "paths"],
                    "properties": {
                        "packet_id": {"type": "string"},
                        "focus": {"type": "string"},
                        "paths": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    },
                },
            },
        },
    }
    system_prompt = (
        "You plan bounded parallel discovery for wiki initialization. "
        "Return strict JSON matching the schema."
    )
    user_prompt = (
        "Inventory summary:\n"
        f"{json.dumps(_inventory_summary(inventory), indent=2)}\n\n"
        "Candidate packets:\n"
        f"{json.dumps(candidates, indent=2)}\n\n"
        f"Pick packets and a subagent_count <= {max_subagents}. "
        "Each path must be chosen from the candidate packets."
    )
    return llm.complete_json_schema(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model_config.manager_model,
        reasoning=model_config.manager_reasoning,
        schema_name="init_packet_plan",
        schema=schema,
    )


def _run_workers(
    *,
    llm: LLMClient,
    repo_root: Path,
    packet_plan: list[dict[str, Any]],
    inventory: MonorepoInventory,
    model_config: InitModelConfig,
    max_workers: int,
) -> list[dict[str, Any]]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "packet_id",
            "facts",
            "module_candidates",
            "entrypoints",
            "dependencies",
            "risks",
            "open_questions",
        ],
        "properties": {
            "packet_id": {"type": "string"},
            "facts": {"type": "array", "items": {"$ref": "#/$defs/evidencedRow"}},
            "module_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "paths", "confidence", "sources"],
                    "properties": {
                        "name": {"type": "string"},
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "sources": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "entrypoints": {"type": "array", "items": {"type": "string"}},
            "dependencies": {"type": "array", "items": {"$ref": "#/$defs/evidencedRow"}},
            "risks": {"type": "array", "items": {"$ref": "#/$defs/evidencedRow"}},
            "open_questions": {"type": "array", "items": {"$ref": "#/$defs/evidencedRow"}},
        },
        "$defs": {
            "evidencedRow": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "sources"],
                "properties": {
                    "statement": {"type": "string"},
                    "sources": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                },
            }
        },
    }

    available_paths = set(inventory.discovered_paths)

    def _run_one(packet: dict[str, Any]) -> dict[str, Any]:
        slice_previews = bounded_slice_previews(repo_root, packet["paths"])
        system_prompt = (
            "You are a bounded repository worker. "
            "Only report facts with direct evidence from provided file previews. "
            "Return strict JSON matching the schema."
        )
        user_prompt = (
            "Packet metadata:\n"
            f"{json.dumps(packet, indent=2)}\n\n"
            "Inventory summary:\n"
            f"{json.dumps(_inventory_summary(inventory), indent=2)}\n\n"
            "File previews:\n"
            f"{json.dumps([{'path': p.path, 'kind': p.kind, 'preview': p.preview} for p in slice_previews], indent=2)}\n\n"
            "Report facts, module candidates, entrypoints, dependencies, risks, and open questions with sources."
        )
        raw = llm.complete_json_schema(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model_config.worker_model,
            reasoning=model_config.worker_reasoning,
            schema_name="init_worker_report",
            schema=schema,
        )
        validated = validate_worker_report(raw, available_paths=available_paths)
        if validated["packet_id"] != packet["packet_id"]:
            raise ValueError(
                f"Worker packet_id mismatch: expected {packet['packet_id']!r}, got {validated['packet_id']!r}"
            )
        return validated

    reports: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_run_one, packet) for packet in packet_plan]
        for fut in futures:
            reports.append(fut.result())
    return reports


def _manager_synthesize_pages(
    *,
    llm: LLMClient,
    inventory: MonorepoInventory,
    packet_plan: list[dict[str, Any]],
    worker_reports: list[dict[str, Any]],
    model_config: InitModelConfig,
) -> dict[str, Any]:
    schema = {
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
    system_prompt = (
        "You synthesize initialization wiki drafts from repository inventory and worker reports. "
        "No unsupported claims. Include low-confidence stubs where evidence is thin. "
        "Return strict JSON only."
    )
    user_prompt = (
        "Inventory summary:\n"
        f"{json.dumps(_inventory_summary(inventory), indent=2)}\n\n"
        "Packet plan:\n"
        f"{json.dumps(packet_plan, indent=2)}\n\n"
        "Worker reports:\n"
        f"{json.dumps(worker_reports, indent=2)}\n\n"
        "Generate initial wiki pages including repository overview, monorepo map, build/test, and major modules/packages."
    )
    return llm.complete_json_schema(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model_config.manager_model,
        reasoning=model_config.manager_reasoning,
        schema_name="init_synthesis",
        schema=schema,
    )


def _build_candidate_packets(inventory: MonorepoInventory) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for rel in inventory.discovered_paths:
        parts = rel.split("/")
        key = parts[0] if parts else "."
        grouped.setdefault(key, []).append(rel)
    packets: list[dict[str, Any]] = []
    for key in sorted(grouped):
        files = sorted(grouped[key])
        if not files:
            continue
        for idx, start in enumerate(range(0, len(files), 80), start=1):
            chunk = files[start : start + 80]
            packet_id = f"{key}-{idx}"
            packets.append(
                {
                    "packet_id": packet_id,
                    "focus": f"Inspect {key} ({len(chunk)} files)",
                    "paths": chunk,
                }
            )
    manifests = inventory.classifications.get("build_manifests", [])
    if manifests:
        packets.insert(
            0,
            {
                "packet_id": "manifests-1",
                "focus": "Cross-repo build manifests and execution entrypoints",
                "paths": manifests[:80],
            },
        )
    return packets[:60]


def _inventory_summary(inventory: MonorepoInventory) -> dict[str, Any]:
    previews = [{"path": item.path, "kind": item.kind} for item in inventory.previews[:120]]
    return {
        "repo_root": inventory.repo_root,
        "totals": inventory.totals,
        "inventory_hash": inventory.inventory_hash,
        "classifications": inventory.classifications,
        "preview_catalog": previews,
    }


def _normalize_evidenced_rows(rows: Any, available_paths: set[str], *, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{field_name} must be a list")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{field_name} row must be an object")
        statement = str(row.get("statement", "")).strip()
        if not statement:
            continue
        sources = _normalize_path_list(row.get("sources", []), available_paths, f"{field_name}.sources")
        if not sources:
            raise ValueError(f"{field_name} row missing sources: {statement!r}")
        normalized.append({"statement": statement, "sources": sources})
    return normalized


def _normalize_modules(rows: Any, available_paths: set[str]) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("module_candidates must be a list")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("module_candidates row must be an object")
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        confidence = str(row.get("confidence", "low")).lower()
        if confidence not in _CONFIDENCE_LEVELS:
            confidence = "low"
        paths = _normalize_path_list(row.get("paths", []), available_paths, "module_candidates.paths")
        sources = _normalize_path_list(row.get("sources", []), available_paths, "module_candidates.sources")
        if not sources and paths:
            sources = paths[:2]
        if not sources:
            raise ValueError(f"module candidate {name!r} missing evidence sources")
        normalized.append({"name": name, "paths": paths, "sources": sources, "confidence": confidence})
    return normalized


def _normalize_path_list(value: Any, available_paths: set[str], field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    out: list[str] = []
    for item in value:
        rel = str(item).replace("\\", "/").strip()
        if not rel:
            continue
        if rel not in available_paths:
            raise ValueError(f"{field_name} references unknown path {rel!r}")
        out.append(rel)
    return out


def _normalize_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


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
    summary = str(row.get("summary", "")).strip() or "No summary was generated."
    key_facts = _normalize_string_list(row.get("key_facts", []), "key_facts")
    details = _normalize_string_list(row.get("details", []), "details")
    relationships = _normalize_string_list(row.get("relationships", []), "relationships")
    evidence_sources = _normalize_sources(row.get("sources", []), available_paths=available_paths)
    open_questions = _normalize_string_list(row.get("open_questions", []), "open_questions")
    frontmatter_sources = _normalize_frontmatter_sources(
        row.get("frontmatter_sources", []),
        available_paths=available_paths,
        fallback_sources=evidence_sources,
    )
    content = _render_page_markdown(
        category=category,
        title=title,
        summary=summary,
        key_facts=key_facts,
        details=details,
        relationships=relationships,
        evidence_sources=evidence_sources,
        open_questions=open_questions,
        confidence=confidence,
        frontmatter_sources=frontmatter_sources,
    )
    if not page_is_schema_compliant(content):
        raise ValueError(f"Generated page {category}/{title} is missing required sections")
    return GeneratedPage(
        category=category,
        title=title,
        content=content,
        confidence=confidence,
        evidence_sources=evidence_sources,
        frontmatter_sources=frontmatter_sources,
    )


def _normalize_sources(value: Any, *, available_paths: set[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("Generated page must include at least one source")
    out: list[str] = []
    for item in value:
        src = str(item).replace("\\", "/").strip()
        if not src:
            continue
        if src.startswith("inventory:"):
            out.append(src)
            continue
        if src not in available_paths:
            raise ValueError(f"Speculative source not found in inventory: {src!r}")
        out.append(src)
    if not out:
        raise ValueError("Generated page must include at least one valid source")
    return out


def _normalize_frontmatter_sources(
    value: Any, *, available_paths: set[str], fallback_sources: list[str]
) -> list[str]:
    out: list[str] = []
    if isinstance(value, list):
        for item in value:
            src = str(item).replace("\\", "/").strip()
            if src and src in available_paths:
                out.append(src)
    if not out:
        for src in fallback_sources:
            if src.startswith("inventory:"):
                continue
            if src in available_paths:
                out.append(src)
            if len(out) >= 4:
                break
    return sorted(set(out))


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
    details_lines = details or ["No additional detail yet."]
    relationship_lines = relationships or ["Related pages will be added as understanding improves."]
    key_fact_lines = key_facts or ["Evidence is limited; this page is a bootstrap stub."]
    open_question_lines = open_questions or ["What should be documented here first?"]
    lines = [f"# {title}", ""]
    if confidence == "low":
        lines.extend(["> stub", ""])
    lines.extend(
        [
            "## Summary",
            summary,
            "",
            "## Key Facts",
            *[f"- {item}" for item in key_fact_lines],
            "",
            "## Details",
            *details_lines,
            "",
            "## Relationships",
            *[f"- {item}" for item in relationship_lines],
            "",
            "## Sources",
            *[_format_source_line(item) for item in evidence_sources],
            "",
            "## Open Questions",
            *[f"- {item}" for item in open_question_lines],
            "",
        ]
    )
    body = "\n".join(lines).rstrip() + "\n"
    if category != "modules":
        return body
    frontmatter = {
        "id": _slug(title),
        "title": title,
        "sources": frontmatter_sources,
    }
    return serialize_frontmatter(frontmatter, body)


def _format_source_line(source: str) -> str:
    if source.startswith("inventory:"):
        return f"- `{source}`"
    return f"- `repo:{source}`"


def _ensure_core_pages(
    pages: list[GeneratedPage], *, inventory: MonorepoInventory, available_paths: set[str]
) -> list[GeneratedPage]:
    wanted = {
        ("concepts", "Repository Overview"): "Repository structure, ownership, and major subsystems.",
        ("concepts", "Monorepo Map"): "Monorepo package and application topology with dependency boundaries.",
        ("concepts", "Build and Test"): "Build and test commands inferred from manifests and CI definitions.",
    }
    existing = {(page.category, page.title): page for page in pages}
    for key, summary in wanted.items():
        if key in existing:
            continue
        pages.append(
            _fallback_page(
                category=key[0],
                title=key[1],
                summary=summary,
                evidence_sources=_default_sources(available_paths),
                confidence="low",
            )
        )
    return _dedupe_pages(pages)


def _ensure_module_pages(
    pages: list[GeneratedPage], *, inventory: MonorepoInventory, available_paths: set[str]
) -> list[GeneratedPage]:
    existing_modules = [page for page in pages if page.category == "modules"]
    if existing_modules:
        return _dedupe_pages(pages)
    for title in _module_title_candidates(inventory)[:8]:
        pages.append(
            _fallback_page(
                category="modules",
                title=title,
                summary=f"Bootstrap module stub for {title}.",
                evidence_sources=_module_evidence_sources(title=title, inventory=inventory)
                or _default_sources(available_paths),
                confidence="low",
            )
        )
    return _dedupe_pages(pages)


def _module_title_candidates(inventory: MonorepoInventory) -> list[str]:
    raw = list(inventory.classifications.get("apps_services", [])) + list(
        inventory.classifications.get("libraries", [])
    )
    if not raw:
        raw = [item for item in inventory.classifications.get("package_roots", []) if item != "."]
    titles: list[str] = []
    for item in raw:
        parts = [segment for segment in item.split("/") if segment]
        if not parts:
            continue
        name = parts[-1]
        title = _humanize(name)
        if title:
            titles.append(title)
    return sorted(set(titles))


def _module_evidence_sources(*, title: str, inventory: MonorepoInventory) -> list[str]:
    needle = title.lower().replace(" ", "")
    matches: list[str] = []
    for path in inventory.discovered_paths:
        compact = re.sub(r"[^a-z0-9]+", "", path.lower())
        if needle and needle in compact:
            matches.append(path)
        if len(matches) >= 4:
            break
    return matches


def _fallback_page(
    *,
    category: str,
    title: str,
    summary: str,
    evidence_sources: list[str],
    confidence: str,
) -> GeneratedPage:
    content = _render_page_markdown(
        category=category,
        title=title,
        summary=summary,
        key_facts=["Generated from inventory signals only."],
        details=["Deeper details require additional source scans."],
        relationships=["Related module/concept links will be refined after review."],
        evidence_sources=evidence_sources,
        open_questions=["What are the most important correctness and reliability concerns here?"],
        confidence=confidence,
        frontmatter_sources=[src for src in evidence_sources if not src.startswith("inventory:")][:4],
    )
    frontmatter_sources = [src for src in evidence_sources if not src.startswith("inventory:")][:4]
    return GeneratedPage(
        category=category,
        title=title,
        content=content,
        confidence=confidence,
        evidence_sources=evidence_sources,
        frontmatter_sources=frontmatter_sources,
    )


def _default_sources(available_paths: set[str]) -> list[str]:
    prioritized = sorted(
        available_paths,
        key=lambda path: (
            0 if Path(path).name in {"pyproject.toml", "package.json", "go.mod", "Cargo.toml"} else 1,
            path,
        ),
    )
    if not prioritized:
        return ["inventory:no-files-discovered"]
    return prioritized[:4]


def _normalize_roadmap(value: Any, pages: list[GeneratedPage]) -> list[str]:
    valid = {f"{page.category}/{page.title}" for page in pages}
    out: list[str] = []
    if isinstance(value, list):
        for item in value:
            key = str(item).strip()
            if key in valid:
                out.append(key)
    if not out:
        out = [f"{page.category}/{page.title}" for page in pages[:10]]
    seen: set[str] = set()
    deduped: list[str] = []
    for key in out:
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _dedupe_pages(pages: list[GeneratedPage]) -> list[GeneratedPage]:
    deduped: dict[tuple[str, str], GeneratedPage] = {}
    for page in pages:
        deduped[(page.category, page.title)] = page
    return list(deduped.values())


def _humanize(value: str) -> str:
    words = re.split(r"[-_.]+", value.strip())
    cleaned = [w for w in words if w]
    if not cleaned:
        return ""
    return " ".join(word[:1].upper() + word[1:] for word in cleaned)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-").lower()
    return cleaned or "module"
