# Wiki Schema

This file is the operating manual for the wiki. It is addressed to the agent that maintains it.

## What belongs in the wiki

- Enduring architectural facts about the host repository.
- Subsystem, module, and service summaries.
- Decisions and tradeoffs.
- Recurring debugging knowledge and incident learnings.
- Stable procedures.

## What does not belong

- Raw transcripts copied into the wiki body.
- Full copied external docs.
- Claims without source evidence or explicit uncertainty markers.

## Page format

Every page under `.wiki-keeper/wiki/` is markdown shaped like:

```md
# <Page Title>

## Summary
One paragraph.

## Key Facts
- Fact 1

## Details
Longer explanation.

## Relationships
- Related to [[Other Page]]

## Sources
- [source.md](../../sources/misc/source.md)

## Open Questions
- Question
```

Non-stub pages must include every required heading and at least one list item
under `## Sources`. Stub pages must place `> stub` directly below the H1 and may
omit `## Sources` until evidence exists.

## Optional frontmatter

Articles that opt into nightly review can declare host-repo source globs:

```yaml
---
id: auth-overview
title: Authentication Overview
sources:
  - services/auth/**
  - packages/session/**
---
```

Frontmatter `sources` are host-repo file globs used by commit-driven nightly
drift review. Nightly maps git changed paths to articles through these globs.

Articles without frontmatter are valid and are skipped by nightly review.

## Invariants

1. Every page in `.wiki-keeper/wiki/` appears in `.wiki-keeper/wiki/index.md`.
2. Every mutation appends one line to `.wiki-keeper/wiki/log.md`.
3. File writes are atomic.
4. All writes stay inside `.wiki-keeper/`.
5. Host-repo files are never modified.
6. `update_knowledge` is the only write path for wiki article content.

Exception: `wiki-keeper site init --repo . --site-dir site` may scaffold a
read-only static site outside `.wiki-keeper/` when explicitly requested. That
site treats `.wiki-keeper/wiki` as build input and must not mutate wiki content.

## Nightly review rules

- Validate corpus before model calls.
- Compute `git.last_processed_commit..HEAD` and inspect git diffs as evidence.
- Write audit-only notes when changed paths do not map to article frontmatter.
- Fail early if `OPENAI_API_KEY` is missing and a mapped article needs model review.
- Apply orchestrator patch only when confidence is `high`.
- Patch content must satisfy required wiki structure before write.
- Always produce an audit note under `.wiki-keeper/audits/YYYY-MM-DD/`.
- Manual source ingestion is deferred; V1 production runs are commit-driven.
