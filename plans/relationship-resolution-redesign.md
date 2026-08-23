# Relationship Resolution Redesign Plan

**Refactor plan for reliable `CALLS` / `IMPORTS` / inheritance resolution in `node-walk`.**

Local-first, correctness-first, incremental migration.

---

## 0. Handoff Status

This section is the quick-start handoff summary for parallel implementation work.

### Completed So Far

The following work has already been implemented in the repo:

- fact enums and models were added
- `relationship_facts` SQLite storage was added
- storage APIs for persisting and updating raw facts were added
- `AnalysisResult` now supports `relationship_facts`
- the Python analyzer now returns raw relationship facts
- the Python visitor now emits raw call facts
- class methods are pre-registered before method-body analysis
- same-class forward references like `self.method_b()` can now resolve better during extraction
- tests were updated and are passing for storage, query engine, and Python analyzer

### Confirmed Decisions

These decisions were explicitly discussed and should be treated as settled unless a new decision is made:

- old edge-emission behavior can be changed or removed if needed
- backward compatibility with the old inline semantic-resolution approach is not required if it blocks a cleaner resolver pipeline
- legacy `CALLS` edge emission inside the Python visitor is transitional, not final architecture
- it is acceptable to replace old resolution paths rather than preserve them indefinitely

### Current Transitional State

The codebase is currently in a mixed migration state:

- raw call facts are now extracted and stored
- legacy `CALLS` relationships are still emitted from the visitor for compatibility
- some in-file resolution still happens during extraction
- the dedicated resolver/materialization pipeline has started, but is not complete yet

This means consumers still work, but the architecture is intentionally temporary until the resolver pipeline takes over semantic edge creation.

### Current Implementation Status

The next phase is no longer just planned; it has been partially started in the worktree.

Implemented or in progress:

- a new `src/node_walk/resolution/` package exists
- a resolver base abstraction has been added
- first call resolvers have been introduced
- `Indexer` has been updated to run resolver passes
- `Indexer` now materializes `CALLS` relationships from resolved facts

What is still incomplete in this phase:

- legacy visitor-emitted `CALLS` edges still exist alongside fact-materialized edges
- import-aware resolution has not been added yet
- cross-file call resolution is still limited
- inheritance has not been migrated onto the same pipeline
- duplicate-edge handling and cleanup strategy still need review

### Current Risks

Subagents should be aware of these active risks in the present implementation state:

- duplicate `CALLS` edges may exist because legacy and materialized paths are both active
- resolver ordering may affect outcomes while the pipeline is still incomplete
- noise filtering is currently more aggressive than the long-term plan recommends
- some logic is still split between extraction-time resolution and resolver-time resolution

These are expected migration risks, not necessarily regressions, but they should be treated as cleanup targets in the next steps.

### Next Recommended Work

The highest-priority next step is:

- build the resolver pipeline
- read pending call facts from storage
- materialize `CALLS` relationships from resolver output
- then remove legacy inline call-resolution logic from the visitor

### Guidance for Subagents

Agents should treat this plan as the source of truth for target architecture, but should not preserve transitional code paths unless they still serve migration safety.

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

---

## 18. Concrete Implementation Checklist

This section turns the redesign into an execution plan tied to the current codebase.

### 18.1 Phase A: Establish Baseline Metrics

Purpose:

- capture current behavior before changing architecture
- create a regression target for future iterations

Files to touch:

- `src/node_walk/storage/sqlite_store.py`
- optional new helper: `src/node_walk/debug/` or a small CLI/report script
- `tests/` for metric-oriented integration fixtures if desired

Tasks:

- add helper queries for counts by:
  - relationship type
  - resolution status
  - unresolved semantic relationships
- add a report path for:
  - total `CALLS`
  - resolved `CALLS`
  - probable `CALLS`
  - unresolved `CALLS`
- capture one baseline snapshot for `jarvis`

Definition of done:

- we can measure before/after improvements without manual SQL spelunking

### 18.2 Phase B: Introduce Fact Models and Schema

Purpose:

- create a storage layer for raw semantic observations

Files to touch:

- `src/node_walk/ir/models.py`
- optional new file: `src/node_walk/ir/facts.py`
- `src/node_walk/storage/schema.py`
- `src/node_walk/storage/base.py`
- `src/node_walk/storage/sqlite_store.py`
- storage-related tests

Tasks:

- add model(s) for raw relationship facts, for example:
  - `RelationshipFact`
  - `FactType`
  - `FactStatus`
