from __future__ import annotations

import difflib
from datetime import datetime, timezone
from typing import Any, Callable

from . import audits, git_delta, roadmap, state, wikilog
from .frontmatter import serialize_frontmatter
from .llm import LLMClient, require_api_key
from .orchestrator import run_orchestrator
from .pages import find_page, parse_page_frontmatter
from .paths import repo_root, schema_path
from .readers import run_reader_a, run_reader_b
from .source_scan import SourceFile, resolve_source_globs
from .storage import read_text
from .validate import page_is_schema_compliant, run as run_validate

UpdateKnowledgeFn = Callable[[str, str, str], dict[str, Any]]


def run_nightly(
    *,
    budget: int = 1,
    since: str | None = None,
    until: str | None = None,
    dry_run: bool = False,
    llm_client: LLMClient | None = None,
    update_knowledge_fn: UpdateKnowledgeFn,
) -> dict[str, Any]:
    if budget < 1:
        raise ValueError("budget must be >= 1")

    report = run_validate(require_source_matches=False)
    if not report.ok:
        raise RuntimeError(
            "Validation failed before run-nightly: " + "; ".join(report.errors)
        )

    root = repo_root()
    current_state = state.load()
    try:
        commit_range, recovery_reason = git_delta.build_range(
            repo_root=root,
            since=since,
            until=until,
            state_git=current_state.get("git", {}),
        )
    except git_delta.GitUnavailableError as exc:
        raise RuntimeError(f"Git delta discovery failed: {exc}") from exc

    if commit_range is None:
        head = git_delta.current_head(root)
        branch = git_delta.default_branch(root)
        if not dry_run:
            updated = state.record_git_run(
                current_state,
                since=since,
                until=head,
                default_branch=branch,
                changed_paths=[],
                outcome="baseline_initialized",
                audit_paths=[],
                patch_status="none",
            )
            state.save(updated)
        return {
            "budget": budget,
            "outcome": "baseline_initialized",
            "reason": recovery_reason,
            "commit_range": {
                "since": since,
                "until": head,
                "range": head,
                "default_branch": branch,
                "changed_paths": [],
            },
            "matches": [],
            "results": [],
            "dry_run": dry_run,
        }

    matches = git_delta.map_changed_paths_to_articles(commit_range.changed_paths)
    payload_base = {
        "budget": budget,
        "commit_range": commit_range.to_dict(),
        "matches": [match.to_dict() for match in matches],
        "dry_run": dry_run,
    }

    if not commit_range.changed_paths:
        if not dry_run:
            updated = state.record_git_run(
                current_state,
                since=commit_range.since,
                until=commit_range.until,
                default_branch=commit_range.default_branch,
                changed_paths=[],
                outcome="no_changes",
                audit_paths=[],
                patch_status="none",
            )
            state.save(updated)
        return {**payload_base, "outcome": "no_changes", "results": []}

    if dry_run:
        return {
            **payload_base,
            "outcome": "dry_run",
            "planned_reviews": [match.to_dict() for match in matches[:budget]],
            "skipped_matches": [match.to_dict() for match in matches[budget:]],
            "results": [],
        }

    if not matches:
        audit_path = _write_unmapped_delta_audit(commit_range)
        relative_audit = str(audit_path.relative_to(root)).replace("\\", "/")
        updated = state.record_git_run(
            current_state,
            since=commit_range.since,
            until=commit_range.until,
            default_branch=commit_range.default_branch,
            changed_paths=commit_range.changed_paths,
            outcome="audit_only",
            audit_paths=[relative_audit],
            patch_status="audit_only",
        )
        state.save(updated)
        wikilog.append("run_nightly", "audit_only", commit_range.range_expr, "no mapped articles")
        return {
            **payload_base,
            "outcome": "audit_only",
            "audit_path": relative_audit,
            "results": [],
        }

    require_api_key()
    llm = llm_client or LLMClient()
    pending_matches = _pending_matches_for_range(
        matches,
        current_state=current_state,
        commit_range=commit_range.to_dict(),
    )
    already_processed = len(matches) - len(pending_matches)
    if not pending_matches:
        updated = state.record_git_run(
            current_state,
            since=commit_range.since,
            until=commit_range.until,
            default_branch=commit_range.default_branch,
            changed_paths=commit_range.changed_paths,
            outcome="already_processed",
            audit_paths=[],
            patch_status="already_processed",
        )
        state.save(updated)
        return {
            **payload_base,
            "outcome": "already_processed",
            "results": [],
            "skipped_matches": [],
            "already_processed_matches": [match.to_dict() for match in matches],
            "patch_status": "already_processed",
        }

    results: list[dict[str, Any]] = []
    for match in pending_matches[:budget]:
        diff_files = git_delta.diff_source_files(
            root,
            since=commit_range.since or commit_range.until,
            until=commit_range.until,
            paths=match.changed_paths,
        )
        results.append(
            run_review(
                article_id=match.page_name,
                llm_client=llm,
                update_knowledge_fn=update_knowledge_fn,
                source_files=diff_files,
                source_globs=match.source_patterns,
                audit_notes=[
                    f"Git range: {commit_range.range_expr}",
                    "Review evidence is commit diff content, not full source files.",
                ],
                commit_range=commit_range.to_dict(),
                changed_paths=match.changed_paths,
            )
        )

    audit_paths = [str(item.get("audit_path", "")) for item in results if item.get("audit_path")]
    skipped_matches = pending_matches[budget:]
    patch_status = _summarize_patch_status(results, skipped=bool(skipped_matches))
    outcome = "patched" if any(item.get("outcome") == "patched" for item in results) else "audit_only"
    if skipped_matches:
        outcome = "partial"

    latest_state = state.load()
    updated = state.record_git_run(
        latest_state,
        since=commit_range.since,
        until=commit_range.until,
        default_branch=commit_range.default_branch,
        changed_paths=commit_range.changed_paths,
        outcome=outcome,
        audit_paths=audit_paths,
        patch_status=patch_status,
    )
    state.save(updated)
    return {
        **payload_base,
        "outcome": outcome,
        "results": results,
        "skipped_matches": [match.to_dict() for match in skipped_matches],
        "already_processed": already_processed,
        "patch_status": patch_status,
    }


