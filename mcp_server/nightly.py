from __future__ import annotations

import difflib
from datetime import datetime, timezone
from typing import Any, Callable

from . import audits, roadmap, state, wikilog
from .frontmatter import serialize_frontmatter
from .llm import LLMClient, require_api_key
from .orchestrator import run_orchestrator
from .pages import find_page, parse_page_frontmatter
from .paths import repo_root, schema_path
from .readers import run_reader_a, run_reader_b
from .source_scan import resolve_source_globs
from .storage import read_text
from .validate import page_is_schema_compliant, run as run_validate

UpdateKnowledgeFn = Callable[[str, str, str], dict[str, Any]]


def run_nightly(
    *,
    budget: int = 1,
    llm_client: LLMClient | None = None,
    update_knowledge_fn: UpdateKnowledgeFn,
) -> dict[str, Any]:
    if budget < 1:
        raise ValueError("budget must be >= 1")
    llm = llm_client or LLMClient()
    results: list[dict[str, Any]] = []
    for _ in range(budget):
        results.append(
            run_review(
                article_id=None,
                llm_client=llm,
                update_knowledge_fn=update_knowledge_fn,
            )
        )
    return {"budget": budget, "results": results}


def run_review(
    *,
    article_id: str | None,
    llm_client: LLMClient | None = None,
    update_knowledge_fn: UpdateKnowledgeFn,
) -> dict[str, Any]:
    report = run_validate()
    if not report.ok:
        raise RuntimeError(
            "Validation failed before run_review: " + "; ".join(report.errors)
        )

    require_api_key()
    llm = llm_client or LLMClient()
    current_state = state.load()
    roadmap_entries = roadmap.load_entries()
    if not roadmap_entries:
        return {"outcome": "skipped", "reason": "roadmap empty"}

    selected = _pick_article(
        explicit_article_id=article_id,
        entries=roadmap_entries,
        current_state=current_state,
    )
    if selected is None:
        return {"outcome": "skipped", "reason": "no resolvable article"}
    selected_index, selected_id, ref = selected

    content = read_text(ref.path)
    frontmatter, body = parse_page_frontmatter(content)
    frontmatter_sources = []
    if frontmatter and isinstance(frontmatter.get("sources"), list):
        frontmatter_sources = [str(item) for item in frontmatter["sources"]]

    if not frontmatter_sources:
        result = _finalize_without_patch(
            current_state=current_state,
            selected_index=selected_index,
            selected_id=selected_id,
            ref_rel=ref.rel,
            outcome="skipped",
            notes=["Article has no frontmatter.sources; nightly review skipped."],
            source_globs=[],
            inspected_files=[],
            reader_a="",
            reader_b="",
            confidence="low",
            decision="audit_only",
            rationale="No frontmatter sources configured.",
            diff_text="",
        )
        return result

    scan = resolve_source_globs(repo_root=repo_root(), patterns=frontmatter_sources)
    if scan.errors:
        return _finalize_without_patch(
            current_state=current_state,
            selected_index=selected_index,
            selected_id=selected_id,
            ref_rel=ref.rel,
            outcome="error",
            notes=scan.errors,
            source_globs=frontmatter_sources,
            inspected_files=[f.rel_path for f in scan.files],
            reader_a="",
            reader_b="",
            confidence="low",
            decision="audit_only",
            rationale="Source glob resolution failed.",
            diff_text="",
        )

    if not scan.files:
        return _finalize_without_patch(
            current_state=current_state,
            selected_index=selected_index,
            selected_id=selected_id,
            ref_rel=ref.rel,
            outcome="error",
            notes=["No source files matched frontmatter.sources"],
            source_globs=frontmatter_sources,
            inspected_files=[],
            reader_a="",
            reader_b="",
            confidence="low",
            decision="audit_only",
            rationale="No source files available.",
            diff_text="",
        )

    notes: list[str] = []
    if scan.truncated:
        notes.append("Source scan was truncated at max files/bytes limit.")

    reader_a = run_reader_a(llm, article_markdown=body, source_files=scan.files)
    reader_b = run_reader_b(llm, article_markdown=body, source_files=scan.files)
    decision = run_orchestrator(
        llm,
        article_markdown=body,
        reader_a=reader_a,
        reader_b=reader_b,
        schema_markdown=read_text(schema_path()),
    )

    confidence = decision["confidence"]
    action = decision["decision"]
    patch_content = decision["patch_content"] or ""
    rationale = decision["rationale"]
    diff_text = _diff(body, patch_content) if patch_content else ""

    outcome = "audit_only"
    patch_applied = False
    if action == "patch" and confidence == "high":
        if page_is_schema_compliant(patch_content):
            full_patch_content = serialize_frontmatter(frontmatter, patch_content)
            update_knowledge_fn(
                f"{ref.category}/{ref.title}",
                full_patch_content,
                "replace",
            )
            outcome = "patched"
            patch_applied = True
        else:
            notes.append("Patch rejected: schema-required sections missing.")

    audit_path = audits.write_audit(
        article_id=selected_id,
        article_path=ref.rel,
        source_globs=frontmatter_sources,
        inspected_files=[sf.rel_path for sf in scan.files],
        reader_a=reader_a,
        reader_b=reader_b,
        confidence=confidence,
        decision=action if patch_applied else "audit_only",
        rationale=rationale,
        diff_text=diff_text if patch_applied else "",
        notes=notes,
    )
    relative_audit = str(audit_path.relative_to(repo_root())).replace("\\", "/")
    updated_state = state.record_run(
        current_state,
        article_id=selected_id,
        index=selected_index,
        outcome=outcome,
        audit_path=relative_audit,
    )
    state.save(updated_state)
    wikilog.append("run_review", outcome, ref.rel)
    return {
        "article_id": selected_id,
        "page": ref.rel,
        "outcome": outcome,
        "audit_path": relative_audit,
        "confidence": confidence,
        "decision": action if patch_applied else "audit_only",
    }


