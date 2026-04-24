from __future__ import annotations

from dataclasses import dataclass, field
import re

from . import lint as lint_mod
from .frontmatter import validate_frontmatter
from .pages import has_sources_section, is_stub, list_all, parse_page_frontmatter
from .paths import (
    CATEGORIES,
    audits_dir,
    corpus_root,
    index_path,
    log_path,
    repo_root,
    roadmap_path,
    schema_path,
    state_path,
    wiki_dir,
)
from .roadmap import load_entries, resolve_entries
from .source_scan import resolve_source_globs
from .state import load as load_state
from .storage import read_text


REQUIRED_SECTIONS = (
    "Summary",
    "Key Facts",
    "Details",
    "Relationships",
    "Sources",
    "Open Questions",
)


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lint: dict | None = None

    @property
    def ok(self) -> bool:
        lint_ok = bool(self.lint and self.lint.get("ok", False))
        return not self.errors and lint_ok

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "lint": self.lint or {},
        }


def page_is_schema_compliant(content: str) -> bool:
    body = _strip_fenced_code_blocks(content)
    if missing_required_sections(body, allow_stub_sources=True):
        return False
    return is_stub(body) or has_sources_section(body)


def missing_required_sections(
    content: str, *, allow_stub_sources: bool = True
) -> list[str]:
    body = _strip_fenced_code_blocks(content)
    sections: tuple[str, ...] = REQUIRED_SECTIONS
    if allow_stub_sources and is_stub(body):
        sections = tuple(
            section for section in REQUIRED_SECTIONS if section != "Sources"
        )
    missing: list[str] = []
    for section in REQUIRED_SECTIONS:
        if section not in sections:
            continue
        if (
            re.search(rf"^##\s+{re.escape(section)}\s*$", body, flags=re.MULTILINE)
            is None
        ):
            missing.append(section)
    return missing


def _strip_fenced_code_blocks(content: str) -> str:
    lines: list[str] = []
    in_fence = False
    for line in content.splitlines():
        if re.match(r"^[ ]{0,3}```", line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return "\n".join(lines)


def run() -> ValidationReport:
    report = ValidationReport()
    _check_layout(report)
    if report.errors:
        return report

    try:
        load_state()
    except (FileNotFoundError, ValueError) as exc:
        report.errors.append(str(exc))

    entries: list[str] = []
    try:
        entries = load_entries()
    except FileNotFoundError as exc:
        report.errors.append(str(exc))
    except Exception as exc:
        report.errors.append(f"Failed to parse roadmap: {exc}")
    else:
        _, unknown = resolve_entries(entries)
        for item in unknown:
            report.errors.append(f"Roadmap entry does not resolve to a page: {item}")

    for page in list_all():
        content = read_text(page.path)
        try:
            frontmatter, body = parse_page_frontmatter(content)
        except ValueError as exc:
            report.errors.append(f"{page.rel}: invalid frontmatter: {exc}")
            continue
        for section in missing_required_sections(body, allow_stub_sources=True):
            report.errors.append(f"{page.rel}: missing required section ## {section}")

        if not is_stub(body) and not has_sources_section(body):
            report.errors.append(
                f"{page.rel}: non-stub page must include at least one ## Sources list item"
            )

        if not frontmatter:
            continue

        for err in validate_frontmatter(frontmatter):
            report.errors.append(f"{page.rel}: {err}")

        patterns = frontmatter.get("sources", [])
        if not patterns:
            continue
        if not isinstance(patterns, list) or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in patterns
        ):
            continue
        scan = resolve_source_globs(repo_root=repo_root(), patterns=patterns)
        if scan.errors:
            for err in scan.errors:
                report.errors.append(f"{page.rel}: {err}")
        if not scan.files:
            report.errors.append(f"{page.rel}: frontmatter.sources matched no files")

    try:
        report.lint = lint_mod.run().to_dict()
    except Exception as exc:
        report.errors.append(f"lint_wiki failed: {exc}")
        report.lint = {"ok": False}

    return report


def _check_layout(report: ValidationReport) -> None:
    required_dirs = [
        corpus_root(),
        wiki_dir(),
        audits_dir(),
        *(wiki_dir() / c for c in CATEGORIES),
    ]
    required_files = [
        schema_path(),
        roadmap_path(),
        state_path(),
        index_path(),
        log_path(),
    ]
    for path in required_dirs:
        if not path.is_dir():
            report.errors.append(f"Missing required directory: {path}")
    for path in required_files:
        if not path.is_file():
            report.errors.append(f"Missing required file: {path}")
