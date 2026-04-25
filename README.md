<p align="center">
  <img src="logo.png" alt="wiki-keeper" width="560" />
</p>

<p align="center">
  <strong>A persistent knowledge layer for coding agents — exposed over MCP.</strong>
</p>

<p align="center">
  <a href="https://github.com/icosahedron10/wiki-keeper/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-1.2%2B-8A2BE2">
  <img alt="Status: Alpha" src="https://img.shields.io/badge/status-alpha-orange">
</p>

---

## Why wiki-keeper?

Coding agents lose context the moment a session ends. Wiki-keeper gives them a **durable, auditable wiki** that lives inside the repo — decisions, modules, and concepts written down once and kept fresh automatically as the code evolves.

- **Agent-native.** Every page is reachable over [MCP](https://modelcontextprotocol.io), so Claude, Codex, and any MCP client can read and write through the same tools.
- **Commit-driven freshness.** A nightly workflow diffs `git` against the wiki and patches pages when it is confident, or opens audit notes when it is not.
- **Deterministic core.** The base tools — `get_page`, `list_pages`, `query_wiki`, `update_knowledge`, `rebuild_index`, `lint_wiki` — never touch an LLM. What you store is what you get.
- **Zero-infra.** Lives as `.wiki-keeper/` inside your repo. Versioned, diffable, reviewable in a pull request.

## How it works

<p align="center">
  <img src="explainer.png" alt="How wiki-keeper works" width="840" />
</p>

Initialize once, use through MCP, keep it fresh nightly.

## Quickstart

```sh
pip install -e .
wiki-keeper init --repo . --offline
wiki-keeper mcp
```

That's it — your agent now has a persistent wiki at `.wiki-keeper/`.

Point your MCP-capable client at the `wiki-keeper mcp` process and the tools below become available.

## MCP tools

**Core (deterministic):**

| Tool | Purpose |
|---|---|
| `get_page` | Read a page by `category/Title`. |
| `list_pages` | List pages, optionally filtered by category. |
| `query_wiki` | Keyword search across the wiki. |
| `update_knowledge` | Create, replace, or append — atomic. |
| `rebuild_index` | Regenerate `wiki/index.md`. |
| `lint_wiki` | Flag orphans, broken links, index drift. |

**Review & freshness:**

| Tool | Purpose |
|---|---|
| `validate` | Structural, frontmatter, roadmap, and lint checks. |
| `list_articles` | Pages with frontmatter and last-audit metadata. |
| `next_review` | The next roadmap entry after the state cursor. |
| `run_review` | Review one article (explicit or next roadmap item). |
| `run_nightly` | Run the full commit-driven nightly pass. |
| `read_article` | Page with parsed frontmatter and latest audit. |
| `read_audits` | Recent audits for an article id. |

> `ingest_source` and `propose_ingest` are deliberately out of V1. They arrive in V1.1 once the commit-driven path is stable.

## CLI

```sh
wiki-keeper mcp                                          # stdio MCP server
wiki-keeper init --repo . --offline                      # deterministic bootstrap
wiki-keeper init --repo . --online --max-subagents 8     # model-assisted bootstrap
wiki-keeper validate --repo .                            # lint + schema checks
wiki-keeper run-nightly --repo . --budget 4 \
  --json-output .wiki-keeper/nightly-result.json         # commit-driven freshness
wiki-keeper site init --repo . --site-dir site           # static Vercel wiki site
wiki-keeper tools --repo . list                          # scripting surface
```

<details>
<summary><strong>Flags reference</strong></summary>

**`init`** — `--offline` / `--online` (mutually exclusive; offline default), `--dry-run`, `--refresh-bootstrap`, `--max-subagents N` (default 12), `--since <sha>`. Records the current git `HEAD` as the nightly baseline by default.

**`run-nightly`** — `--budget N` (default 1), `--since <sha>`, `--until <sha>`, `--dry-run`, `--json-output <path>`.

**`tools`** — `get <page>`, `list [--category]`, `query <q> [--top-k N]`, `update <page> [--content | stdin] [--mode replace|append|create_only]`, `rebuild-index`, `lint`. Note that `--repo` is a `tools`-level flag, so it goes **before** the sub-subcommand.

</details>

## The nightly workflow

`run-nightly` is commit-driven — it only looks at files that actually changed.

1. Validate corpus.
2. Compute `git.last_processed_commit..HEAD` from `.wiki-keeper/state.json`.
3. Collect changed paths and diffs.
4. Map paths to articles through frontmatter `sources`.
5. Write audit-only notes for unmapped deltas.
6. For mapped articles, run two reader calls.
7. Run the strict-schema orchestrator call.
8. Write audit notes to `.wiki-keeper/audits/YYYY-MM-DD/`.
9. Apply patches **only** when confidence is `high`, via `update_knowledge`.
10. Update git run history in `state.json`.

`run_review` uses the same validation, audit, and patch policy for a single article.

## Configuration

`OPENAI_API_KEY` is required for `run-nightly`, `run_review`, and `init --online`.

`WIKI_KEEPER_ROOT` overrides the host-repo root (set automatically by `--repo`).

**Nightly models** — defaults: `gpt-5-nano` for reader and orchestrator, `medium` orchestrator reasoning, `low` reader reasoning.

| Variable | Default |
|---|---|
| `WIKI_KEEPER_ORCHESTRATOR_MODEL` | `gpt-5-nano` |
| `WIKI_KEEPER_READER_MODEL` | `gpt-5-nano` |
| `WIKI_KEEPER_ORCHESTRATOR_REASONING` | `medium` |
| `WIKI_KEEPER_READER_REASONING` | `low` |

**Online init models** — defaults: `gpt-5.4-mini` for both roles.

| Variable | Default |
|---|---|
| `WIKI_KEEPER_INIT_MANAGER_MODEL` | `gpt-5.4-mini` |
| `WIKI_KEEPER_INIT_WORKER_MODEL` | `gpt-5.4-mini` |
| `WIKI_KEEPER_INIT_MANAGER_REASONING` | `medium` |
| `WIKI_KEEPER_INIT_WORKER_REASONING` | `low` |

## Repository layout

```text
.wiki-keeper/
├── schema.md        # canonical page schema
├── roadmap.md       # scheduled reviews
├── state.json       # cursor + git run history
├── wiki/
│   ├── index.md
│   ├── log.md       # append-only mutation log
│   ├── decisions/
│   ├── modules/
│   └── concepts/
└── audits/          # YYYY-MM-DD/ review notes
```

## Deploy to CI

A composite GitHub Action ships at `.github/actions/wiki-keeper-nightly/action.yml`, with a host workflow template at `docs/workflows/wiki-keeper-nightly.yml`. Requires `OPENAI_API_KEY`, `contents: write`, `pull-requests: write`, and checkout with `fetch-depth: 0`.

See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for monorepo installation and release checks.

## For host repositories

Paste **[docs/AGENTS_TEMPLATE.md](docs/AGENTS_TEMPLATE.md)** into your `AGENTS.md` so agents know how to use the wiki.

## License

MIT — see [LICENSE](LICENSE).