def _pick_article(
    *,
    explicit_article_id: str | None,
    entries: list[str],
    current_state: dict[str, Any],
) -> tuple[int, str, Any] | None:
    if explicit_article_id:
        ref = find_page(explicit_article_id)
        if ref is None:
            raise ValueError(f"Article {explicit_article_id!r} was not found")
        try:
            idx = entries.index(explicit_article_id)
        except ValueError:
            idx = -1
        return idx, explicit_article_id, ref

    cursor = current_state.get("cursor", {})
    cursor_index = int(cursor.get("index", -1))
    next_item = roadmap.next_entry(entries, cursor_index)
    if next_item is None:
        return None
    index, article_id = next_item
    ref = find_page(article_id)
    if ref is None:
        raise ValueError(
            f"Roadmap entry {article_id!r} does not resolve to an article"
        )
    return index, article_id, ref


def _diff(old: str, new: str) -> str:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    return "\n".join(
        difflib.unified_diff(old_lines, new_lines, fromfile="before", tofile="after", lineterm="")
    )


def _finalize_without_patch(
    *,
    current_state: dict[str, Any],
    selected_index: int,
    selected_id: str,
    ref_rel: str,
    outcome: str,
    notes: list[str],
    source_globs: list[str],
    inspected_files: list[str],
    reader_a: str,
    reader_b: str,
    confidence: str,
    decision: str,
    rationale: str,
    diff_text: str,
) -> dict[str, Any]:
    audit_path = audits.write_audit(
        article_id=selected_id,
        article_path=ref_rel,
        source_globs=source_globs,
        inspected_files=inspected_files,
        reader_a=reader_a,
        reader_b=reader_b,
        confidence=confidence,
        decision=decision,
        rationale=rationale,
        diff_text=diff_text,
        notes=notes,
        run_at=datetime.now(timezone.utc),
    )
    relative_audit = str(audit_path.relative_to(repo_root())).replace("\\", "/")
    updated_state = state.record_run(
        current_state,
        article_id=selected_id,
        index=selected_index,
        outcome=outcome,
        audit_path=relative_audit,
    )
    state.save(updated_state)
    wikilog.append("run_review", outcome, ref_rel, "audit-only path")
    return {
        "article_id": selected_id,
        "page": ref_rel,
        "outcome": outcome,
        "audit_path": relative_audit,
        "confidence": confidence,
        "decision": decision,
    }
