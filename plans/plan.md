**CODEGRAPH**

Comprehensive Product & Architecture Plan

_Semantic Code Intelligence for Humans and LLMs_

Local-first • Lightweight • Polyglot by architecture • Single-language V1

# 1\. Intention

CodeGraph is a local developer tool that builds a semantic, machine-readable model of a codebase and makes that model useful to both humans and AI agents. The core problem is not simply finding text in files; it is understanding what a piece of code is, where it lives, and how it relates to the rest of the system.

The product should make unfamiliar or large codebases easier to navigate without requiring users or LLMs to repeatedly grep, open files, follow symbols manually, and reconstruct relationships in context.

# 2\. Product Vision

Build a lightweight local 'semantic map' of software. A developer can visually explore the map, while an LLM can use small navigation skills to traverse the same underlying graph and retrieve precise source locations.

Core principle:

**The semantic code graph is the product's brain.** Visualization, CLI/TUI, and LLM skills are consumers of that graph.

# 3\. What We Are Building

- A repository analyzer that discovers files, symbols, scopes, definitions, references, and relationships.
- A language-independent Code IR (intermediate representation) that normalizes information from different programming languages.
- A local semantic graph stored in SQLite for V1.
- A query engine for structural navigation: definitions, callers, callees, references, implementations, imports, traces, and blast radius.
- A lightweight local browser visualization for interactive exploration.
- A lightweight CLI for indexing, querying, debugging, and launching the visual explorer.
- LLM-oriented scripts/skills that expose focused graph-navigation capabilities and exact source locations.
- A future path for Git history, runtime traces, architecture analytics, and ML-based code intelligence.

# 4\. What We Are Not Building

- Not an LLM wrapper that merely sends repository text to a model.
- Not a generic RAG chatbot for code.
- Not a cloud-hosted code analysis platform for V1.
- Not a giant graph database/distributed system.
- Not a multi-language parser implementation from day one.
- Not a visualization that dumps the entire repository into a spaghetti graph.

# 5\. Primary Use Cases

| **Use case**         | **Example**                                    | **Expected result**                            |
| -------------------- | ---------------------------------------------- | ---------------------------------------------- |
| Understand a symbol  | Where is UserService.createUser defined?       | Exact file and line range + context            |
| Find callers         | What uses UserRepository.save?                 | Resolved callers with locations                |
| Find callees         | What does createUser call?                     | Direct and recursive downstream relationships  |
| Trace behavior       | How does a request reach the database?         | Relevant path through the graph                |
| Find implementations | Where is Repository&lt;T&gt; implemented?      | Concrete implementations                       |
| Assess blast radius  | What could be affected if this method changes? | Dependents and configurable depth              |
| Explore architecture | What are the major modules and boundaries?     | Aggregated visual map                          |
| Support an LLM       | Help an agent understand an unfamiliar service | Compact structural facts + exact source ranges |

# 6\. Design Principles

- Local-first: repository analysis and graph data stay on the developer's machine by default.
- Fast feedback: indexing and common queries should feel like a normal developer CLI operation.
- Graph correctness over UI polish: incorrect relationships destroy trust.
- Typed relationships: CALLS is different from PUBLISHES, READS, WRITES, or CONSUMES.
- Lazy exploration: load only the neighborhood or path the user/agent is exploring.
- Language-independent core: language adapters produce a shared IR.
- Thin consumers: the UI and LLM skills should query the same query engine rather than duplicate logic.
- Progressive enrichment: static analysis first; Git, runtime, service metadata, and ML later.

# 7\. System Architecture

High-level flow:

**Repository → Language Analysis → Code IR → Semantic Graph → Query Engine → (Browser / CLI-TUI / LLM Skills)**

## 7.1 Language Analysis Layer

This layer is language-specific. It parses source files, extracts syntax and symbols, establishes scopes, and resolves relationships as far as the language semantics allow.

- File discovery and language detection
- Parsing into syntax trees
- Symbol extraction
- Scope and namespace analysis
- Import/module resolution
- Reference resolution
- Call-site extraction
- Inheritance/interface resolution where supported
- Source-location tracking

## 7.2 Code IR

The Code IR is the contract between language-specific analyzers and the rest of the system. It must be stable and language-independent enough that the graph/query/UI layers do not care whether a symbol originated in Python, Java, Go, or TypeScript.

Core symbol fields:

- id
- name
- qualified_name
- kind
- language
- file_id
- start_line
- end_line
- signature
- parent_id

Core relationship fields:

- source_id
- target_id
- type
- source_location
- metadata

## 7.3 Semantic Graph

