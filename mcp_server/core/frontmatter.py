from __future__ import annotations

from typing import Any

import yaml


def parse_frontmatter(content: str) -> tuple[dict[str, Any] | None, str]:
    """Parse optional leading YAML frontmatter from markdown."""
    if not content.startswith("---"):
        return None, content

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, content

    end_index: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_index = i
            break
    if end_index is None:
        raise ValueError("Unterminated YAML frontmatter block")

    raw = "".join(lines[1:end_index])
    body = "".join(lines[end_index + 1 :])
    try:
        data = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML frontmatter: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("Frontmatter must decode to a mapping")
    return data, body


def serialize_frontmatter(
    frontmatter: dict[str, Any] | None,
    body: str,
) -> str:
    if not frontmatter:
        return body
    rendered = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    if body and not body.startswith("\n"):
        body = "\n" + body
    return f"---\n{rendered}\n---{body}"


def validate_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(frontmatter, dict):
        return ["Frontmatter must be a mapping"]

    if "id" in frontmatter and (
        not isinstance(frontmatter["id"], str) or not frontmatter["id"].strip()
    ):
        errors.append("frontmatter.id must be a non-empty string")

    if "title" in frontmatter and (
        not isinstance(frontmatter["title"], str)
        or not frontmatter["title"].strip()
    ):
        errors.append("frontmatter.title must be a non-empty string")

    if "sources" in frontmatter:
        sources = frontmatter["sources"]
        if not isinstance(sources, list):
            errors.append("frontmatter.sources must be a list of glob strings")
        else:
            for idx, item in enumerate(sources):
                if not isinstance(item, str) or not item.strip():
                    errors.append(
                        f"frontmatter.sources[{idx}] must be a non-empty string"
                    )
    return errors