def run_review(
    *,
    article_id: str | None,
    llm_client: LLMClient | None = None,
    update_knowledge_fn: UpdateKnowledgeFn,
    source_files: list[SourceFile] | None = None,
    source_globs: list[str] | None = None,
    audit_notes: list[str] | None = None,
    commit_range: dict[str, Any] | None = None,
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    report = run_validate(require_source_matches=source_files is None)
    if not report.ok:
        raise RuntimeError(
            "Validation failed before run_review: " + "; ".join(report.errors)
        )

    require_api_key()
    llm = llm_client or LLMClient()
    current_state = state.load()
    roadmap_entries = roadmap.load_entries()
    if not roadmap_entries and article_id is None:
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
    selected_id = _article_id_from_frontmatter(frontmatter, fallback=selected_id)
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
            source_globs=source_globs or [],
            inspected_files=[],
            reader_a="",
            reader_b="",
            confidence="low",
            decision="audit_only",
            rationale="No frontmatter sources configured.",
            diff_text="",
            commit_range=commit_range,
            changed_paths=changed_paths,
        )
        return result

    notes: list[str] = list(audit_notes or [])
    if source_files is None:
        scan = resolve_source_globs(repo_root=repo_root(), patterns=frontmatter_sources)
    else:
        scan = None

    if scan is not None and scan.errors:
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
            commit_range=commit_range,
            changed_paths=changed_paths,
        )

    if scan is not None and not scan.files:
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
            commit_range=commit_range,
            changed_paths=changed_paths,
        )

    if source_files is not None:
        inspected_source_files = source_files
    else:
        assert scan is not None
        inspected_source_files = scan.files
    if scan is not None and scan.truncated:
        notes.append("Source scan was truncated at max files/bytes limit.")

    reader_a = run_reader_a(llm, article_markdown=body, source_files=inspected_source_files)
    reader_b = run_reader_b(llm, article_markdown=body, source_files=inspected_source_files)
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
        source_globs=source_globs or frontmatter_sources,
        inspected_files=[sf.rel_path for sf in inspected_source_files],
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
        commit_range=commit_range,
        changed_paths=changed_paths,
        patch_status="applied" if patch_applied else "audit_only",
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
        "commit_range": commit_range,
        "changed_paths": changed_paths or [],
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
        difflib.unified_diff(
            old_lines, new_lines, fromfile="before", tofile="after", lineterm=""
        )
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
    commit_range: dict[str, Any] | None = None,
    changed_paths: list[str] | None = None,
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
        commit_range=commit_range,
        changed_paths=changed_paths,
        patch_status=decision,
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
        "commit_range": commit_range,
        "changed_paths": changed_paths or [],
    }


def _write_unmapped_delta_audit(commit_range: git_delta.GitRange) -> Any:
    return audits.write_audit(
        article_id="git-delta",
        article_path=f"git:{commit_range.range_expr}",
        source_globs=[],
        inspected_files=commit_range.changed_paths,
        reader_a="",
        reader_b="",
        confidence="low",
        decision="audit_only",
        rationale="No wiki article frontmatter.sources matched the changed paths.",
        diff_text="",
        notes=[
            f"Git range: {commit_range.range_expr}",
            "No article maps cleanly to this change set.",
        ],
        run_at=datetime.now(timezone.utc),
    )


def _pending_matches_for_range(
    matches: list[git_delta.ArticleMatch],
    *,
    current_state: dict[str, Any],
    commit_range: dict[str, Any],
) -> list[git_delta.ArticleMatch]:
    processed = _processed_article_ids_for_range(
        current_state,
        since=commit_range.get("since"),
        until=commit_range.get("until"),
    )
    return [match for match in matches if match.article_id not in processed]


def _processed_article_ids_for_range(
    current_state: dict[str, Any],
    *,
    since: Any,
    until: Any,
) -> set[str]:
    processed: set[str] = set()
    history = current_state.get("history", [])
    if not isinstance(history, list):
        return processed
    for row in history:
        if not isinstance(row, dict):
            continue
        if row.get("outcome") == "error":
            continue
        row_range = row.get("commit_range")
        if not isinstance(row_range, dict):
            continue
        if row_range.get("since") != since or row_range.get("until") != until:
            continue
        article_id = row.get("article_id")
        if isinstance(article_id, str) and article_id.strip():
            processed.add(article_id.strip())
    return processed


def _article_id_from_frontmatter(frontmatter: dict[str, Any] | None, *, fallback: str) -> str:
    if frontmatter and isinstance(frontmatter.get("id"), str) and frontmatter["id"].strip():
        return frontmatter["id"].strip()
    return fallback


def _summarize_patch_status(results: list[dict[str, Any]], *, skipped: bool) -> str:
    if skipped:
        return "partial"
    if any(item.get("outcome") == "patched" for item in results):
        return "patched"
    if results:
        return "audit_only"
    return "none"
