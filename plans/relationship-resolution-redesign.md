# Relationship Resolution Redesign Plan

**Refactor plan for reliable `CALLS` / `IMPORTS` / inheritance resolution in `node-walk`.**

Local-first, correctness-first, incremental migration.

---

## 1. Why This Plan Exists

The current graph pipeline mixes together three different concerns:

- parsing syntax
- registering symbols
- resolving semantic relationships

That works for structural edges like `CONTAINS`, but it breaks down for semantic edges like `CALLS`, `IMPORTS`, `EXTENDS`, and `IMPLEMENTS`, where the parser often sees only partial information at first.

The current failure mode is especially visible in `CALLS`:

- most call sites are emitted during AST traversal with incomplete context
- in-file resolution only checks exact local name matches
- cross-file resolution only looks at `metadata["target_name"]`
- unresolved relationships are stored with `target_id = ""`, making them effectively invisible in graph views

This creates a trust problem for the product:

- the graph looks sparse or disconnected even when the code is highly connected
- browser and CLI consumers appear wrong even when they are faithfully reading the database
- debugging is hard because "what we observed" and "what we concluded" are not stored separately

This plan redesigns the pipeline so parsing produces raw facts, resolution happens in dedicated passes, and final graph edges are materialized from those results.

---

## 2. Goals

### Primary Goals

- Dramatically improve relationship correctness, especially for `CALLS`
- Separate extraction from resolution so heuristics can evolve without re-parsing everything
- Preserve enough raw information to debug failed resolutions
- Keep the current CLI and browser explorer working during the migration
- Make the design extensible for better type-aware resolution later

### Success Criteria

- `self.method()` resolves reliably within classes, including forward references
- `ClassName()` resolves to `ClassName.__init__` when available
- cross-file `CALLS` can resolve using imports, names, and simple heuristics
- unresolved relationships are inspectable as raw facts rather than silently disappearing
- graph consumers can distinguish `resolved`, `probable`, and `unresolved` relationships

### Non-Goals

- Full Python type inference
- Interprocedural data-flow analysis
- Perfect attribute-call resolution for arbitrary dynamic Python
- Multi-language redesign in this phase
- Replacing Tree-sitter unless it proves structurally insufficient

---

## 3. Current Problems

### 3.1 Extraction and Resolution Are Too Tightly Coupled

`SymbolCollector` currently decides relationship targets while walking the AST. That is too early for many semantic edges.

Example:

- parse sees `self._message_payload()`
- collector has only partial knowledge of the class at that moment
- if the target method is defined later, or if the receiver needs scope/import context, resolution fails

### 3.2 Raw Facts Are Lost

Today, unresolved edges are stored as relationships with empty `target_id`. That means:

- the raw call-site observation is mixed with the final graph layer
- unresolved data is hard to inspect and reason about
- re-running better resolution logic requires re-parsing the repo

### 3.3 Cross-File Resolution Is Too Generic

`Indexer._resolve_cross_file()` applies a single generic `target_name` matching strategy. That is too weak for `CALLS`.

Different relationship types need different resolution logic:

- `IMPORTS` want import/module-aware rules
- `CALLS` want lexical, class-member, constructor, and import-aware rules
- `EXTENDS` / `IMPLEMENTS` want class/interface matching rules

### 3.4 UI Reflects Broken Data

The explorer is not the root problem, but there are a few follow-on issues:

- unresolved edges vanish from the graph because they have no target
- detail counts are currently based on all relationship types rather than just `CALLS`
- the UI has no way to show "we saw a call here, but resolution was uncertain"

---

## 4. Design Principles

- Graph correctness over graph density
- Store observations separately from conclusions
- Prefer explicit staged pipelines over hidden heuristics in visitors
- Treat each relationship type as a first-class resolution problem
- Make re-resolution cheap
- Keep migration incremental so current commands do not break

---

## 5. Target Architecture

High-level flow:

**Repository -> Parse / Extract Facts -> Persist Facts -> Resolve Facts -> Materialize Graph Edges -> Query / UI**

