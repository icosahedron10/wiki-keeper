from __future__ import annotations

import asyncio
import difflib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from ..bootstrap.source_scan import SourceFile
from ..core.frontmatter import serialize_frontmatter
from ..core.pages import find_page, parse_page_frontmatter
from ..core.paths import repo_root, schema_path
from ..core.storage import read_text
from ..integrations import git_delta
from ..integrations.llm import AsyncOpenAIClient, complete_json_schema, create_openai_client, nightly_model, require_api_key
from . import audits, state, wikilog
from .validate import page_is_schema_compliant, run as run_validate

UpdateKnowledgeFn = Callable[[str, str, str], dict[str, Any]]


async def run_nightly_async(
    *,
    since: str | None = None,
    until: str | None = None,
    dry_run: bool = False,
    client: AsyncOpenAIClient | None = None,
    update_knowledge_fn: UpdateKnowledgeFn,
    model: str | None = None,
) -> dict[str, Any]:
    report = run_validate(require_source_matches=False)
    if not report.ok:
        raise RuntimeError("Validation failed before run-nightly: " + "; ".join(report.errors))

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

    selected_model = model or nightly_model()
    if commit_range is None:
        head = git_delta.current_head(root)
        branch = git_delta.default_branch(root)
        if not dry_run:
            state.save(
                state.record_git_run(
                    current_state,
                    since=since,
                    until=head,
                    default_branch=branch,
                    changed_paths=[],
                    outcome="baseline_initialized",
                    audit_paths=[],
                    patch_status="none",
                )
            )
        return {
            "model": selected_model,
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
            "article_decisions": [],
            "audit_paths": [],
            "patch_status": "none",
            "dry_run": dry_run,
        }

    matches = git_delta.map_changed_paths_to_articles(commit_range.changed_paths)
    payload_base = {
        "model": selected_model,
        "commit_range": commit_range.to_dict(),
        "matches": [match.to_dict() for match in matches],
        "dry_run": dry_run,
    }

    if not commit_range.changed_paths:
        if not dry_run:
            state.save(
                state.record_git_run(
                    current_state,
                    since=commit_range.since,
                    until=commit_range.until,
                    default_branch=commit_range.default_branch,
                    changed_paths=[],
                    outcome="no_changes",
                    audit_paths=[],
                    patch_status="none",
                )
            )
        return {**payload_base, "outcome": "no_changes", "article_decisions": [], "audit_paths": [], "patch_status": "none"}

    if dry_run:
        return {
            **payload_base,
            "outcome": "dry_run",
            "article_decisions": [],
            "audit_paths": [],
            "patch_status": "none",
        }

    if not matches:
        audit_path = _write_unmapped_delta_audit(commit_range)
        relative_audit = str(audit_path.relative_to(root)).replace("\\", "/")
        state.save(
            state.record_git_run(
                current_state,
                since=commit_range.since,
                until=commit_range.until,
                default_branch=commit_range.default_branch,
                changed_paths=commit_range.changed_paths,
                outcome="audit_only",
                audit_paths=[relative_audit],
                patch_status="audit_only",
            )
        )
        wikilog.append("run_nightly", "audit_only", commit_range.range_expr, "no mapped articles")
        return {
            **payload_base,
            "outcome": "audit_only",
            "article_decisions": [],
            "audit_paths": [relative_audit],
            "patch_status": "audit_only",
        }

    pending_matches = _pending_matches_for_range(matches, current_state=current_state, commit_range=commit_range.to_dict())
    if not pending_matches:
        state.save(
            state.record_git_run(
                current_state,
                since=commit_range.since,
                until=commit_range.until,
                default_branch=commit_range.default_branch,
                changed_paths=commit_range.changed_paths,
                outcome="already_processed",
                audit_paths=[],
                patch_status="already_processed",
            )
        )
        return {
            **payload_base,
            "outcome": "already_processed",
            "article_decisions": [],
            "audit_paths": [],
            "patch_status": "already_processed",
        }

    require_api_key("run-nightly")
    openai_client = client or create_openai_client()
    review_input = _build_review_input(root=root, commit_range=commit_range, matches=pending_matches)
    payload = await complete_json_schema(
        openai_client,
        model=selected_model,
        instructions=(
            "You are wiki-keeper nightly review. Review all provided article/change matches in one pass. "
            "Return strict JSON. Only patch when the source diff clearly supports the complete replacement body."
        ),
        input_text=review_input,
        schema_name="wiki_keeper_nightly",
        schema=_nightly_schema(),
    )
    decisions = _normalize_decisions(payload, allowed_article_ids={match.article_id for match in pending_matches})
    results = [_apply_decision(decision, commit_range=commit_range, update_knowledge_fn=update_knowledge_fn) for decision in decisions]
    audit_paths = [item["audit_path"] for item in results if item.get("audit_path")]
    patch_status = _summarize_patch_status(results)
    outcome = "patched" if any(item.get("outcome") == "patched" for item in results) else "audit_only"
    latest_state = state.load()
    state.save(
        state.record_git_run(
            latest_state,
            since=commit_range.since,
            until=commit_range.until,
            default_branch=commit_range.default_branch,
            changed_paths=commit_range.changed_paths,
            outcome=outcome,
            audit_paths=audit_paths,
            patch_status=patch_status,
        )
    )
    return {
        **payload_base,
        "outcome": outcome,
        "article_decisions": results,
        "audit_paths": audit_paths,
        "patch_status": patch_status,
    }


