# Deployment

wiki-keeper is designed to be dropped into a host monorepo as a submodule,
subtree, or path package. The host repo owns `.wiki-keeper/`; the package code
can live anywhere in the tree.

## Install As A Path Package

```sh
python -m pip install -e ./tools/wiki-keeper
wiki-keeper init --repo . --offline
wiki-keeper validate --repo .
```

Use `--online` for model-assisted initialization:

```sh
OPENAI_API_KEY=... wiki-keeper init --repo . --online --max-subagents 8
```

`init` records the current `HEAD` as the first nightly baseline. Pass
`--since <sha>` to choose a different baseline.

## GitHub Nightly

Copy `docs/workflows/wiki-keeper-nightly.yml` into the host repo at
`.github/workflows/wiki-keeper-nightly.yml`, then update the action path if the
package is not vendored at `tools/wiki-keeper`.

Required repository settings:

- Secret: `OPENAI_API_KEY`
- Workflow permissions: `contents: write`, `pull-requests: write`
- Checkout: `fetch-depth: 0`

The workflow writes only under `.wiki-keeper/`, pushes one stable branch named
`wiki-keeper/nightly`, and opens or updates the matching pull request.

## Local Release Checks

```sh
python -m pip install -e ".[dev]"
python -m ruff check .
python -m mypy mcp_server --exclude "mcp_server/tests"
python -m pytest
python -m build
```

Clean wheel smoke:

```sh
python -m venv .venv-smoke
.venv-smoke/Scripts/python -m pip install dist/*.whl
.venv-smoke/Scripts/wiki-keeper --help
.venv-smoke/Scripts/wiki-keeper init --repo . --dry-run
.venv-smoke/Scripts/wiki-keeper validate --repo .
```

On POSIX shells, replace `.venv-smoke/Scripts/` with `.venv-smoke/bin/`.
