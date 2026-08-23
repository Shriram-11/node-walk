# Changelog

All notable changes to **node-walk** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.3.0] - 2026-08-23

---

## [0.2.0] - 2026-08-23
### Added
- **`node-walk serve` command**: Launches an embedded local HTTP server (`localhost:7777` by default) and auto-opens the browser to the interactive graph explorer. Accepts `--port`, `--host`, and `--no-open` flags.
- **Graph Explorer UI** (`src/node_walk/web/`): A single-page browser application (no build step, no npm) that renders the full code graph using Cytoscape.js (CDN):
  - Force-directed layout with manual drag support.
  - Nodes coloured and shaped by `SymbolKind`; edges styled and coloured by `RelationshipType`.
  - Click node → highlight it and direct neighbours + show detail panel (name, kind, file, lines, signature, docstring, source snippet).
  - Double-click node → lazy 1-hop neighbour expansion via `/api/neighbors`.
  - Right-click context menu: expand neighbours, focus subtree, hide node, reset focus.
  - Debounced search bar backed by `/api/search` → centres and selects the top match.
  - Filter panel (checkboxes) to show/hide nodes by `SymbolKind` and edges by `RelationshipType`; `CONTAINS` edges hidden by default.
  - Tooltip on hover for nodes and edges.
  - Fit-to-viewport and re-layout buttons.
  - Status bar showing visible node/edge counts and selected symbol name.
- **Web API** (`src/node_walk/web/server.py`): Zero-dependency HTTP server (Python `http.server`) exposing:
  - `GET /api/graph[?kinds=…&rels=…]` — full graph for initial render, with optional kind/relationship filters.
  - `GET /api/symbol/{id}` — symbol detail including source snippet, caller/callee counts.
  - `GET /api/neighbors/{id}[?direction=out|in|both&rels=…]` — 1-hop neighbours for lazy canvas expansion.
  - `GET /api/search?q=…` — fuzzy symbol search (reuses `QueryEngine.find_symbol`).
  - `GET /api/stats` — graph statistics.
- **API tests** (`tests/test_web_server.py`): Full test suite covering all five endpoints (schema validation, filter params, 404 handling, score ranges) plus a full smoke round-trip and static file serving tests.

### Changed
- **`pyproject.toml`**: Added `force-include` entries so `index.html`, `style.css`, and `app.js` are bundled in the installed wheel.
- **`node-walk help`**: Added "Browser Explorer" section listing the `serve` command.


## [0.1.1] - 2026-08-20

### Added
- **Dotted-Path Symbol Search**: Support searching for scoped symbols (e.g. `ModelAdapter.chat`, `UserService.create_user`) matching child symbols within parents without needing full module paths.
- **Fuzzy / Typo Matching**: Added `difflib.SequenceMatcher` fallback across symbols to handle typos (e.g. `ModelAdpater.chat`, `creat_user`), returning match scores and `(fuzzy)` indicators.
- **Graph Visualizations**: Added ASCII tree, Graphviz DOT (`.dot`), and Mermaid diagram formatters in `tree_formatter.py`.
- **`graph` Command**: Added `node-walk graph <symbol>` for general BFS neighborhood exploration with customizable direction (`out`, `in`, `both`) and format (`tree`, `dot`, `mermaid`, `table`).
- **Formatting Options**: Added `--format` / `-f` (`tree`, `dot`, `mermaid`, `table`) and `--output` / `-o` flags to `trace` and `blast-radius` commands.
- **Automated Versioning in CI**: Updated GitHub Actions release workflow with dynamic version resolution (tag triggers or `patch`/`minor`/`major` dispatch inputs).

### Changed
- **Branding**: Renamed project and CLI binaries from `CodeGraph` to `node-walk`.
- **Packaging**: Switched `pyproject.toml` to dynamic versioning via Hatchling reading from `src/node_walk/__init__.py`.
- **CLI Resolution**: Auto-select top candidate when an unambiguous high-confidence match is found instead of prompting unnecessarily.
- **Documentation**: Updated `README.md` to reflect all currently supported features, traversal modes, and CLI commands.

---

## [0.1.0] - 2026-08-20

### Added
- **Tree-sitter Python Analyzer**: High-speed AST analysis extracting files, classes, methods, functions, constants, variables, fields, docstrings, signatures, and call sites.
- **Language-Independent Code IR**: Pydantic v2 data models for `Symbol`, `Relationship`, `FileInfo`, `SourceLocation`, and `AnalysisResult`.
- **Typed Semantic Relationships**: Support for `CONTAINS`, `IMPORTS`, `CALLS`, `REFERENCES`, `EXTENDS`, `IMPLEMENTS`, and `OVERRIDES`.
- **SQLite Storage Backend**: Local SQLite database with WAL mode, foreign keys, and 10 query indexes.
- **Recursive CTE Query Engine**: In-database bounded graph traversals (`walk`, `trace`, `blast_radius`) using recursive SQL CTEs.
- **Rich CLI**: Typer-based command-line interface with custom themed tables and syntax highlighting:
  - `index`: Analyze and index a codebase.
  - `find`: Search symbols by name or qualified name.
  - `definition`: Show symbol definition and metadata.
  - `source`: Extract and display the exact source code block of any symbol.
  - `callers` & `callees`: Discover direct upstream and downstream call sites.
  - `refs`: Locate symbol references.
  - `implementations`: Find concrete classes extending or implementing interfaces/ABCs.
  - `imports`: Inspect imported modules and symbols.
  - `trace`: Follow outgoing dependency chains.
  - `blast-radius`: Follow incoming dependent chains.
  - `stats`: Display database statistics and symbol/relationship breakdowns.
  - `export`: Export the complete semantic graph to JSON.
  - `help`: Custom command cheat sheet.
