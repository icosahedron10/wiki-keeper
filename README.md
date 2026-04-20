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
wiki-keeper init --repo .
wiki-keeper validate --repo .
wiki-keeper run-nightly --repo . --budget 1
wiki-keeper tools list --repo .
```

`wiki-keeper` without a subcommand is intentionally invalid in V1.

## MCP tools

Existing deterministic tools:

- `get_page`
- `list_pages`
- `query_wiki`
- `update_knowledge`
- `rebuild_index`
- `lint_wiki`

New V1 tools:

- `validate`
- `list_articles`
- `next_review`
- `run_review`
- `read_article`
- `read_audits`

## Nightly workflow

`run-nightly` and `run_review` do:

1. Validate corpus.
2. Check `OPENAI_API_KEY` before any model call.
3. Select target article from roadmap/state.
4. Resolve frontmatter `sources` globs against host repo root (read-only, capped at 200 files / 1 MB).
5. Run two reader calls (`gpt-5-nano` by default).
6. Run orchestrator call (`gpt-5-mini` by default).
7. Write audit note to `.wiki-keeper/audits/YYYY-MM-DD/`.
8. Apply patch only if confidence is `high`, through `update_knowledge`.
9. Update `.wiki-keeper/state.json`.

Model defaults can be overridden:

- `WIKI_KEEPER_ORCHESTRATOR_MODEL`
- `WIKI_KEEPER_READER_MODEL`
- `WIKI_KEEPER_ORCHESTRATOR_REASONING`
- `WIKI_KEEPER_READER_REASONING`

## AGENTS snippet

See [docs/AGENTS_TEMPLATE.md](docs/AGENTS_TEMPLATE.md) for a snippet host repositories can paste into their `AGENTS.md`.
