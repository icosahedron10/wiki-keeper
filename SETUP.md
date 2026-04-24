Below is an iterated version of your plan in a more stripped-down, pragmatic style. I kept your core idea intact, but made it more concrete, more operational, and more biased toward implementation details and failure modes.

Implementation note: V1 production automation is now commit-driven through
`wiki-keeper run-nightly` and GitHub Actions. `ingest_source` and
`propose_ingest` remain design targets for V1.1 rather than shipped V1 tools.

# SRD: Wiki-MCP

**A persistent knowledge layer for coding agents**

## 0. Why this exists

Coding agents are good at short-horizon reasoning inside a single session. They are weak at long-horizon accumulation of understanding across days, branches, documents, debugging sessions, and architectural drift.

The goal of Wiki-MCP is to give agents a place to write down what they have learned in a form that compounds. Not a dump of embeddings. Not a pile of transcripts. A maintained, structured, evolving wiki that acts as external memory for the codebase.

This system should let an agent do three things well:

1. read the current state of understanding,
2. update that understanding when it learns something new,
3. keep the knowledge base coherent over time.

The wiki is not the source of truth for code. The repository is. The wiki is the source of synthesized understanding about the repository.

---

## 1. Product definition

Wiki-MCP is a subrepository plus an MCP server.

The subrepository stores:

* raw evidence,
* synthesized wiki pages,
* governance rules for how the wiki is maintained.

The MCP server exposes tools that let an agent:

* ingest new evidence,
* search existing knowledge,
* create or revise pages,
* run health checks on the wiki.

The intended user is not a human directly. The intended user is an agent working on a codebase. Humans should still be able to read the wiki easily.

---

## 2. Non-goals

This project is not trying to be:

* a general-purpose documentation generator,
* a pure vector database over repo contents,
* a replacement for code comments, ADRs, or RFCs,
* a chat transcript archive,
* a fully automatic truth engine.

If the system cannot synthesize reliably, it should fail toward leaving explicit uncertainty rather than manufacturing confident prose.

---

## 3. Core idea

We split knowledge into three layers:

### Layer 1: `sources/`

Immutable evidence.

This is where raw material lands:

* architecture notes
* CLI output
* design docs
* PR summaries
* issue threads
* debugging transcripts
* pasted code snippets
* benchmark results
* meeting notes

These files should be append-only or treated as immutable once ingested.

### Layer 2: `wiki/`

Mutable synthesized knowledge.

This is the living markdown wiki:

* concept pages
* module pages
* people/process pages if useful
* decisions
* incident summaries
* dependency maps
* index and logs

This layer is maintained by the agent.

### Layer 3: `schema.md`

Governance.

This is the operating manual for the wiki:

* page format
* linking rules
* citation requirements
* what belongs in the wiki and what does not
* how uncertainty is represented
* maintenance workflows
* naming conventions

The schema is effectively the policy layer for agent behavior.

---

## 4. Design principles

### 4.1 Write for compounding value

A page should become more useful after the tenth update than after the first. Avoid repetition. Prefer synthesis.

### 4.2 Preserve evidence boundaries

Raw source material stays raw. Synthesized claims in the wiki should point back to specific source files.

### 4.3 Small pages, dense links

Pages should be atomic and heavily cross-linked. A page should answer one question well.

### 4.4 Favor explicit uncertainty

If the agent is not sure, it should say so and cite the ambiguity.

### 4.5 Make maintenance cheap

If the wiki becomes annoying to maintain, it will rot. Tooling should make the right behavior easy.

### 4.6 The wiki is for agents first, humans second

The format should be readable by humans, but optimized for machine maintenance and retrieval.

---

## 5. Repository layout

```text
wiki-mcp/
├── sources/
│   ├── architecture/
│   ├── meetings/
│   ├── debugging/
│   ├── prs/
│   ├── docs/
│   └── misc/
├── wiki/
│   ├── index.md
│   ├── log.md
│   ├── decisions/
│   ├── modules/
│   ├── concepts/
│   ├── systems/
│   ├── incidents/
│   └── people/
├── schema.md
├── mcp_server/
│   ├── server.py
│   ├── tools/
│   ├── search/
│   ├── storage/
│   └── tests/
├── scripts/
└── README.md
```

Recommended bias: start simple. Do not over-partition the wiki on day one. The first version may just need:

```text
wiki/
├── index.md
├── log.md
├── decisions/
├── modules/
└── concepts/
```

---

## 6. Data model

A wiki page is a markdown file with lightweight structure.

### Required page sections

