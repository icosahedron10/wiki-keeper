# Handoff: wiki-keeper V1 Productionization

Date: 2026-04-24

## Status

The PLAN.md V1 work has been implemented, code-reviewed, and verified in the
working tree. The repo now has a commit-driven nightly path, hardened corpus
validation, GitHub deployment assets, release metadata, and expanded tests.

`PLAN.md` is still untracked because it was provided as the implementation
brief. Build, test, and wheel-smoke artifacts are ignored by `.gitignore`.

## Major Changes

- Added git-delta tracking in `mcp_server/git_delta.py`.
- Extended `.wiki-keeper/state.json` shape with:
  - `git.last_processed_commit`
  - `git.last_seen_commit`
  - `git.default_branch`
  - `git.runs[]`
- `wiki-keeper init` now accepts:
  - `--offline` / `--online`
  - `--dry-run`
  - `--refresh-bootstrap`
  - `--max-subagents N`
  - `--since <sha>`
- `init` records the current `HEAD` as the first nightly baseline unless
  `--since` is provided.
- `wiki-keeper run-nightly` now accepts:
  - `--since <sha>`
  - `--until <sha>`
  - `--dry-run`
  - `--json-output <path>`
- Nightly now computes `last_processed_commit..HEAD`, maps changed paths to
  article frontmatter `sources`, writes audit-only notes for unmapped changes,
  and reviews mapped article diffs.
- MCP now exposes `run_nightly` while keeping existing tools stable.
- Orchestrator output uses strict JSON schema calls.
- Model defaults are nano-first for V1 nightly work.
- Validation now enforces required wiki sections and requires `## Sources`
  content for non-stub pages.
- The repo's own `.wiki-keeper/` corpus now validates under the same contract.
- Manual `ingest_source` / `propose_ingest` remain documented as V1.1 future
  work, not V1 production tools.
- Added GitHub Actions assets:
  - `.github/workflows/ci.yml`
  - `.github/actions/wiki-keeper-nightly/action.yml`
  - `docs/workflows/wiki-keeper-nightly.yml`
- Added package metadata, license, dev extras, ruff config, and mypy config.
- Added deployment docs in `docs/DEPLOYMENT.md`.

## Important Files

- `mcp_server/nightly.py`: commit-driven nightly orchestration.
- `mcp_server/git_delta.py`: git range selection, changed path discovery,
  article mapping, and diff extraction.
- `mcp_server/state.py`: state migration/normalization and git run recording.
- `mcp_server/init_corpus.py`: initialization bootstrap and git baseline setup.
- `mcp_server/validate.py`: schema section and source enforcement.
- `mcp_server/orchestrator.py`: strict JSON schema decision call.
- `mcp_server/cli.py`: expanded CLI options and JSON output support.
- `mcp_server/server.py`: MCP `run_nightly` tool.
- `mcp_server/tests/test_git_delta.py`: integration coverage for commit ranges,
  mapping, unmapped audits, rebaselining, and idempotency.

## Verification Run

These checks passed locally on Windows with Python 3.12 after code review fixes:

```sh
python -m compileall mcp_server
python -m mcp_server.cli validate --repo .
python -m ruff check .
python -m mypy mcp_server --exclude "mcp_server/tests"
python -m pytest
python -m build
```

Final pytest result:

```text
71 passed, 2 skipped
```

Wheel smoke also passed in a project-local clean venv:

```sh
wiki-keeper --help
wiki-keeper init --repo . --dry-run
wiki-keeper validate --repo .
```

`python -m build` now completes without the previous setuptools package-data
warning.

## Code Review Follow-up

The review found and fixed these release-blocking issues:

- Deleted host source files were omitted from git-delta changed path discovery;
  deletions now map through article frontmatter `sources`.
- Partial nightly runs advanced `git.last_processed_commit` even when mapped
  articles were skipped by `--budget`; partial runs now update `last_seen_commit`
  but leave `last_processed_commit` at the prior fully processed baseline.
- Patch schema gating accepted non-stub pages with an empty `## Sources`
  section; patch validation now requires source list content for non-stubs and
  still allows stubs to omit `## Sources`.
- The wheel build included excluded tests as package data and emitted a
  setuptools warning; package data inclusion is now disabled for the wheel.

Regression coverage was added for deleted source mapping, partial nightly state
handling, and strict patch schema enforcement.

## Operational Notes

- Nightly only writes under `.wiki-keeper/`.
- Host repository files are read-only evidence.
- Unmapped git changes do not require `OPENAI_API_KEY`; they produce an
  audit-only record.
- Mapped article reviews require `OPENAI_API_KEY`.
- Patches apply only when the orchestrator returns `decision=patch` and
  `confidence=high`, and only if the replacement body passes schema checks.
- The GitHub composite action expects `fetch-depth: 0`, `OPENAI_API_KEY`,
  `contents: write`, and `pull-requests: write`.
- The stable maintenance branch is `wiki-keeper/nightly`.

## Caveats

- The CI workflow was syntax-tested locally but not run on GitHub yet.
- The composite action uses `gh` for PR create/update; GitHub hosted runners
  include it, but self-hosted runners must provide it.
- Local tests skipped two Windows symlink cases when symlink creation was not
  available.
- `dist/`, `build/`, `.pytest_tmp/`, and `wiki_keeper.egg-info/` were generated
  during verification and are ignored.

## Remaining External Steps

1. Commit the reviewed productionization changes.
2. Open a PR and let the new CI workflow run on GitHub.
3. In a host monorepo, copy `docs/workflows/wiki-keeper-nightly.yml` to
   `.github/workflows/wiki-keeper-nightly.yml` and adjust `package-dir`.
4. Run one manual `workflow_dispatch` before relying on the nightly schedule.
