# Wiki Schema

This file is the operating manual for the wiki. It is addressed to the agent that maintains it.

## What belongs in the wiki

- Enduring architectural facts about the host repository.
- Subsystem, module, and service summaries.
- Decisions and tradeoffs.
- Recurring debugging knowledge and incident learnings.
- Stable procedures.

## What does not belong

- Raw transcripts (store in `.wiki-keeper/sources/`).
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
- [evidence.md](../../sources/...)

## Open Questions
- Question
```

Pages without at least one entry under `## Sources` must be marked as stubs with `> stub` on the line below the H1.

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

Two notions of sources are distinct:

- Frontmatter `sources`: host-repo file globs used by nightly drift review.
- Body `## Sources`: evidence files under `.wiki-keeper/sources/` used to support wiki claims.

Articles without frontmatter are valid and are skipped by nightly review.

## Invariants

1. Every page in `.wiki-keeper/wiki/` appears in `.wiki-keeper/wiki/index.md`.
2. Every mutation appends one line to `.wiki-keeper/wiki/log.md`.
3. Every page has `## Sources` entries, or is marked `> stub`.
4. Files under `.wiki-keeper/sources/` are never modified by wiki tools.
5. File writes are atomic.
6. All writes stay inside `.wiki-keeper/`.
7. Host-repo files are never modified.
8. `update_knowledge` is the only write path for wiki article content.

## Nightly review rules

- Validate corpus before model calls.
- Fail early if `OPENAI_API_KEY` is missing.
- Resolve frontmatter source globs read-only against host repo root.
- Enforce caps: max 200 files and 1 MB aggregate source payload.
- Apply orchestrator patch only when confidence is `high`.
- Patch content must satisfy required wiki structure before write.
- Always produce an audit note under `.wiki-keeper/audits/YYYY-MM-DD/`.