Every page should follow a standard shape:

```md
# <Page Title>

## Summary
One short paragraph describing what this page is about.

## Key Facts
- Fact 1
- Fact 2
- Fact 3

## Details
Longer explanation.

## Relationships
- Related to [[Other Page]]
- Depends on [[Another Page]]

## Sources
- [source_file_1.md](../sources/...)
- [source_file_2.md](../sources/...)

## Open Questions
- Question 1
- Question 2
```

This should not be enforced too rigidly at first, but enough consistency helps both retrieval and maintenance.

---

## 7. Naming and linking rules

### Page naming

Use stable, explicit names:

* `Auth Service.md`
* `Feature Flags.md`
* `Checkout Pipeline.md`
* `Decision - Async Job Retries.md`

Avoid vague names:

* `notes.md`
* `thoughts.md`
* `stuff.md`

### Internal links

Use wiki-style links or markdown links, but pick one and enforce it consistently. Example:

```md
[[Checkout Pipeline]]
[[Decision - Async Job Retries]]
```

### Citations

Every nontrivial claim should point to evidence under `sources/`.

Good:

* “The retry limit was increased from 3 to 5 in Q2.” `[Source](../sources/prs/pr_184_summary.md)`

Bad:

* “Retries were changed at some point.”

---

## 8. MCP tool surface

The MCP server should expose a minimal set of tools first. Keep the interface tight.

## 8.1 `ingest_source`

```python
ingest_source(source_path: str, context: str | None = None) -> IngestResult
```

### Purpose

Read a raw source file and update the wiki based on it.

### Expected behavior

* parse the source file
* identify impacted pages
* propose page edits
* create new pages if needed
* update `wiki/index.md`
* append an entry to `wiki/log.md`
* return a structured summary of changes

### Notes

This is the highest leverage tool. Most of the system value flows through it.

### Return shape

Suggested:

```json
{
  "source_path": "sources/prs/pr_184_summary.md",
  "pages_created": ["Decision - Async Job Retries.md"],
  "pages_updated": ["Checkout Pipeline.md"],
  "index_updated": true,
  "log_updated": true,
  "conflicts_found": [],
  "open_questions": []
}
```

---

## 8.2 `query_wiki`

```python
query_wiki(query: str, mode: str = "hybrid", top_k: int = 5) -> QueryResult
```

### Purpose

Retrieve relevant pages or snippets from the wiki.

### Modes

* `keyword`
* `semantic`
* `hybrid`

### Behavior by scale

* small wiki: grep + `index.md` scan is enough
* medium wiki: add BM25
* large wiki: add vectors over page chunks or summaries

### Return shape

Should include:

* matched pages
* matching snippets
* confidence or score
* maybe related pages

---

## 8.3 `update_knowledge`

```python
update_knowledge(page_name: str, content: str, mode: str = "replace_or_merge") -> UpdateResult
```

### Purpose

Create or modify a wiki page atomically.

### Required guarantees

* page exists after success
* page path is deterministic
* `index.md` updated if page is new
* backlink or link syntax validated
* log entry appended

This tool should be lower-level than `ingest_source`. Most agents will use `ingest_source`; `update_knowledge` exists for targeted edits.

---

## 8.4 `lint_wiki`

```python
lint_wiki() -> LintReport
```

### Purpose

Check wiki health.

### Checks

* broken internal links
* orphan pages
* missing sources sections
* pages absent from `index.md`
* duplicate pages with overlapping meaning
* stale “Open Questions” that may now be answerable
* conflicting claims across pages
* malformed timestamps in `log.md`

This tool should be runnable frequently and cheaply.

---

## 8.5 Strongly recommended additional tools

Your current plan is close, but I would add these.

### `list_pages`

```python
list_pages(category: str | None = None) -> list[str]
```

Useful for planning and agent situational awareness.

### `get_page`

```python
get_page(page_name: str) -> str
```

Agents will need direct reads of canonical page content.

### `propose_ingest`

```python
propose_ingest(source_path: str) -> ProposedChanges
```

Dry-run mode before mutation. Very useful for debugging agent behavior and for human review loops.

### `rebuild_index`

```python
rebuild_index() -> RebuildResult
```

Useful when things drift or after large migrations.

---

## 9. Operational invariants

These are the rules the system must never silently violate.

### Invariant 1

Every page in `wiki/` must appear in `wiki/index.md`.

### Invariant 2

Every mutation to the wiki must append a record to `wiki/log.md`.

### Invariant 3

Every page must contain at least one source citation, unless explicitly marked as a stub.