### 5.1 Stage A: Extraction

Language-specific analyzers produce:

- symbols
- scopes / containment
- imports
- raw call sites
- raw inheritance references
- raw references
- optional local hints from syntax

At this stage, only relationships that are structurally certain should be emitted as final graph edges.

Examples:

- `CONTAINS` can remain final immediately
- `CALLS` should usually become raw facts first
- `IMPORTS` can be fact-first unless trivially resolvable
- `EXTENDS` / `IMPLEMENTS` should be fact-first

### 5.2 Stage B: Resolution

Dedicated resolver passes run over stored symbols plus raw facts.

Resolvers should be relationship-specific:

- `CallResolver`
- `ImportResolver`
- `InheritanceResolver`
- future `ReferenceResolver`

Each resolver may produce:

- resolved target symbol
- probable target symbol
- unresolved outcome with diagnostics

### 5.3 Stage C: Graph Materialization

The final semantic graph is generated from resolution results.

Materialized graph edges should represent the best current conclusion, while raw facts remain preserved for debugging and future re-resolution.

---

## 6. Data Model Redesign

The key design change is to stop treating every early observation as a final relationship.

### 6.1 Keep Existing Tables

Retain:

- `files`
- `symbols`
- `relationships`

This avoids breaking the current query engine and browser immediately.

### 6.2 Add Raw Fact Storage

Introduce a new table, for example:

- `relationship_facts`

Suggested shape:

| Column | Purpose |
|---|---|
| `id` | fact id |
| `file_id` | source file |
| `source_symbol_id` | caller / dependent / importer |
| `fact_type` | `CALL`, `IMPORT`, `INHERITANCE`, `REFERENCE` |
| `raw_text` | original text like `self.foo`, `json.loads`, `BaseService` |
| `simple_name` | last segment like `foo` |
| `receiver_text` | receiver like `self`, `json`, `adapter` |
| `qualified_hint` | optional dotted hint if extracted |
| `line` / `col` | source location |
| `scope_symbol_id` | enclosing function/method/class if useful |
| `metadata_json` | language-specific hints |
| `status` | `pending`, `resolved`, `probable`, `unresolved`, `ignored` |
| `resolved_target_id` | best current target |
| `resolver_name` | which resolver last made the decision |
| `diagnostics_json` | why it resolved or failed |

This table becomes the source of truth for semantic observations.

### 6.3 Continue Materializing Final Relationships

Keep `relationships` as the query-facing graph surface.

For semantic edges, materialize rows in `relationships` from facts after resolution.

This gives us:

- stable consumers
- debuggable internals
- re-runnable resolution

### 6.4 Optional Future Split

If needed later, the design can be made even clearer with:

- `relationship_facts`
- `resolved_relationships`

For now, the existing `relationships` table can play the role of the materialized output layer.

---

## 7. Extraction Design

### 7.1 Symbols and Structural Edges

The parser should continue to emit:

- `FILE`
- `MODULE`
- `CLASS`
- `INTERFACE`
- `FUNCTION`
- `METHOD`
- `FIELD`
- `VARIABLE`
- `CONSTANT`
- `CONTAINS`

These are stable and mostly deterministic.

### 7.2 Pre-Register Class Members

Class handling should move to a two-step process:

1. Register the class symbol.
2. Pre-register class members before analyzing method bodies.

That means inside a class body:

- collect method symbols first
- collect class field symbols first
- build a class-member lookup map
- only then analyze method bodies and call sites

This fixes the forward-reference problem:

- `method_a()` can resolve `self.method_b()` even if `method_b()` appears later in the file

### 7.3 Extract Raw Call Facts Instead of Final `CALLS` Edges

For each call expression, store a raw fact with:

- caller symbol id
- call text
- callee simple name
- receiver text if present
- source location
- any obvious syntactic hints

Examples:

