# wiki-keeper AGENTS.md Snippet

```md
## wiki-keeper

- Corpus lives under `.wiki-keeper/`.
- Keep synthesized pages under `.wiki-keeper/wiki/`.
- Use article frontmatter `sources` globs for host-repo evidence in nightly reviews.
- Run `wiki-keeper validate --repo .` before large wiki edits.
- Use `wiki-keeper run-nightly --repo . --budget 4` in automation for git-delta drift audits.
- Treat `ingest_source` and `propose_ingest` as future V1.1 capabilities, not V1 tools.
- Never edit host repository files from wiki-keeper flows.
```