### Invariant 4

No source file under `sources/` is modified by the agent after ingestion.

### Invariant 5

If two pages contain conflicting claims, the conflict must be surfaced, not silently resolved unless the evidence clearly settles it.

### Invariant 6

All file writes are atomic. Partial writes should not corrupt the wiki.

---

## 10. `schema.md` as policy

The schema should be short, strict, and legible. It should read like instructions to a model.

Suggested sections:

### 10.1 What belongs in the wiki

* enduring architectural facts
* subsystem summaries
* decisions and tradeoffs
* recurring debugging knowledge
* glossary/entity definitions
* stable operational procedures

### 10.2 What does not belong

* long raw transcripts
* full copied documentation
* speculative claims without labeling
* temporary scratch notes unless explicitly kept

### 10.3 Style rules

* write concise, information-dense prose
* no filler
* prefer bullets for facts
* prefer short paragraphs
* cite evidence
* mark uncertainty explicitly
* link related pages aggressively

### 10.4 Update policy

When ingesting a source:

1. read source
2. identify affected concepts/entities/decisions
3. update existing pages first
4. create new pages only if needed
5. update index
6. append log entry
7. run lint checks if change was substantial

---

## 11. Search strategy

Do not overengineer retrieval early.

### Phase A: very small scale

Use:

* `index.md`
* file names
* full text grep

This is probably enough for 0 to 100 pages.

### Phase B: medium scale

Add BM25 or tantivy-style lexical indexing over:

* page titles
* summaries
* headings
* body text

### Phase C: larger scale

Add local embeddings over:

* page summaries
* sections
* optionally source summaries

Use hybrid retrieval, not semantic-only retrieval.

Important point: the wiki itself is already a compressed knowledge representation. The need for vectors may arrive later than expected.

---

## 12. Write path and conflict handling

The hardest part of the system is not retrieval. It is controlled mutation.

### Update algorithm

For a given source:

1. extract candidate entities, modules, and decisions
2. find relevant existing pages
3. decide:

   * update existing page,
   * create new page,
   * or log unresolved ambiguity
4. write changes atomically
5. update index/log
6. run lightweight lint
7. return structured diff

### Conflict policy

If source A says one thing and source B says another:

* do not collapse into one confident statement
* record the conflict
* cite both
* optionally create an “Open Questions” item
* optionally tag the page for review

This matters. A false memory layer is worse than no memory layer.

---

## 13. Observability

This should be instrumented from day one.

Track:

* number of sources ingested
* pages created
* pages updated
* link density
* orphan count
* average citations per page
* conflict count
* query hit rate
* query latency
* lint failures over time

You want to know whether the wiki is getting healthier or just bigger.

---

## 14. Evaluation

You need explicit evals or the project will feel good while quietly drifting.

### 14.1 Retrieval eval

Given a query, does the system return the right page(s)?

Examples:

* “Where is retry policy defined?”
* “Why did we choose async processing here?”
* “What module owns feature flags?”

### 14.2 Synthesis eval

Given a new source, does the system update the correct pages and preserve coherence?

Check:

* were the right pages edited?
* were claims cited?
* was duplication introduced?
* did the summary improve?

### 14.3 Maintenance eval

Over repeated ingests, does the wiki remain clean?

Track:

* broken links
* duplicate concepts
* uncited claims
* stale stubs
* unresolved conflicts

### 14.4 Human usefulness eval

Can an engineer answer repo questions faster with the wiki than without it?

This is the real benchmark.

---

## 15. Security and trust boundaries

This matters if agents can write.

### Risks

* malicious or low-quality source text poisoning the wiki
* overconfident synthesis from weak evidence
* accidental leakage of secrets from raw sources into synthesized pages
* wiki drift away from actual code reality

### Mitigations

* treat `sources/` as evidence, not truth
* require citations
* optionally redact secrets before ingest
* optionally support approval mode for high-impact updates
* prefer append-only logs
* support dry-run proposals
* periodically reconcile wiki claims against code or docs

---

## 16. MVP scope

The MVP should be much smaller than the full vision.

### Must have

* repo structure
* `schema.md`
* `ingest_source`
* `query_wiki`
* `update_knowledge`
* `lint_wiki`
* `index.md`
* `log.md`

### Nice to have later

* embeddings
* graph views
* Mermaid auto-generation
* automatic code entity extraction
* human review UI
* confidence scoring
* conflict dashboard

### Explicitly do not build first

* multi-user sync complexity
* remote hosting
* elaborate permission systems
* fancy frontends
* autonomous continuous crawling of the entire repo