Initial symbol types:

- FILE
- MODULE
- PACKAGE
- CLASS
- INTERFACE
- FUNCTION
- METHOD
- VARIABLE
- CONSTANT
- FIELD

Initial relationship types:

- CONTAINS
- IMPORTS
- CALLS
- REFERENCES
- EXTENDS
- IMPLEMENTS
- OVERRIDES

Future relationship types:

- READS
- WRITES
- PUBLISHES
- CONSUMES
- HTTP_CALLS
- QUERIES
- CONFIGURED_BY
- DEPENDS_ON

## 7.4 Storage

Use SQLite for V1. It keeps installation simple, works locally, supports indexes and transactions, and is more than adequate for an initial developer tool. The storage layer must be abstracted so it can be replaced if profiling later proves a different backend is necessary.

## 7.5 Query Engine

The query engine is the only layer that should directly depend on graph storage.

- find_symbol(query)
- get_definition(symbol)
- get_callers(symbol)
- get_callees(symbol)
- get_references(symbol)
- get_implementations(symbol)
- get_importers(symbol)
- get_imports(symbol)
- get_children(symbol)
- get_parents(symbol)
- walk(start, relationship, direction, depth)
- trace(start, depth, filters)
- blast_radius(start, depth, relationship filters)
- get_source(symbol or location)

# 8\. Language Strategy: Polyglot Architecture, One Language V1

The product should be designed for multiple languages but initially implement one language well. The purpose is to validate the graph model and developer experience before multiplying the complexity of symbol resolution.

| **Phase** | **Language**          | **Purpose**                                                  |
| --------- | --------------------- | ------------------------------------------------------------ |
| V1        | Python                | Fast iteration; accessible AST tooling; validate graph model |
| V2        | TypeScript/JavaScript | Large real-world web codebases; valuable polyglot test       |
| V3        | Java or Go            | Static typing, interfaces, large backend systems             |
| Later     | More languages        | Add adapters only after the IR/query model proves stable     |

Tree-sitter is the preferred common parsing technology for the multi-language direction. However, language-specific parsers or compiler APIs may be used where they provide substantially better semantic information. Parsing is not the same as symbol resolution; resolution is expected to be the harder part.

# 9\. Technology Stack

| **Layer**           | **Technology**                           | **Reason**                                                             |
| ------------------- | ---------------------------------------- | ---------------------------------------------------------------------- |
| Core analyzer       | Python                                   | Fast iteration and strong ecosystem for analysis/ML                    |
| Parsing             | Tree-sitter                              | Common parsing approach across languages                               |
| IR/model            | Python dataclasses or Pydantic           | Explicit, validated data model                                         |
| Storage             | SQLite                                   | Local, lightweight, zero infrastructure                                |
| Graph algorithms    | NetworkX initially; custom later         | Fast experimentation before optimizing                                 |
| Git                 | Git CLI/subprocess initially             | Avoid unnecessary dependency; reliable local Git                       |
| CLI                 | Typer                                    | Simple typed Python CLI                                                |
| TUI                 | Textual                                  | Optional terminal explorer/debugging interface                         |
| Browser UI          | TypeScript + lightweight graph library   | Use browser rendering without shipping a heavy desktop runtime         |
| Graph visualization | Cytoscape.js or Sigma.js; evaluate early | Interactive graph rendering with manageable frontend footprint         |
| Local server        | Starlette/FastAPI or minimal HTTP server | Serve bundled static UI and query endpoints                            |
| LLM integration     | Python skills/scripts; MCP adapter later | Focused structural navigation without coupling core to an LLM platform |
| Testing             | pytest + golden graph fixtures           | Validate analyzer correctness                                          |

# 10\. Visualization Architecture

The browser is intentionally used as a lightweight local rendering surface. Users should not need Node.js, Docker, a cloud account, or a separate visualization service. The Python package can serve bundled static assets from localhost.

User experience:

1. Install the package.
2. Index a repository.
3. Launch the local explorer.
4. Search for any symbol/entity.
5. Open a neighborhood around it.
6. Expand relationships lazily.
7. Switch between semantic views without changing the underlying graph.

Primary views:

- Neighborhood: immediate graph around the selected entity.
- Flow/Journey: a directional path through selected relationship types.
- Architecture: aggregated modules/packages/services rather than individual methods.
- Blast radius: dependents highlighted by relationship and depth.
- Hotspots: later view based on usage, change frequency, coupling, or other metrics.
- Source panel: exact file/line range alongside the visual graph.

The frontend must never reconstruct graph semantics itself. It requests a graph slice from the query engine and renders the returned nodes/edges.

