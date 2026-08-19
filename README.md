# CodeGraph

**Semantic code intelligence for humans and LLMs.**

Local-first · Lightweight · Python first · SQLite backed

---

## Quickstart

```bash
# Install
pip install -e ".[dev]"

# Index a repository
codegraph index ./path/to/repo

# Find a symbol
codegraph find UserService

# See its definition
codegraph definition UserService

# Who calls it?
codegraph callers UserService.create_user

# What does it call?
codegraph callees UserService.create_user

# Trace dependencies 5 hops deep
codegraph trace UserService.create_user --depth 5

# Blast radius — what breaks if this changes?
codegraph blast-radius UserService.create_user

# See exact source
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