---

## 17. Build plan

## Phase 1: Bootstrap

Goal: get a tiny but functional wiki loop working.

Deliverables:

* initialize repo structure
* write `schema.md`
* seed `wiki/index.md` and `wiki/log.md`
* manually ingest 5 to 10 representative sources
* create 10 to 20 initial pages

Success criteria:

* the wiki is readable
* the page format is stable enough
* manual maintenance reveals missing rules

---

## Phase 2: Local MCP server

Goal: expose the wiki as tools.

Deliverables:

* file I/O server
* implementations of:

  * `get_page`
  * `list_pages`
  * `query_wiki`
  * `update_knowledge`
  * `ingest_source`
  * `lint_wiki`
* atomic write logic
* tests for index/log invariants

Success criteria:

* an agent can ingest a source and correctly update the wiki end to end

---

## Phase 3: Agent integration

Goal: make this usable inside your coding workflow.

Deliverables:

* hook MCP into your agentic interface
* add a workflow where the agent updates the wiki after:

  * debugging sessions
  * PR review
  * architecture exploration
  * major code changes

Success criteria:

* after a real coding session, the agent leaves behind useful durable knowledge

---

## Phase 4: Quality hardening

Goal: prevent rot.

Deliverables:

* better linting
* duplicate page detection
* conflict surfacing
* dry-run propose mode
* better tests
* basic metrics

Success criteria:

* repeated ingests do not cause obvious wiki decay

---

## Phase 5: Retrieval scaling

Goal: improve search only when needed.

Deliverables:

* BM25 index
* optional local embeddings
* hybrid ranking
* query eval set

Success criteria:

* retrieval remains good as the wiki grows beyond ~100 pages

---

## 18. Example workflows

### Workflow A: ingest a PR summary

1. PR summary dropped into `sources/prs/`
2. agent calls `ingest_source`
3. system detects impacted modules and decisions
4. updates relevant pages
5. appends log entry
6. lint runs
7. agent can later answer “why was this changed?”

### Workflow B: debugging session

1. agent finishes complex debugging
2. writes session summary into `sources/debugging/`
3. ingests source
4. updates incident page or module page
5. preserves the learned tribal knowledge

### Workflow C: architecture exploration

1. engineer asks agent to understand subsystem X
2. agent reads code/docs
3. agent writes synthesized notes into `sources/architecture/`
4. ingests notes
5. wiki gains a durable subsystem overview

---

## 19. Failure modes

These are likely and should be expected.

### Failure mode 1: duplication

Same concept gets split across multiple pages.

Mitigation:

* lint for semantic similarity in titles/summaries
* bias to updating existing pages first

### Failure mode 2: shallow summaries

Pages become bland restatements of sources.

Mitigation:

* schema should explicitly demand synthesis, relationships, and tradeoffs

### Failure mode 3: uncited confidence

Agent writes fluent but weak claims.

Mitigation:

* require sources section
* fail lint on missing citations

### Failure mode 4: wiki bloat

Too many low-value pages.

Mitigation:

* require atomic page criterion
* allow stubs to be merged or retired

### Failure mode 5: stale truth

Code changes but wiki does not.

Mitigation:

* integrate wiki updates into agent workflows around actual code changes
* consider future hooks from PRs/commits

---

## 20. Recommended implementation bias

If I were building this, I would do it in the following order:

1. markdown structure first
2. local file-backed MCP tools second
3. grep/BM25 search third
4. evals and linting fourth
5. embeddings only after pain is real

That ordering matters. The hardest part is not vector search. The hardest part is getting the agent to maintain a coherent written artifact over time.

---

## 21. Concise product summary

Wiki-MCP is a persistent external memory system for coding agents. It stores raw evidence in `sources/`, synthesized understanding in `wiki/`, and maintenance rules in `schema.md`. Agents interact with it through MCP tools that ingest evidence, query the wiki, update pages, and lint for coherence. The main value is not storage. The main value is accumulation of structured understanding that compounds across sessions.

---

## 22. Immediate next steps

1. Freeze the page schema.
2. Create the directory layout.
3. Write `schema.md` as model-facing instructions.
4. Hand-create 10 good pages from real repo material.
5. Build `update_knowledge` and `lint_wiki` first.
6. Then build `ingest_source`.
7. Only add semantic search after the manual workflow already feels useful.

If useful, I can turn this next into one of two things:

* a much tighter Karpathy-style implementation memo with sharper bullets and fewer words
* a file-by-file engineering plan for the MCP server, including Python package layout and tool signatures
