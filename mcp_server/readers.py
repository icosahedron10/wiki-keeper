from __future__ import annotations

from .llm import LLMClient
from .source_scan import SourceFile


_SYSTEM = (
    "You are a strict technical reviewer. Be concise, cite file paths, and list "
    "specific mismatches only."
)


def run_reader_a(
    llm: LLMClient,
    *,
    article_markdown: str,
    source_files: list[SourceFile],
) -> str:
    prompt = (
        "Given this wiki article and source files, list claims in the article that "
        "are unsupported or contradicted.\n\n"
        "ARTICLE:\n"
        f"{article_markdown}\n\n"
        "SOURCES:\n"
        f"{_format_sources(source_files)}"
    )
    return llm.complete_text(
        system_prompt=_SYSTEM,
        user_prompt=prompt,
        model=llm.config.reader_model,
        reasoning=llm.config.reader_reasoning,
    )


def run_reader_b(
    llm: LLMClient,
    *,
    article_markdown: str,
    source_files: list[SourceFile],
) -> str:
    prompt = (
        "Given these source files and the wiki article, list facts present in the "
        "sources that the article misses or misstates.\n\n"
        "SOURCES:\n"
        f"{_format_sources(source_files)}\n\n"
        "ARTICLE:\n"
        f"{article_markdown}"
    )
    return llm.complete_text(
        system_prompt=_SYSTEM,
        user_prompt=prompt,
        model=llm.config.reader_model,
        reasoning=llm.config.reader_reasoning,
    )


def _format_sources(source_files: list[SourceFile]) -> str:
    parts: list[str] = []
    for sf in source_files:
        parts.append(f"### {sf.rel_path}\n{sf.content}\n")
    return "\n".join(parts)
