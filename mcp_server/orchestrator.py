from __future__ import annotations

from typing import Any

from .llm import LLMClient


def run_orchestrator(
    llm: LLMClient,
    *,
    article_markdown: str,
    reader_a: str,
    reader_b: str,
    schema_markdown: str,
) -> dict[str, Any]:
    system = (
        "You are the wiki-keeper orchestrator. Decide whether to patch the article "
        "or file audit-only. Respond with JSON only."
    )
    prompt = (
        "Using schema rules and two reader reports, decide whether to patch.\n\n"
        "Output JSON shape:\n"
        "{\n"
        '  "confidence": "high|medium|low",\n'
        '  "decision": "patch|audit_only",\n'
        '  "patch_content": "<full replacement markdown or empty string>",\n'
        '  "rationale": "<brief reason>"\n'
        "}\n\n"
        "SCHEMA:\n"
        f"{schema_markdown}\n\n"
        "ARTICLE:\n"
        f"{article_markdown}\n\n"
        "READER_A:\n"
        f"{reader_a}\n\n"
        "READER_B:\n"
        f"{reader_b}\n"
    )
    result = llm.complete_json(
        system_prompt=system,
        user_prompt=prompt,
        model=llm.config.orchestrator_model,
        reasoning=llm.config.orchestrator_reasoning,
    )
    confidence = str(result.get("confidence", "low")).lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    decision = str(result.get("decision", "audit_only")).lower()
    if decision not in {"patch", "audit_only"}:
        decision = "audit_only"
    patch_content = result.get("patch_content") or ""
    if not isinstance(patch_content, str):
        patch_content = ""
    rationale = result.get("rationale") or ""
    if not isinstance(rationale, str):
        rationale = str(rationale)
    return {
        "confidence": confidence,
        "decision": decision,
        "patch_content": patch_content,
        "rationale": rationale.strip(),
    }