- `self.foo()` -> `simple_name=foo`, `receiver_text=self`
- `adapter.chat()` -> `simple_name=chat`, `receiver_text=adapter`
- `OllamaAdapter()` -> `simple_name=OllamaAdapter`, constructor candidate
- `json.loads()` -> `simple_name=loads`, `receiver_text=json`

### 7.4 Extract Raw Import Facts

Import statements should also be fact-first:

- module text
- imported name
- alias if any
- relative import context

This supports better cross-file call resolution later because imported symbols shape the visible namespace.

### 7.5 Extract Raw Inheritance Facts

For classes:

- store each declared base expression as a raw inheritance fact
- resolution later determines whether it becomes `EXTENDS` or `IMPLEMENTS`

---

## 8. Resolution Design

Resolution should move into explicit passes rather than hidden logic inside the AST visitor.

### 8.1 Resolver Ordering

Recommended order:

1. `ImportResolver`
2. `InFileCallResolver`
3. `ClassMemberCallResolver`
4. `ConstructorCallResolver`
5. `CrossFileCallResolver`
6. `InheritanceResolver`
7. optional noise-pruning / ignored-fact pass

This ordering gives later passes better context.

### 8.2 Import Resolver

Responsibilities:

- resolve raw import facts to symbols or modules where possible
- build per-file import context for later call resolution
- normalize aliases

Useful outputs:

- final `IMPORTS` edges
- import namespace map per file

### 8.3 In-File Call Resolver

Responsibilities:

- resolve exact lexical references within the same file
- match local function names
- match directly visible qualified names

This is the safe replacement for the current `_local_by_name` and `_local_by_qname` checks, but moved out of the visitor.

### 8.4 Class-Member Resolver

Responsibilities:

- resolve `self.foo()` to methods or fields of the enclosing class
- resolve class-local references after member pre-registration
- optionally resolve `cls.foo()` for classmethods

High-confidence rules:

- if receiver is `self` and the enclosing function belongs to class `C`, match `C.foo`
- if receiver is `cls` and the enclosing function is a classmethod, match `C.foo`

This should usually produce `resolved` or `probable` outcomes depending on confidence.

### 8.5 Constructor Resolver

Responsibilities:

- detect calls where `callee_name` matches a class symbol
- prefer the class `__init__` method if one exists
- otherwise fall back to the class symbol itself

This fixes `ClassName()` style edges, which are semantically useful in a call graph.

### 8.6 Cross-File Call Resolver

Responsibilities:

- use import context to map visible names to actual symbols
- resolve plain `foo()` to imported or module-level functions where possible
- resolve `ModuleName.func()` or alias-based references
- fall back to suffix/simple-name heuristics only when stronger evidence is absent

Confidence tiers:

- `resolved`: explicit import or exact qualified match
- `probable`: unambiguous simple-name or suffix match
- `unresolved`: ambiguous or unsupported

### 8.7 Inheritance Resolver

Responsibilities:

- resolve base class names across files
- distinguish `EXTENDS` vs `IMPLEMENTS`
- use interface markers and symbol kinds rather than raw text alone

### 8.8 Noise Filtering Strategy

Do not begin with a giant skip list.

Start conservative:

- ignore obvious Python builtins
- ignore calls that can be positively identified as stdlib or external-library noise if they are outside the intended graph scope
- never drop a call merely because its method name is common like `get`, `read`, or `write`

Why:

- broad filters will hide real user-defined edges
- resolution correctness is more valuable than aggressive graph thinning early on

A better long-term model is:

- facts can be marked `ignored`
- diagnostics explain why
- users can tune graph views later without destroying source observations

---

## 9. Query and UI Changes

### 9.1 Query Engine

Short-term:

- keep the query engine reading `relationships`
- ensure semantic edges in `relationships` are the materialized output of resolution

Medium-term:

- add debug queries for fact inspection
- allow querying unresolved or ignored facts for diagnostics

Examples:

- `get_unresolved_calls(symbol_id)`
- `get_relationship_diagnostics(rel_id)`
- `stats_by_resolution()`

### 9.2 Browser Explorer

