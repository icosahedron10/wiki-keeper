# wiki-keeper AGENTS.md Snippet

```md
## wiki-keeper

- Corpus lives under `.wiki-keeper/`.
- Keep source evidence under `.wiki-keeper/sources/`.
- Keep synthesized pages under `.wiki-keeper/wiki/`.
- Run `wiki-keeper validate --repo .` before large wiki edits.
- Use `wiki-keeper run-nightly --repo . --budget 1` in automation for drift audits.
- Never edit host repository files from wiki-keeper flows.
```
