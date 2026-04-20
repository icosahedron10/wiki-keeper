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

## Open Questions
- Question
```

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

Frontmatter `sources` are host-repo file globs used by nightly drift review.

Articles without frontmatter are valid and are skipped by nightly review.

## Invariants

1. Every page in `.wiki-keeper/wiki/` appears in `.wiki-keeper/wiki/index.md`.
2. Every mutation appends one line to `.wiki-keeper/wiki/log.md`.
3. File writes are atomic.
4. All writes stay inside `.wiki-keeper/`.
5. Host-repo files are never modified.
6. `update_knowledge` is the only write path for wiki article content.

## Nightly review rules

- Validate corpus before model calls.
- Fail early if `OPENAI_API_KEY` is missing.
- Resolve frontmatter source globs read-only against host repo root.
- Enforce caps: max 200 files and 1 MB aggregate source payload.
- Apply orchestrator patch only when confidence is `high`.
- Patch content must satisfy required wiki structure before write.
- Always produce an audit note under `.wiki-keeper/audits/YYYY-MM-DD/`.
