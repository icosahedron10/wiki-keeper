# Deployment

wiki-keeper is designed to be dropped into a host monorepo as a submodule,
subtree, or path package. The host repo owns `.wiki-keeper/`; the package code
can live anywhere in the tree.

## Install As A Path Package

```sh
python -m pip install -e ./tools/wiki-keeper
OPENAI_API_KEY=... wiki-keeper init --repo .
wiki-keeper validate --repo .
```

`init` uses a model-assisted bootstrap and records the current `HEAD` as the
first nightly baseline. Pass
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

## Static Vercel Wiki Site

wiki-keeper can also scaffold a read-only public site that serves
`.wiki-keeper/wiki` through a static Next.js export:

```sh
wiki-keeper site init --repo . --site-dir site
```

This command intentionally writes outside `.wiki-keeper/`: it creates a `site/`
Next.js app and, when missing, a repo-root `vercel.json`. Use `--dry-run` to
preview files, or `--force` to overwrite existing template files in `site/`.

The generated Vercel config builds from the repository root:

```json
{
  "framework": null,
  "installCommand": "cd site && npm ci",
  "buildCommand": "cd site && npm run build",
  "outputDirectory": "site/out"
}
```

Import the repository into Vercel with the project root set to the repository
root, not `site/`. The build must read `.wiki-keeper/wiki`, which is outside the
site directory. If the host repo already has `vercel.json`, wiki-keeper reports
the required settings and leaves the existing file untouched.

Source links use `WIKI_KEEPER_SOURCE_URL_TEMPLATE` when set, with `{ref}` and
`{path}` placeholders. Otherwise, GitHub links are inferred from Vercel system
environment variables. Enable Vercel's "Automatically expose System Environment
Variables" setting for commit-pinned source links. Private repositories should
use Vercel deployment protection or private project access because published
wiki pages may expose internal project state.

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