- add SQLite schema for `relationship_facts`
- add storage APIs such as:
  - `store_fact(...)`
  - `store_facts(...)`
  - `get_facts_by_type(...)`
  - `get_pending_facts(...)`
  - `update_fact_resolution(...)`
  - `clear_materialized_relationships(...)` if needed

Design notes:

- keep the existing `relationships` table intact
- do not remove or rename current graph-facing storage yet

Definition of done:

- raw semantic facts can be stored and queried independently of final graph edges

### 18.3 Phase C: Refactor Python Extraction to Emit Facts

Purpose:

- move semantic observation out of final-edge creation

Primary files to touch:

- `src/node_walk/analysis/python/visitor.py`
- `src/node_walk/analysis/python/__init__.py` if exports need updating
- `src/node_walk/analysis/python/` for any new extraction helpers
- `src/node_walk/ir/models.py` or `ir/facts.py`
- analyzer tests

Tasks inside `visitor.py`:

- keep symbol extraction logic
- keep `CONTAINS` relationship emission
- stop emitting most final `CALLS` edges directly
- emit raw call facts instead
- stop relying on `_local_by_name` / `_local_by_qname` for final semantic resolution

Implementation subtasks:

- add helper to parse call shape into:
  - `call_text`
  - `callee_name`
  - `receiver_text`
  - `qualified_hint`
- add helper to emit raw import facts
- add helper to emit raw inheritance facts

Definition of done:

- parsing produces symbols, structural edges, and raw semantic facts

### 18.4 Phase D: Pre-Register Class Members Before Body Analysis

Purpose:

- fix forward references and improve class-local resolution

Primary files to touch:

- `src/node_walk/analysis/python/visitor.py`
- possibly `src/node_walk/analysis/python/scope.py`
- tests for class member registration and `self.method()` resolution

Tasks:

- add a class-member registry keyed by class symbol id or qualified name
- split class handling into:
  - class symbol registration
  - member pre-scan
  - body analysis
- ensure methods defined later in the class are visible when analyzing earlier methods

Suggested implementation shape:

- add a helper like `_collect_class_members(...)`
- register method and field symbols before descending into method call analysis
- preserve current containment relationships

Definition of done:

- `self.method_b()` inside `method_a()` can resolve even if `method_b()` is declared later

### 18.5 Phase E: Add Resolver Pipeline Entry Point

Purpose:

- replace the single generic cross-file resolver with an explicit staged pipeline

Primary files to touch:

- `src/node_walk/indexer.py`
- new package: `src/node_walk/resolution/`
- tests for orchestration

Suggested new files:

- `src/node_walk/resolution/__init__.py`
- `src/node_walk/resolution/base.py`
- `src/node_walk/resolution/calls.py`
- `src/node_walk/resolution/imports.py`
- `src/node_walk/resolution/inheritance.py`
- optional `src/node_walk/resolution/materialize.py`

Tasks:

- add a resolver orchestration step after storing symbols/facts
- keep `Indexer.index()` as the top-level flow owner
- deprecate direct dependence on `_resolve_cross_file()` as the main semantic resolution mechanism

Suggested indexer flow:

1. discover files
2. analyze files
3. store files, symbols, structural edges, and facts
4. run resolver passes
5. materialize final semantic relationships
6. report resolution stats

Definition of done:

- semantic resolution is an explicit pipeline, not a single name-matching loop

### 18.6 Phase F: Implement First Call Resolvers

Purpose:

- land the highest-value behavior improvements first

Primary files to touch:

- `src/node_walk/resolution/calls.py`
- `src/node_walk/storage/base.py`
- `src/node_walk/storage/sqlite_store.py`
- tests for resolver behavior

Resolvers to implement first:

- `InFileCallResolver`
- `ClassMemberCallResolver`
- `ConstructorCallResolver`

Tasks:

- resolve exact local function/method names within a file
- resolve `self.foo()` using enclosing class membership
- optionally resolve `cls.foo()` where supported
- resolve `ClassName()` to `ClassName.__init__` if available, else class symbol
- update fact status and diagnostics
- materialize `CALLS` relationships from resolved/probable facts

Definition of done:

- the most common same-file and same-class call relationships appear in the graph

### 18.7 Phase G: Implement Import Resolver

Purpose:

- create the namespace context needed for accurate cross-file calls

Primary files to touch:

- `src/node_walk/resolution/imports.py`
- `src/node_walk/storage/sqlite_store.py`
- `src/node_walk/indexer.py`
- tests for import facts and aliases

Tasks:

- resolve import facts into graph edges
- build a per-file import context map
- support:
  - `import pkg.module`
  - `import pkg.module as alias`
  - `from pkg import X`
  - `from pkg import X as Y`
  - relative imports used in current codebase fixtures