The browser should remain a consumer of the graph, not the place where resolution logic lives.

Recommended improvements after backend fixes:

- distinguish `resolved` vs `probable` edges visually
- show only `CALLS` counts in caller/callee summary
- expose caller/callee lists, not only counts
- optionally expose unresolved raw-call diagnostics in the detail panel

Important:

- UI improvements should follow data-model correctness, not lead it

---

## 10. Implementation Plan

This should be delivered in stages so the repo remains usable throughout.

### Phase 0: Baseline and Metrics

Before redesign work:

- capture current stats for one or more real repos such as `jarvis`
- record counts by relationship type and resolution status
- add a small debug report command or script if needed

Deliverables:

- baseline unresolved counts
- representative failure examples
- regression dataset for validation

### Phase 1: Introduce Fact Storage

Add:

- schema support for `relationship_facts`
- store APIs to insert and query facts
- models for fact records and fact statuses

Keep existing relationship storage intact.

Deliverables:

- DB migration
- storage-layer support
- tests for fact persistence

### Phase 2: Refactor Python Extraction

Change the Python analyzer / visitor to:

- keep symbol extraction
- keep `CONTAINS` edge emission
- pre-register class members
- emit raw call/import/inheritance facts
- stop doing most semantic resolution inline

Deliverables:

- updated `visitor.py`
- updated analyzer outputs
- tests for raw fact extraction

### Phase 3: Add First Resolver Passes

Implement:

- `ImportResolver`
- `InFileCallResolver`
- `ClassMemberCallResolver`
- `ConstructorCallResolver`

Materialize resulting `IMPORTS` and `CALLS` edges into `relationships`.

Deliverables:

- resolver module(s)
- resolution diagnostics
- tests for `self.method()` and `ClassName()`

### Phase 4: Cross-File Call Resolution

Implement stronger cross-file `CALLS` resolution using:

- imported names
- aliases
- same-module names
- suffix/simple-name fallback only when safe

Deliverables:

- improved `CALLS` resolution rate
- tests for imported and cross-file call paths

### Phase 5: Inheritance Resolution Migration

Move `EXTENDS` / `IMPLEMENTS` onto the same fact-resolution-materialization pipeline.

Deliverables:

- inheritance fact extraction
- inheritance resolver
- tests for ABC / Protocol / concrete class cases

### Phase 6: Diagnostics and Explorer Polish

Once relationship correctness improves:

- surface resolution status in API responses
- fix detail panel counts to be relationship-type-specific
- expose caller/callee lists
- optionally show unresolved facts in debug detail views

Deliverables:

- server/API changes
- app.js rendering improvements
- browser verification

### Phase 7: Cleanup

After the new pipeline is stable:

- remove obsolete inline resolution code
- simplify old cross-file resolution entry points
- update README and architecture notes

---

## 11. Module-by-Module Change Plan

### `src/node_walk/analysis/python/visitor.py`

Change responsibilities:

- keep symbol and containment extraction
- replace inline `CALLS` edge creation with raw call facts
- add class-member pre-registration support
- emit richer call metadata such as receiver text and local scope hints

### `src/node_walk/analysis/python/`

Potential additions:

- `facts.py` for raw fact models or helpers
- `extractors.py` if visitor responsibilities need splitting

### `src/node_walk/indexer.py`

Change responsibilities:

- orchestrate fact extraction and storage
- run resolver pipeline after store writes
- materialize final relationships after resolver passes

It should stop being a generic "scan unresolved relationships and guess by target name" loop.

### `src/node_walk/storage/base.py`

Add methods for:

- storing fact records
- listing pending facts
- updating fact resolution outcomes
- rematerializing relationships from facts

### `src/node_walk/storage/sqlite_store.py`

Add:

- `relationship_facts` CRUD
- targeted lookup helpers for resolver passes
- stats grouped by fact type and resolution status

### `src/node_walk/query/engine.py`

Short-term:

- minimal or no changes for primary graph queries

Medium-term:

- add diagnostic helpers for unresolved/probable facts

