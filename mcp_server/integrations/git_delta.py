from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..bootstrap.source_scan import SourceFile
from ..core.pages import PageRef, list_all, parse_page_frontmatter
from ..core.storage import read_text


@dataclass(frozen=True)
class GitRange:
    since: str | None
    until: str
    default_branch: str | None
    changed_paths: list[str]

    @property
    def range_expr(self) -> str:
        if not self.since:
            return self.until
        return f"{self.since}..{self.until}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "since": self.since,
            "until": self.until,
            "range": self.range_expr,
            "default_branch": self.default_branch,
            "changed_paths": list(self.changed_paths),
        }


@dataclass(frozen=True)
class ArticleMatch:
    article_id: str
    page_name: str
    page_path: str
    source_patterns: list[str]
    changed_paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "page_name": self.page_name,
            "page_path": self.page_path,
            "source_patterns": list(self.source_patterns),
            "changed_paths": list(self.changed_paths),
        }


class GitUnavailableError(RuntimeError):
    pass


def current_head(repo_root: Path) -> str | None:
    return _run_git(["rev-parse", "HEAD"], repo_root, check=False)


def default_branch(repo_root: Path) -> str | None:
    symbolic = _run_git(
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        repo_root,
        check=False,
    )
    if symbolic:
        return symbolic.removeprefix("origin/")
    return _run_git(["branch", "--show-current"], repo_root, check=False)


def commit_exists(repo_root: Path, rev: str | None) -> bool:
    if not rev:
        return False
    return _run_git(["cat-file", "-e", f"{rev}^{{commit}}"], repo_root, check=False) is not None


def build_range(
    *,
    repo_root: Path,
    since: str | None,
    until: str | None,
    state_git: dict[str, Any] | None = None,
) -> tuple[GitRange | None, str | None]:
    """Resolve the commit range to inspect.

    Returns (range, recovery_reason). A None range means the repository can only
    be baselined, usually because no prior commit cursor exists.
    """
    head = until or current_head(repo_root)
    if not head:
        raise GitUnavailableError(f"{repo_root} is not a git repository")
    head = _resolve_commit(repo_root, head)

    git_state = state_git or {}
    baseline = since or _clean_str(git_state.get("last_processed_commit"))
    if not baseline:
        return None, "missing_baseline"

    if not commit_exists(repo_root, baseline):
        return None, f"missing_baseline_commit:{baseline}"

    baseline = _resolve_commit(repo_root, baseline)
    paths = changed_paths(repo_root, since=baseline, until=head)
    return (
        GitRange(
            since=baseline,
            until=head,
            default_branch=default_branch(repo_root),
            changed_paths=paths,
        ),
        None,
    )


def changed_paths(repo_root: Path, *, since: str, until: str) -> list[str]:
    output = _run_git(
        ["diff", "--name-only", "--diff-filter=ACMRTD", f"{since}..{until}", "--"],
        repo_root,
    )
    assert output is not None
    paths = []
    for line in output.splitlines():
        rel = line.strip().replace("\\", "/")
        if rel:
            paths.append(rel)
    return sorted(set(paths))


def diff_source_files(
    repo_root: Path,
    *,
    since: str,
    until: str,
    paths: list[str],
    max_bytes: int = 500_000,
) -> list[SourceFile]:
    files: list[SourceFile] = []
    total = 0
    for rel in paths:
        diff = _run_git(
            ["diff", "--unified=80", f"{since}..{until}", "--", rel],
            repo_root,
            check=False,
        )
        if not diff:
            diff = f"(No textual diff available for {rel}.)\n"
        raw = diff.encode("utf-8", errors="replace")
        if total + len(raw) > max_bytes:
            files.append(
                SourceFile(
                    rel_path=rel,
                    content="(Diff omitted because the nightly diff budget was reached.)\n",
                    size_bytes=0,
                )
            )
            break
        total += len(raw)
        files.append(
            SourceFile(rel_path=rel, content=diff, size_bytes=len(raw))
        )
    return files


def map_changed_paths_to_articles(changed: list[str]) -> list[ArticleMatch]:
    changed_set = [path.replace("\\", "/") for path in changed]
    matches: list[ArticleMatch] = []
    for page in list_all():
        frontmatter = _safe_frontmatter(page)
        if not frontmatter:
            continue
        raw_patterns = frontmatter.get("sources")
        if not isinstance(raw_patterns, list):
            continue
        patterns = [str(item).replace("\\", "/").strip() for item in raw_patterns if str(item).strip()]
        if not patterns:
            continue
        page_matches = sorted(
            {
                rel
                for rel in changed_set
                if any(_path_matches_pattern(rel, pattern) for pattern in patterns)
            }
        )
        if not page_matches:
            continue
        article_id = _article_id(frontmatter, page)
        matches.append(
            ArticleMatch(
                article_id=article_id,
                page_name=f"{page.category}/{page.title}",
                page_path=page.rel,
                source_patterns=patterns,
                changed_paths=page_matches,
            )
        )
    return sorted(matches, key=lambda item: item.page_name)


def _safe_frontmatter(page: PageRef) -> dict[str, Any] | None:
    try:
        frontmatter, _ = parse_page_frontmatter(read_text(page.path))
    except ValueError:
        return None
    return frontmatter


def _article_id(frontmatter: dict[str, Any], page: PageRef) -> str:
    raw = frontmatter.get("id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return f"{page.category}/{page.title}"


def _path_matches_pattern(path: str, pattern: str) -> bool:
    clean_pattern = pattern.strip().replace("\\", "/")
    if not clean_pattern:
        return False
    if fnmatch.fnmatchcase(path, clean_pattern):
        return True
    if clean_pattern.endswith("/**"):
        return path.startswith(clean_pattern[:-3].rstrip("/") + "/")
    if clean_pattern.endswith("/"):
        return path.startswith(clean_pattern)
    return False


def _resolve_commit(repo_root: Path, rev: str) -> str:
    output = _run_git(["rev-parse", "--verify", f"{rev}^{{commit}}"], repo_root)
    assert output is not None
    return output


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _run_git(args: list[str], repo_root: Path, *, check: bool = True) -> str | None:
    command = ["git", *args]
    try:
        proc = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        if check:
            raise GitUnavailableError("git executable is not available") from exc
        return None
    if proc.returncode != 0:
        if check:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise GitUnavailableError(detail or f"git command failed: {' '.join(command)}")
        return None
    return (proc.stdout or "").strip()