Definition of done:

- later resolver passes can ask "what names are visible in this file?"

### 18.8 Phase H: Implement Cross-File Call Resolution

Purpose:

- recover the bulk of meaningful unresolved `CALLS`

Primary files to touch:

- `src/node_walk/resolution/calls.py`
- `src/node_walk/storage/sqlite_store.py`
- `src/node_walk/indexer.py`
- resolver integration tests

Tasks:

- use import context for unqualified call names
- resolve module-qualified or alias-qualified calls
- apply suffix/simple-name fallback only when unambiguous
- mark ambiguous matches as `probable` or `unresolved`
- write clear diagnostics for misses

Definition of done:

- cross-file `CALLS` no longer depend on `metadata["target_name"]` alone

### 18.9 Phase I: Migrate Inheritance Resolution

Purpose:

- bring `EXTENDS` / `IMPLEMENTS` into the same fact-driven pipeline

Primary files to touch:

- `src/node_walk/analysis/python/visitor.py`
- `src/node_walk/resolution/inheritance.py`
- `src/node_walk/indexer.py`
- inheritance tests

Tasks:

- emit raw inheritance facts
- resolve base types after symbol collection
- materialize final `EXTENDS` / `IMPLEMENTS` edges
- use symbol kinds and interface markers to decide relationship type

Definition of done:

- inheritance resolution uses the same staged architecture as calls/imports

### 18.10 Phase J: Update Query Engine and Explorer API

Purpose:

- make consumers reflect the improved graph correctly

Primary files to touch:

- `src/node_walk/query/engine.py`
- `src/node_walk/web/server.py`
- `src/node_walk/web/app.js`
- API and UI tests

Tasks:

- ensure caller/callee counts use `RelationshipType.CALLS`
- optionally add:
  - caller list payloads
  - callee list payloads
  - resolution diagnostics for debug views
- expose edge resolution status consistently
- style `probable` edges differently in the UI

Definition of done:

- graph consumers show the improved backend truth without inflating counts

### 18.11 Phase K: Cleanup and Documentation

Purpose:

- remove migration scaffolding and document the new architecture

Primary files to touch:

- `README.md`
- `plans/plan.md` if architectural notes should be synchronized
- `src/node_walk/indexer.py`
- `src/node_walk/analysis/python/visitor.py`
- any obsolete resolver fallback code

Tasks:

- remove old inline semantic-resolution logic
- remove or simplify `_resolve_cross_file()` if superseded
- document the fact -> resolver -> materialized graph pipeline
- add operator guidance for re-resolution vs re-indexing

Definition of done:

- the codebase reflects one clear mental model instead of mixed old/new behavior

## 19. Suggested Work Order for the First PRs

If this should be broken into manageable pull requests, I would do it like this:

### PR 1: Baseline Metrics and Fact Schema

Scope:

- add fact models
- add `relationship_facts` schema
- add storage APIs
- add baseline metrics helpers

Why first:

- minimal behavior change
- establishes the new persistence layer safely

### PR 2: Python Extraction Refactor

Scope:

- emit raw call facts
- pre-register class members
- keep current graph mostly intact where needed

Why second:

- gets the parser producing the right raw data
- unlocks the new resolvers

### PR 3: First Call Resolvers and Materialization

Scope:

- in-file resolution
- `self.method()` resolution
- constructor resolution
- materialized `CALLS` edges from facts

Why third:

- delivers the first visible quality jump

### PR 4: Import-Aware Cross-File Resolution

Scope:

- import resolver
- alias handling
- cross-file call resolution

Why fourth:

- brings back many of the currently missing edges

### PR 5: Explorer/API Polish

Scope:

- caller/callee lists
- accurate counts
- resolution-aware styling

Why fifth:

- UI improvements land after backend correctness is real

### PR 6: Inheritance Pipeline and Cleanup

Scope:

- fact-based inheritance resolution
- remove obsolete fallback logic
- update docs

Why sixth:

- completes the architecture migration cleanly

## 20. Recommended First Coding Slice

If implementation starts immediately, the first slice I would code in this repo is:

1. Add `relationship_facts` schema and models.
2. Refactor `visitor.py` to emit raw call facts.
3. Pre-register class members in `visitor.py`.
4. Add a small `resolution/calls.py` with:
   - in-file exact resolution
   - `self.method()` resolution
   - constructor resolution
5. Materialize `CALLS` edges back into `relationships`.
6. Re-index `jarvis` and compare metrics.

That is the smallest slice that validates the new architecture and should materially improve graph usefulness.