### `src/node_walk/web/server.py`

After backend redesign:

- caller/callee counts should use only `CALLS`
- include caller/callee lists if desired
- expose resolution status where helpful

### `src/node_walk/web/app.js`

After backend redesign:

- style `probable` edges distinctly
- render caller/callee lists as navigable items
- optionally show unresolved diagnostics in symbol detail

---

## 12. Testing Strategy

### 12.1 Unit Tests

Add focused tests for:

- pre-registration of class members
- raw call fact extraction
- `self.method()` resolution
- `cls.method()` resolution if supported
- `ClassName()` constructor resolution
- import-alias-based resolution
- ambiguous simple-name calls remaining `probable` or `unresolved`
- ignored builtin calls

### 12.2 Storage Tests

Add tests for:

- fact insertion and retrieval
- fact status updates
- materialized relationship generation
- re-running resolution without re-parsing

### 12.3 Integration Tests

End-to-end tests should cover:

- index a small fixture repo
- inspect facts
- inspect materialized graph
- verify query engine callers/callees

### 12.4 Explorer/API Tests

Add coverage for:

- `/api/symbol/{id}` returns `CALLS`-specific counts
- caller/callee lists, if implemented
- edge resolution status included in graph payloads

### 12.5 Real-Repo Verification

Use at least one real codebase, such as `jarvis`, to validate:

- total `CALLS` edge counts
- resolved/probable/unresolved distribution
- important symbols that were previously disconnected now show meaningful neighbors

---

## 13. Migration Strategy

The migration should avoid a hard break.

### Step 1

Add new tables and code paths without removing old ones.

### Step 2

Write both:

- raw facts
- legacy relationships where still needed

This gives a transition window for verification.

### Step 3

Switch query-facing semantic relationships to materialized outputs from resolvers.

### Step 4

Remove old inline-resolution logic once metrics show parity or improvement.

### Step 5

Delete obsolete fallback code and update docs.

---

## 14. Risks and Tradeoffs

### Risk: Scope Creep

Once fact storage exists, it is easy to expand into full static-analysis ambitions.

Mitigation:

- keep V1 focused on Python semantic relationship correctness
- avoid full type inference

### Risk: Duplicate Data During Migration

Storing facts and materialized edges increases DB complexity temporarily.

Mitigation:

- accept some duplication during migration
- document which table is source-of-truth for which layer

### Risk: Resolver Complexity

Moving logic out of the visitor improves architecture but increases moving parts.

Mitigation:

- keep passes small and relationship-specific
- add diagnostics explaining each resolution decision

### Risk: Over-Aggressive Noise Filtering

This can silently delete valid edges.

Mitigation:

- start with tiny ignore sets
- prefer marking a fact `ignored` with diagnostics over dropping it completely

---

## 15. Recommended First Slice

If this work needs to start with the highest-value, lowest-risk slice, do this first:

1. Add fact storage for raw calls.
2. Pre-register class members in Python extraction.
3. Move `self.method()` resolution into a dedicated resolver.
4. Add constructor resolution for `ClassName()`.
5. Materialize final `CALLS` edges from those results.
6. Re-index `jarvis` and compare metrics.

Why this slice first:

- it fixes the most visible graph correctness issue
- it validates the new architecture without redesigning everything at once
- it gives a measurable before/after result quickly

---

## 16. Acceptance Criteria

This redesign can be considered successful when:

- unresolved `CALLS` drop substantially on real repositories
- major same-class and constructor call paths are visible in the graph
- fact diagnostics make remaining misses understandable
- graph consumers do not need to know resolver internals
- improving resolution logic no longer requires re-parsing the repository

---

## 17. Summary

The right fix is not just "make the current resolver smarter." The better long-term design is:

- parse once
- store raw semantic facts
- resolve in explicit passes
- materialize the graph from those results

That gives `node-walk` a graph it can trust, a pipeline it can debug, and a foundation that can grow into stronger semantic analysis without rewriting the whole product again.