def run_nightly(
    *,
    since: str | None = None,
    until: str | None = None,
    dry_run: bool = False,
    client: AsyncOpenAIClient | None = None,
    update_knowledge_fn: UpdateKnowledgeFn,
    model: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        run_nightly_async(
            since=since,
            until=until,
            dry_run=dry_run,
            client=client,
            update_knowledge_fn=update_knowledge_fn,
            model=model,
        )
    )


async def run_review_async(
    *,
    client: AsyncOpenAIClient | None = None,
    update_knowledge_fn: UpdateKnowledgeFn,
    model: str | None = None,
) -> dict[str, Any]:
    return await run_nightly_async(client=client, update_knowledge_fn=update_knowledge_fn, model=model)


def run_review(
    *,
    client: AsyncOpenAIClient | None = None,
    update_knowledge_fn: UpdateKnowledgeFn,
    model: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(run_review_async(client=client, update_knowledge_fn=update_knowledge_fn, model=model))


def _nightly_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["article_decisions"],
        "properties": {
            "article_decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["article_id", "decision", "confidence", "rationale", "patch_content", "audit_notes"],
                    "properties": {
                        "article_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["patch", "audit_only"]},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "rationale": {"type": "string"},
                        "patch_content": {"type": "string"},
                        "audit_notes": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }


def _build_review_input(*, root: Any, commit_range: git_delta.GitRange, matches: list[git_delta.ArticleMatch]) -> str:
    articles: list[dict[str, Any]] = []
    for match in matches:
        ref = find_page(match.page_name)
        if ref is None:
            continue
        content = read_text(ref.path)
        frontmatter, body = parse_page_frontmatter(content)
        diff_files = git_delta.diff_source_files(
            root,
            since=commit_range.since or commit_range.until,
            until=commit_range.until,
            paths=match.changed_paths,
        )
        articles.append(
            {
                "article_id": match.article_id,
                "page_name": match.page_name,
                "page_path": match.page_path,
                "frontmatter": frontmatter,
                "article_body": body,
                "source_patterns": match.source_patterns,
                "changed_paths": match.changed_paths,
                "diffs": [_source_to_dict(item) for item in diff_files],
            }
        )
    return (
        "Schema rules:\n"
        f"{read_text(schema_path())}\n\n"
        "Commit range:\n"
        f"{json.dumps(commit_range.to_dict(), indent=2)}\n\n"
        "Matched articles and source diffs:\n"
        f"{json.dumps(articles, indent=2)}"
    )


def _source_to_dict(source: SourceFile) -> dict[str, Any]:
    return {"path": source.rel_path, "content": source.content, "size_bytes": source.size_bytes}


def _normalize_decisions(payload: dict[str, Any], *, allowed_article_ids: set[str]) -> list[dict[str, Any]]:
    rows = payload.get("article_decisions")
    if not isinstance(rows, list):
        raise ValueError("Nightly payload must include article_decisions[]")
    decisions: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Nightly decision must be an object")
        article_id = str(row.get("article_id", "")).strip()
        if article_id not in allowed_article_ids:
            raise ValueError(f"Nightly decision references unknown article_id {article_id!r}")
        decision = str(row.get("decision", "audit_only")).lower()
        confidence = str(row.get("confidence", "low")).lower()
        decisions.append(
            {
                "article_id": article_id,
                "decision": decision if decision in {"patch", "audit_only"} else "audit_only",
                "confidence": confidence if confidence in {"high", "medium", "low"} else "low",
                "rationale": str(row.get("rationale", "")).strip(),
                "patch_content": row.get("patch_content") if isinstance(row.get("patch_content"), str) else "",
                "audit_notes": [str(item).strip() for item in row.get("audit_notes", []) if str(item).strip()]
                if isinstance(row.get("audit_notes"), list)
                else [],
            }
        )
    return decisions


def _apply_decision(
    decision: dict[str, Any],
    *,
    commit_range: git_delta.GitRange,
    update_knowledge_fn: UpdateKnowledgeFn,
) -> dict[str, Any]:
    match_ref = _find_article_by_id(decision["article_id"])
    if match_ref is None:
        raise ValueError(f"Article {decision['article_id']!r} was not found")
    ref, frontmatter, body = match_ref
    patch_content = decision["patch_content"]
    notes = list(decision["audit_notes"])
    outcome = "audit_only"
    patch_applied = False
    if decision["decision"] == "patch" and decision["confidence"] == "high":
        if page_is_schema_compliant(patch_content):
            update_knowledge_fn(f"{ref.category}/{ref.title}", serialize_frontmatter(frontmatter, patch_content), "replace")
            outcome = "patched"
            patch_applied = True
        else:
            notes.append("Patch rejected: schema-required sections missing.")
    diff_text = _diff(body, patch_content) if patch_applied else ""
    source_globs = []
    if frontmatter and isinstance(frontmatter.get("sources"), list):
        source_globs = [str(item) for item in frontmatter["sources"]]
    audit_path = audits.write_audit(
        article_id=decision["article_id"],
        article_path=ref.rel,
        source_globs=source_globs,
        inspected_files=commit_range.changed_paths,
        reader_a="",
        reader_b="",
        confidence=decision["confidence"],
        decision="patch" if patch_applied else "audit_only",
        rationale=decision["rationale"],
        diff_text=diff_text,
        notes=notes,
    )
    relative_audit = str(audit_path.relative_to(repo_root())).replace("\\", "/")
    wikilog.append("run_nightly", outcome, ref.rel)
    return {
        "article_id": decision["article_id"],
        "page": ref.rel,
        "outcome": outcome,
        "audit_path": relative_audit,
        "confidence": decision["confidence"],
        "decision": "patch" if patch_applied else "audit_only",
        "rationale": decision["rationale"],
    }


def _find_article_by_id(article_id: str) -> tuple[Any, dict[str, Any] | None, str] | None:
    ref = find_page(article_id)
    if ref is not None:
        frontmatter, body = parse_page_frontmatter(read_text(ref.path))
        return ref, frontmatter, body
    from ..core.pages import list_all

    for page in list_all():
        frontmatter, body = parse_page_frontmatter(read_text(page.path))
        if frontmatter and frontmatter.get("id") == article_id:
            return page, frontmatter, body
    return None


def _diff(old: str, new: str) -> str:
    return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile="before", tofile="after", lineterm=""))


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
        notes=[f"Git range: {commit_range.range_expr}", "No article maps cleanly to this change set."],
        run_at=datetime.now(timezone.utc),
    )


def _pending_matches_for_range(
    matches: list[git_delta.ArticleMatch],
    *,
    current_state: dict[str, Any],
    commit_range: dict[str, Any],
) -> list[git_delta.ArticleMatch]:
    processed = _processed_article_ids_for_range(current_state, since=commit_range.get("since"), until=commit_range.get("until"))
    return [match for match in matches if match.article_id not in processed]


def _processed_article_ids_for_range(current_state: dict[str, Any], *, since: Any, until: Any) -> set[str]:
    processed: set[str] = set()
    history = current_state.get("history", [])
    if not isinstance(history, list):
        return processed
    for row in history:
        if not isinstance(row, dict) or row.get("outcome") == "error":
            continue
        row_range = row.get("commit_range")
        if not isinstance(row_range, dict) or row_range.get("since") != since or row_range.get("until") != until:
            continue
        article_id = row.get("article_id")
        if isinstance(article_id, str) and article_id.strip():
            processed.add(article_id.strip())
    return processed


def _summarize_patch_status(results: list[dict[str, Any]]) -> str:
    if any(item.get("outcome") == "patched" for item in results):
        return "patched"
    if results:
        return "audit_only"
    return "none"
