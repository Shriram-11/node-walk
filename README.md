# node-walk

**Semantic code intelligence and graph navigation for Python codebases.**

Local-first · Lightweight · Fast indexing · SQLite backed · CLI & Visualizations

---

## What is node-walk?

`node-walk` parses your Python codebase into an Intermediate Representation (IR) graph stored locally in SQLite. It lets humans and LLMs query code relationships semantically instead of running repeated `grep` or text searches.

### Key Capabilities
- **Smart Symbol Search**: Find symbols by simple name (`chat`), qualified dotted path (`ModelAdapter.chat`), or fuzzy typo matching (`ModelAdpater.chat`).
- **Exact Source Retrieval**: View definitions, signatures, and exact source ranges instantly.
- **Relationship Navigation**: Find callers, callees, references, class implementations / ABCs, and imports.
- **Graph Traversal & Visualization**: Trace outgoing call chains and incoming blast radiuses rendered directly in terminal (ASCII tree), exported to Graphviz (`.dot`), or generated as Mermaid diagrams.
- **Lightweight & Self-Contained**: Pure Python + Tree-sitter + SQLite. Zero external database services or cloud dependencies.

---

## Installation

```bash
git clone <repo-url> node-walk
cd node-walk
python -m venv .venv
.venv\Scripts\activate     # Windows (.venv/bin/activate on Linux/macOS)
pip install -e ".[dev]"
```

---

## CLI Usage

All CLI commands discover the graph database by searching for `.node_walk/graph.db` in the current directory or walking up parent directories.

### 1. Index a repository
```bash
cd /path/to/your/project
node-walk index .
```

### 2. Search & Inspect Symbols
```bash
# Search by name, dotted path, or fuzzy typo
node-walk find UserService
node-walk find ModelAdapter.chat
node-walk find creat_user

# View symbol definition metadata
node-walk definition UserService.create_user

# View the exact source code block
node-walk source UserService.create_user
```

### 3. Explore Relationships
```bash
# Find callers of a function or method
node-walk callers UserService.create_user

# Find callees (what does this method call?)
node-walk callees UserService.create_user

# Find references/usages
node-walk refs User

# Find implementations / subclasses of an ABC or class
node-walk implementations BaseRepository

# Inspect imports
node-walk imports services.py
```

### 4. Graph Traversals & Visualizations
```bash
# Trace outgoing dependencies (tree, table, dot, mermaid)
node-walk trace UserService.create_user --depth 4 --format tree
node-walk trace UserService.create_user --format dot -o trace.dot
node-walk trace UserService.create_user --format mermaid

# Assess blast radius (what calls/depends on this?)
node-walk blast-radius UserService.create_user --format tree

# General graph exploration around any symbol
node-walk graph ModelAdapter --depth 3 --format tree
```

### 5. Utilities
```bash
# Show database statistics (symbol kinds, relationship counts)
node-walk stats

# Export the entire graph to JSON
node-walk export --output graph.json
```

---

## Architecture

```
Source Files (.py) ──► Tree-sitter Parser ──► Code IR (Pydantic v2) ──► SQLite Graph ──► Query Engine ──► CLI / Visualizers
```

- **Analysis**: Tree-sitter for robust AST parsing and symbol/call-site extraction.
- **Data Model**: Structured `Symbol`, `Relationship`, and `FileInfo` models.
- **Storage**: SQLite with WAL mode and indexes on names, qualified names, and relationships.
- **Traversals**: Recursive Common Table Expressions (CTEs) for fast BFS graph walks without loading graphs into memory.
- **Visualization Formats**: ASCII Tree, Graphviz DOT, and Mermaid markdown diagrams.

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Graph Storage & Lifecycle

The generated graph is stored in `.node_walk/graph.db` inside your indexed repository. It is disposable and can be re-indexed at any time with `node-walk index .`.