# 11\. LLM Integration

The LLM is not given a giant graph dump and does not need to understand the visualization. It receives focused navigation skills/scripts that query the semantic graph and return compact, deterministic structural information.

Initial skills:

- find_symbol — resolve a natural-language or exact symbol name to candidates.
- definition — return the canonical symbol and exact source location.
- callers — find direct callers.
- callees — find direct callees.
- references — find usages/references.
- implementations — resolve implementations/subclasses.
- walk/trace — traverse selected relationship types to a bounded depth.
- source — retrieve exact source ranges for selected symbols.
- blast_radius — identify potentially affected dependents.

The scripts should be composable. A small set of reliable primitives is preferable to dozens of hard-coded workflows.

Example agent workflow: find symbol → inspect definition → query callers/callees → follow the relevant branch → retrieve exact source. This replaces repeated grep/open/grep cycles with semantic navigation while still allowing the model to inspect source when necessary.

# 12\. CLI and Installation Experience

Target experience:

pip install codegraph

codegraph index .

codegraph find createUser

codegraph callers UserService.createUser

codegraph callees UserService.createUser

codegraph trace UserService.createUser

codegraph explore

The CLI is also a development/debugging interface for the engine. Even if the primary human experience is the browser, the CLI makes graph correctness testable without the frontend.

# 13\. Repository Data Layout

Recommended local cache:

- .codegraph/
- .codegraph/graph.db
- .codegraph/metadata.json
- .codegraph/cache/

The generated graph should be local and disposable by default. A global cache can be added later for repositories where users prefer not to store generated data in the project directory.

# 14\. Indexing and Incremental Updates

The initial implementation can perform full indexing. Very quickly after correctness is established, incremental indexing should become a priority.

- Detect changed files using Git/file timestamps or content hashes.
- Reparse only changed files.
- Invalidate affected symbols and relationships.
- Re-resolve relationships whose targets/imports may have changed.
- Persist graph state so subsequent runs are fast.

Longer-term goal: an initial full index followed by near-instant incremental updates for normal edit cycles.

# 15\. Correctness Strategy

Graph correctness is the central quality attribute. The project should use small source fixtures with expected symbols and relationships.

- Golden repository fixtures: input source → expected graph.
- Unit tests for parsing and symbol extraction.
- Unit tests for scope/reference resolution.
- Relationship tests for calls, imports, inheritance, and overrides.
- Regression fixtures for every discovered resolution bug.
- Performance benchmarks on progressively larger repositories.
- Explicit representation of uncertainty for relationships that cannot be statically resolved.

# 16\. Performance Targets (Initial Goals)

These are engineering targets, not guarantees; benchmark against representative repositories before locking them in.

| **Area**           | **Target direction**                                           |
| ------------------ | -------------------------------------------------------------- |
| Installation       | Single package; no external infrastructure                     |
| First index        | Fast enough to be practical on ordinary developer repositories |
| Incremental index  | Only changed/affected portions reprocessed                     |
| Common graph query | Interactive response on local repository                       |
| Visualization      | Load small graph slices lazily rather than entire repository   |
| LLM skill output   | Compact, deterministic, source-location aware                  |

# 17\. Development Roadmap

## Phase 0 — Foundations

- Define IR and relationship taxonomy.
- Create repository/package structure.
- Set up SQLite schema and migrations/versioning.
- Create analyzer interfaces and test fixtures.

## Phase 1 — Python Static Graph

- Integrate Tree-sitter.
- Discover files and parse syntax trees.
- Extract files/classes/functions/methods/variables.
- Track exact source locations.
- Implement imports and basic call/reference resolution.

## Phase 2 — Query Engine

- Implement symbol resolution API.
- Implement callers/callees/references/implementations.
- Implement bounded graph walks and trace.
- Implement source retrieval.
- Build CLI commands around queries.

## Phase 3 — Lightweight Browser

- Bundle a minimal frontend.
- Implement search and selected-symbol view.
- Implement neighborhood visualization.
- Add lazy expansion.
- Add source panel and navigation to files.

## Phase 4 — LLM Skills

- Build focused navigation scripts.
- Define compact output schemas.
- Test with representative code-understanding tasks.
- Measure token usage and navigation efficiency against grep-based workflows.

## Phase 5 — Incremental Indexing

- Changed-file detection.
- Graph invalidation/update.
- Caching and performance work.
- Large repository benchmarks.

## Phase 6 — Git Intelligence

- Commit/file/symbol history.
- Change frequency.
- Authors and recent modifications.
- Hotspot metrics.

## Phase 7 — Polyglot Expansion

