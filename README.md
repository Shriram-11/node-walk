# CodeGraph

**Semantic code intelligence for humans and LLMs.**

Local-first · Lightweight · Python first · SQLite backed

---

## Quickstart

### 1. Install & Setup
```bash
# Clone and setup the environment
git clone <repo-url> CodeGraph
cd CodeGraph
python -m venv .venv
.venv\Scripts\activate     # Windows (or source .venv/bin/activate on Unix)
pip install -e ".[dev]"
```

### 2. Indexing and Querying
All CLI commands discover the graph database by searching for a `.codegraph/` directory in the current directory or walking up parent directories.

**To index any directory:**
```bash
# Activate CodeGraph venv, then go to the repository you want to index
cd /path/to/your/project
codegraph index .
```

**Run queries within that project's directory:**
```bash
# Find a symbol
codegraph find UserService

# See its definition
codegraph definition UserService

# Find callers (shows interactive picker if multiple symbols match)
codegraph callers UserService.create_user

# Trace outgoing dependencies (out-edges)
codegraph trace UserService.create_user --depth 5

# Blast radius (in-edges - what breaks if this changes?)
codegraph blast-radius UserService.create_user

# View source code of a symbol
codegraph source UserService.create_user

# Graph statistics
codegraph stats

# Export to JSON
codegraph export --output graph.json
```


## Architecture

```
Repository → Language Analysis → Code IR → SQLite Graph → Query Engine → CLI
```

- **Language analysis**: Tree-sitter based Python parser
- **Code IR**: Pydantic v2 models — Symbol, Relationship, FileInfo
- **Storage**: SQLite with 10 indexes, WAL mode
- **Query engine**: Pure SQLite including recursive CTEs for graph traversal
- **CLI**: Typer + Rich

## Development

```bash
python -m venv .venv
.venv\Scripts\activate     # Windows
pip install -e ".[dev]"
pytest tests/ -v
```

## Graph data

Stored in `.codegraph/graph.db` inside the indexed repository.
Safe to delete and re-index at any time.
