# wiki-keeper

A persistent knowledge layer for coding agents, exposed over MCP.

V1 keeps a deterministic MCP surface and adds an optional nightly freshness workflow that audits wiki pages against host-repo files.

## Layout

```text
.wiki-keeper/
├── schema.md
├── roadmap.md
├── state.json
├── wiki/
│   ├── index.md
│   ├── log.md
│   ├── decisions/
│   ├── modules/
│   └── concepts/
└── audits/
```

## Install

```sh
pip install -e .
```

Python 3.10+ required.

## CLI

```sh
wiki-keeper mcp
wiki-keeper init --repo . --offline
wiki-keeper init --repo . --online --max-subagents 8
wiki-keeper validate --repo .
wiki-keeper run-nightly --repo . --budget 4 --json-output .wiki-keeper/nightly-result.json
wiki-keeper tools list --repo .
```

`wiki-keeper` without a subcommand is intentionally invalid in V1.

`init` accepts `--dry-run`, `--refresh-bootstrap`, `--max-subagents N`, and
`--since <sha>`. By default it records the current git `HEAD` as the nightly
baseline.

`run-nightly` accepts `--since <sha>`, `--until <sha>`, `--dry-run`, and
`--json-output <path>`.

## MCP tools

Existing deterministic tools:

- `get_page`
- `list_pages`
- `query_wiki`
- `update_knowledge`
- `rebuild_index`
- `lint_wiki`

`ingest_source` and `propose_ingest` are intentionally not V1 production tools;
they are documented as future V1.1 work after the commit-driven GitHub path is
stable.

New V1 tools:

- `validate`
- `list_articles`
- `next_review`
- `run_review`
- `run_nightly`
- `read_article`
- `read_audits`

## Nightly workflow

`run-nightly` is commit-driven:

1. Validate corpus.
2. Compute `.wiki-keeper/state.json` `git.last_processed_commit..HEAD`.
3. Collect changed paths and diffs from git.
4. Map changed paths to articles through frontmatter `sources`.
5. Write audit-only notes when no article maps cleanly.
6. For mapped articles, run two reader calls (`gpt-5-nano` by default).
7. Run the strict-schema orchestrator call (`gpt-5-nano` by default).
8. Write audit notes to `.wiki-keeper/audits/YYYY-MM-DD/`.
9. Apply patches only if confidence is `high`, through `update_knowledge`.
10. Update `.wiki-keeper/state.json` git run history.

`run_review` remains available for one explicit article or the next roadmap
entry. It uses the same validation, audit, and patch policy.

Model defaults can be overridden:

- `WIKI_KEEPER_ORCHESTRATOR_MODEL`
- `WIKI_KEEPER_READER_MODEL`
- `WIKI_KEEPER_ORCHESTRATOR_REASONING`
- `WIKI_KEEPER_READER_REASONING`

## GitHub Actions

The repo ships a composite action at
`.github/actions/wiki-keeper-nightly/action.yml` and a host workflow template at
`docs/workflows/wiki-keeper-nightly.yml`. The workflow needs `OPENAI_API_KEY`,
`contents: write`, `pull-requests: write`, and checkout `fetch-depth: 0`.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for monorepo installation and
release checks.

## AGENTS snippet

See [docs/AGENTS_TEMPLATE.md](docs/AGENTS_TEMPLATE.md) for a snippet host repositories can paste into their `AGENTS.md`.