- Add TypeScript/JavaScript.
- Validate IR across languages.
- Add Java or Go.
- Improve cross-language/service relationships where possible.

## Phase 8 — Runtime and Advanced Intelligence

- Optional runtime traces.
- Combine static and runtime graphs.
- Risk/blast-radius scoring.
- ML experiments for anomaly/risk prediction.

# 18\. Required Components / Work Breakdown

- Project/package scaffolding
- Language adapter interface
- Tree-sitter integration
- File discovery and language detection
- AST traversal utilities
- Symbol extraction
- Scope model
- Reference resolver
- Call resolver
- Inheritance/interface resolver
- Code IR
- SQLite schema and storage layer
- Graph query engine
- Graph traversal algorithms
- Source retrieval
- CLI
- Browser static asset bundling
- Local server
- Graph visualization
- Search UI
- Source viewer
- LLM navigation skills
- Golden test fixtures
- Benchmark suite
- Documentation

# 19\. Risks and Hard Problems

| **Risk**                   | **Why it matters**                                                        | **Mitigation**                                                                          |
| -------------------------- | ------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Symbol resolution          | Dynamic languages and ambiguous names make exact relationships difficult. | Represent confidence/uncertainty; start with tractable cases; preserve source evidence. |
| Cross-language consistency | Different languages express concepts differently.                         | Keep IR small and semantic; use language-specific metadata when necessary.              |
| Graph explosion            | Large repositories can create huge neighborhoods.                         | Lazy traversal, bounded depth, aggregation, filtering.                                  |
| Incorrect relationships    | A wrong edge can mislead both humans and LLMs.                            | Golden fixtures, regression tests, explicit unresolved states.                          |
| Incremental indexing       | Changes can affect relationships beyond the changed file.                 | Track dependency invalidation; add complexity only after full indexing works.           |
| Visualization overload     | Large graphs become unreadable.                                           | Neighborhood-first UI, aggregation, filtering, multiple views.                          |
| LLM context bloat          | Dumping graph data can be as bad as dumping source.                       | Compact outputs, bounded traversal, progressive disclosure.                             |

# 20\. How We Know It Works

- A developer can find a symbol and jump to its exact source location.
- A developer can start from any supported entity, not only controllers/endpoints.
- The system can answer callers, callees, references, implementations, and bounded traversal queries.
- A developer can visually explore a local neighborhood without rendering the whole repository.
- An LLM can navigate an unfamiliar repository using skills and source retrieval rather than repeated grep calls.
- Graph answers are measurably more compact than equivalent raw-source retrieval for structural questions.
- Indexing and common queries remain practical on real repositories.
- Adding a second language requires a new language adapter rather than rewriting the core.

# 21\. Future Extensions

- Service-to-service and API dependency graphs.
- Database schema and query relationships.
- Message broker/event relationships.
- Runtime execution traces.
- Architecture drift detection.
- Dead-code and unused dependency analysis.
- Code ownership and change-risk analytics.
- Semantic similarity and code embeddings.
- ML models for change-risk prediction or anomaly detection.
- IDE integrations for VS Code and JetBrains.

# 22\. Final Architecture Decision

The recommended product architecture is a local-first semantic code intelligence engine with a polyglot-ready language layer, a language-independent Code IR, SQLite-backed graph storage, a reusable query engine, a lightweight browser explorer, and focused LLM navigation skills. V1 should target Python deeply enough to validate the model, while the interfaces are designed so additional languages can be added through adapters.

**Core stack:** Python + Tree-sitter + Code IR + SQLite + Query Engine + lightweight local browser + LLM skills/scripts.

**Core product promise:** Turn a codebase into a navigable semantic map that is useful to both humans and AI.

# Appendix A — Example End-to-End Flow

A developer points CodeGraph at a repository:

codegraph index ./repo

The analyzer produces symbols and relationships. The graph is stored locally. The developer launches:

codegraph explore

The browser asks the query engine for small graph slices as the developer searches and expands nodes. Separately, an LLM uses the navigation skills to find a symbol, inspect its callers/callees, follow relevant relationships, and retrieve exact source ranges. Both experiences rely on the same semantic model.

# Appendix B — Suggested V1 Success Demo

1. Index a medium-sized Python repository.
2. Search for a method by name.
3. Open its definition and exact location.
4. Show its callers and callees.
5. Expand two levels of the neighborhood.
6. Switch to a flow view.
7. Ask an LLM skill to identify the method and retrieve its callers.
8. Have the LLM follow one branch and retrieve the final source locations.
9. Compare the number of files/tokens needed with a grep-driven baseline.