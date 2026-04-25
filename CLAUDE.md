# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Install dev environment:

```sh
python -m pip install -e ".[dev]"
```

Lint / type-check / test / build (the same set CI runs):

```sh
python -m ruff check .
python -m mypy mcp_server --exclude "mcp_server/tests"
python -m pytest
python -m build
```

Run a single test:

```sh
python -m pytest mcp_server/tests/test_nightly.py::test_name
```

Note: `pyproject.toml` pins `--basetemp=.pytest_tmp` because Windows `%TEMP%` frequently denies `scandir`. Do not remove that addopt.

Exercise the CLI locally against this repo's own corpus:

```sh
wiki-keeper mcp                                    # stdio MCP server
wiki-keeper init --repo . --offline --dry-run      # deterministic bootstrap (safe)
wiki-keeper validate --repo .
wiki-keeper run-nightly --repo . --budget 1 --dry-run
wiki-keeper tools --repo . list                    # debug surface for base tools
```

`run-nightly`, `run_review`, and `init --online` require `OPENAI_API_KEY`. Everything else is deterministic and offline.

CI matrix is Python 3.10/3.11/3.12 × Ubuntu/Windows (`.github/workflows/ci.yml`). It also builds a wheel and smoke-installs it in a clean venv — breakage here usually means a missing package in `[tool.setuptools.packages.find]` or a new non-code asset that needs `include-package-data`.

## Architecture

### Two surfaces, one core

The package is `mcp_server/`. It exposes the same set of tools through two front-ends:

- **MCP server** (`mcp_server/app/server.py`) — stdio JSON-RPC for agents. Tool list is declared statically in `_TOOLS`.
- **CLI** (`mcp_server/app/cli.py`) — `wiki-keeper <cmd>`, entrypoint `mcp_server.app.cli:main`. `wiki-keeper tools …` is the scripting mirror of the MCP tools.

Both dispatch into `mcp_server/app/tools.py`, which is the canonical implementation of every user-visible operation. When adding a tool, wire it in `tools.py` first, then register it in both `server.py._TOOLS` and `cli.py._parse_args`.

### Corpus layout and root discovery

Wiki data lives under `.wiki-keeper/` in the **host repo**, not this package. This repo dogfoods its own corpus.

`paths.repo_root()` resolves the host root in this priority order:
1. `WIKI_KEEPER_ROOT` env var (set automatically by any CLI command via `--repo`).
2. Walk up from `mcp_server/` until a directory containing `.wiki-keeper/schema.md` + `.wiki-keeper/wiki/` is found.

If you add code that reads corpus files, always go through `paths.py` helpers (`corpus_root()`, `wiki_dir()`, `schema_path()`, etc.) — never hardcode `.wiki-keeper/…`. Use `paths.safe_resolve()` for any path derived from user/LLM input; it rejects traversal out of the base directory.

### The commit-driven nightly pipeline

`nightly.run_nightly` is the non-trivial orchestration. It is driven by git deltas, not timestamps:

1. `validate.run()` must pass before anything else.
2. `git_delta.build_range()` computes `state.git.last_processed_commit..HEAD` from `.wiki-keeper/state.json`.
3. `source_scan.resolve_source_globs()` maps changed paths to articles via each page's frontmatter `sources:` glob list.
4. Unmapped deltas → audit-only notes in `.wiki-keeper/audits/YYYY-MM-DD/`.
5. Mapped articles → two reader passes (`readers.run_reader_a`, `run_reader_b`) then a strict-JSON-schema `orchestrator.run_orchestrator` call.
6. Patches are applied **only** when orchestrator confidence is `high`, by calling back into `tools.update_knowledge` (injected as `update_knowledge_fn` — keeps `nightly` decoupled from its caller's tool invocation path).
7. `state.record_git_run()` advances the cursor and appends to `git.runs[]`.

`run_review` reuses the same validation/audit/patch machinery for a single article — edit both if you change the review contract.

### LLM call conventions

All model calls go through `llm.LLMClient` (OpenAI Responses API). Defaults are nano-first for V1 nightly work. Model/reasoning are overridable per-role via `WIKI_KEEPER_*` env vars documented in the README. Orchestrator is strict-schema; reader calls are free-form. If you add a new model-touching tool, route it through `LLMClient` and `require_api_key()` rather than calling OpenAI directly.

### State and atomicity

`storage.atomic_write` + `storage.exclusive_lock` are mandatory for any write into `.wiki-keeper/` — nightly and manual `update_knowledge` calls must be safe against concurrent runs. `state.json` shape (`git.last_processed_commit`, `git.last_seen_commit`, `git.default_branch`, `git.runs[]`) is load-bearing for the delta computation; migrations need a bump in `state.py`.

### Deterministic vs model-driven boundary

The base tools (`get_page`, `list_pages`, `query_wiki`, `update_knowledge`, `rebuild_index`, `lint_wiki`) never call an LLM. Keep it that way — agents rely on them being reproducible. The model-driven surface is fenced off in `nightly.py`, `orchestrator.py`, `readers.py`, and `init_bootstrap.py` (online mode only).

`ingest_source` / `propose_ingest` are deliberately out of V1. Don't reintroduce them without a plan — the commit-driven path is the supported ingestion mechanism.

## Conventions

- Ruff config (`pyproject.toml`) only selects `E9,F63,F7,F82` — parse/logic errors, not style. Don't turn on broader rule sets as part of an unrelated change.
- Mypy runs with `check_untyped_defs`, `warn_unused_ignores`, `no_implicit_optional`. Tests are excluded; production code is not.
- `from __future__ import annotations` at the top of every module in `mcp_server/`.
