# CLI JSON Output Mode — Feature Plan

**Make the CLI machine-readable for AI agents without removing the Rich human experience.**

Rich stays for humans. `--json` gives agents clean, structured, parseable output.

---

## 1. Goal

AI agents that invoke `node-walk` commands get garbled ANSI escape codes, box-drawing
characters, and interactive prompts that break automation. We need every CLI command to
support a `--json` flag that emits a **standardized JSON envelope** to stdout — zero
ANSI, zero Rich markup, zero interactive prompts — while preserving the current Rich
output as the default for human users.

### Non-Goals

- Removing Rich (it stays as the default for TTY users).
- Changing the query engine or storage layer.
- Adding new commands (this is purely an output layer change).

---

## 2. Design Decisions

### 2.1 Global `--json` flag

A single global `--json` / `--output-format json` option on the Typer app, inherited by
all commands. No per-command flags needed.

```
node-walk --json find QueryEngine
node-walk --json callers get_callers
node-walk --json stats
```

### 2.2 Standard JSON Envelope

**Every** command response follows the same top-level envelope:

```json
{
  "ok": true,
  "command": "find",
  "data": { ... },
  "error": null
}
```

On failure:

```json
{
  "ok": false,
  "command": "find",
  "data": null,
  "error": {
    "code": "SYMBOL_NOT_FOUND",
    "message": "No symbol found matching: 'FooBar'"
  }
}
```

This gives agents a predictable parse target: check `ok`, read `data` or `error`.

### 2.3 No interactive prompts in JSON mode

The `_resolve_symbol` picker (multiple-match disambiguation) must **auto-select the
top match** in JSON mode instead of prompting. The response includes all candidates so the
agent can re-query with a more specific name if needed.

### 2.4 Stderr stays human-readable

Even in `--json` mode, stderr can carry human messages (e.g., progress during `index`).
Only stdout is machine-parsed. Agents should read stdout only.

---

## 3. Standard JSON Response Schemas

### 3.1 Envelope (all commands)

```typescript
interface CLIResponse<T> {
  ok: boolean;          // true if command succeeded
  command: string;      // command name: "find", "callers", "index", etc.
  data: T | null;       // command-specific payload (null on error)
  error: {              // null on success
    code: string;       //   machine-readable error code
    message: string;    //   human-readable description
  } | null;
}
```

### 3.2 Symbol Object (shared across commands)

Whenever a symbol appears in JSON output, it uses this shape:

```typescript
interface SymbolJSON {
  id: string;                 // UUID
  name: string;               // simple name: "get_callers"
  qualified_name: string;     // "node_walk.query.engine.QueryEngine.get_callers"
  kind: string;               // "FUNCTION" | "CLASS" | "METHOD" | ...
  language: string;           // "python"
  file: string;               // relative file path
  start_line: number;         // 1-indexed
  end_line: number;           // 1-indexed, inclusive
  signature: string | null;   // "(self, symbol_id: str) -> ..."
  is_async: boolean;
  docstring: string | null;   // first 500 chars, null if empty
}
```

Key decision: `file` is the **relative path** (relative to the repo root), not the
absolute path stored in the DB. This makes output portable.

### 3.3 Relationship Object (shared)

```typescript
interface RelationshipJSON {
  type: string;               // "CALLS" | "IMPORTS" | "EXTENDS" | ...
  resolution: string;         // "resolved" | "probable" | "unresolved"
  source_location: {
    line: number;
    col: number;
  } | null;
}
```

---

## 4. Per-Command JSON Schemas

### 4.1 `find`

```json
{
  "ok": true,
  "command": "find",
  "data": {
    "query": "QueryEngine",
    "count": 3,
    "results": [
      {
        "symbol": { /* SymbolJSON */ },
        "score": 1.0
      }
    ]
  }
}
```

### 4.2 `definition`

```json
{
  "ok": true,
  "command": "definition",
  "data": {
    "symbol": { /* SymbolJSON */ },
    "parent_id": "uuid-or-null"
  }
}
```

### 4.3 `callers` / `callees`

```json
{
  "ok": true,
  "command": "callers",
  "data": {
    "target": "get_callers",
    "count": 5,
    "results": [
      {
        "symbol": { /* SymbolJSON */ },
        "relationship": { /* RelationshipJSON */ }
      }
    ]
  }
}
```

### 4.4 `refs`

```json
{
  "ok": true,
  "command": "refs",
  "data": {
    "target": "QueryEngine",
    "count": 12,
    "results": [
      {
        "symbol": { /* SymbolJSON */ },
        "score": 1.0
      }
    ]
  }
}
```

### 4.5 `implementations`

```json
{
  "ok": true,
  "command": "implementations",
  "data": {
    "target": "GraphStore",
    "count": 1,
    "results": [
      {
        "symbol": { /* SymbolJSON */ },
        "score": 1.0
      }
    ]
  }
}
```

### 4.6 `imports`

```json
{
  "ok": true,
  "command": "imports",
  "data": {
    "symbol": "engine.py",
    "count": 4,
    "results": [
      {
        "target_name": "SQLiteGraphStore",
        "relationship": { /* RelationshipJSON */ }
      }
    ]
  }
}
```

### 4.7 `source`

```json
{
  "ok": true,
  "command": "source",
  "data": {
    "symbol": { /* SymbolJSON */ },
    "file": "src/node_walk/query/engine.py",
    "start_line": 76,
    "end_line": 82,
    "source": "class QueryEngine:\n    ...\n"
  }
}
```

### 4.8 `trace` / `blast-radius` / `graph`

```json
{
  "ok": true,
  "command": "trace",
  "data": {
    "root": { /* SymbolJSON */ },
    "direction": "out",
    "depth": 5,
    "nodes": [
      {
        "symbol": { /* SymbolJSON */ },
        "depth": 1,
        "via": "CALLS"
      }
    ],
    "edges": [
      {
        "source": "uuid-source",
        "target": "uuid-target",
        "type": "CALLS",
        "depth": 1
      }
    ]
  }
}
```

### 4.9 `stats`

```json
{
  "ok": true,
  "command": "stats",
  "data": {
    "files": 42,
    "symbols": 312,
    "relationships": 874,
    "unresolved_relationships": 23,
    "symbols_by_kind": {
      "CLASS": 28,
      "FUNCTION": 105,
      "METHOD": 132,
      "VARIABLE": 47
    },
    "relationships_by_type": {
      "CALLS": 312,
      "IMPORTS": 198,
      "EXTENDS": 14,
      "CONTAINS": 280,
      "REFERENCES": 70
    }
  }
}
```

### 4.10 `index`

```json
{
  "ok": true,
  "command": "index",
  "data": {
    "mode": "incremental",
    "forward": {
      "files_discovered": 42,
      "files_changed": 3,
      "files_added": 1,
      "files_removed": 0,
      "files_unchanged": 38,
      "files_analyzed": 4,
      "symbols_extracted": 28,
      "facts_extracted": 67
    },
    "backward": {
      "iterations_run": 3,
      "converged": true,
      "total_resolved": 52,
      "total_probable": 8,
      "total_ignored": 3,
      "total_pending": 0,
      "iteration_stats": [
        {
          "iteration": 1,
          "facts_resolved": 40,
          "facts_probable": 6,
          "facts_ignored": 2
        }
      ]
    },
    "materialization": {
      "relationships_created": 60,
      "relationships_deduplicated": 5
    },
    "errors": []
  }
}
```

### 4.11 `export`

`export` already outputs raw JSON. In `--json` mode it wraps in the envelope:

```json
{
  "ok": true,
  "command": "export",
  "data": {
    "files": [ ... ],
    "symbols": [ ... ],
    "relationships": [ ... ]
  }
}
```

---

## 5. Error Codes

Standardized machine-readable error codes for the `error.code` field:

| Code | Meaning |
|---|---|
| `NO_INDEX` | No `.node_walk/graph.db` found |
| `SYMBOL_NOT_FOUND` | No symbol matches the query |
| `SYMBOL_AMBIGUOUS` | Multiple matches, could not auto-resolve (includes candidates) |
| `SOURCE_UNAVAILABLE` | Symbol exists but source file is missing/unreadable |
| `INVALID_ARGUMENT` | Bad flag value or missing required argument |
| `INTERNAL_ERROR` | Unexpected exception |

When `code` is `SYMBOL_AMBIGUOUS`, the error includes candidates:

```json
{
  "ok": false,
  "command": "callers",
  "data": null,
  "error": {
    "code": "SYMBOL_AMBIGUOUS",
    "message": "Multiple matches for 'process'. Use a more specific qualified name.",
    "candidates": [
      { "symbol": { /* SymbolJSON */ }, "score": 0.95 },
      { "symbol": { /* SymbolJSON */ }, "score": 0.90 }
    ]
  }
}
```

---

## 6. Implementation Plan

### Phase 1: Infrastructure (single PR)

#### 6.1 Add output module: `src/node_walk/cli/output.py`

New module that centralizes all output logic:

```python
"""
CLI output — handles both Rich (human) and JSON (machine) output modes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console


@dataclass
class OutputContext:
    """Holds the current output mode and console references."""
    json_mode: bool = False
    console: Console | None = None
    err_console: Console | None = None
    repo_root: Path | None = None  # for relativizing file paths


# Module-level singleton, set by the Typer callback
_ctx = OutputContext()


def init(json_mode: bool, repo_root: Path | None = None) -> None:
    """Initialize the output context. Called once from the Typer callback."""
    global _ctx
    _ctx = OutputContext(
        json_mode=json_mode,
        console=Console(force_terminal=not json_mode) if not json_mode else None,
        err_console=Console(stderr=True),
        repo_root=repo_root,
    )


def is_json() -> bool:
    return _ctx.json_mode


def get_console() -> Console:
    if _ctx.console is None:
        return Console()
    return _ctx.console


def get_err_console() -> Console:
    if _ctx.err_console is None:
        return Console(stderr=True)
    return _ctx.err_console


def emit(command: str, data: Any) -> None:
    """Emit a successful JSON response to stdout."""
    response = {
        "ok": True,
        "command": command,
        "data": data,
        "error": None,
    }
    print(json.dumps(response, indent=2, default=str))


def emit_error(command: str, code: str, message: str, **extra: Any) -> None:
    """Emit a failed JSON response to stdout."""
    error = {"code": code, "message": message, **extra}
    response = {
        "ok": False,
        "command": command,
        "data": None,
        "error": error,
    }
    print(json.dumps(response, indent=2, default=str))
    sys.exit(1)


def symbol_to_dict(sym, file_path: str | None = None) -> dict:
    """Convert a Symbol to the standard SymbolJSON dict."""
    rel_path = file_path or ""
    if _ctx.repo_root and rel_path:
        try:
            rel_path = str(Path(rel_path).relative_to(_ctx.repo_root))
        except ValueError:
            pass
    return {
        "id": sym.id,
        "name": sym.name,
        "qualified_name": sym.qualified_name,
        "kind": sym.kind.value,
        "language": sym.language.value,
        "file": rel_path,
        "start_line": sym.start_line,
        "end_line": sym.end_line,
        "signature": sym.signature or None,
        "is_async": sym.is_async,
        "docstring": (sym.docstring[:500] if sym.docstring else None),
    }
```

#### 6.2 Add global `--json` callback to Typer app

In `main.py`, register a global callback:

```python
@app.callback()
def main_callback(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON to stdout."),
    ] = False,
) -> None:
    """Global options for node-walk."""
    output.init(json_mode=json_output)
```

#### 6.3 Update `_resolve_symbol` for non-interactive JSON mode

```python
def _resolve_symbol(engine, query):
    matches = engine.find_symbol(query)
    if not matches:
        if output.is_json():
            output.emit_error(cmd, "SYMBOL_NOT_FOUND", f"No symbol found matching: {query!r}")
        # ... existing Rich error ...
        return None

    if len(matches) == 1:
        return matches[0].symbol.id

    # Auto-confidence check
    if matches[0].score >= 0.85 and (matches[0].score - matches[1].score >= 0.10):
        return matches[0].symbol.id

    # JSON mode: auto-pick top match, never prompt
    if output.is_json():
        return matches[0].symbol.id

    # Rich mode: interactive picker (unchanged)
    ...
```

### Phase 2: Convert each command (single PR)

Each command gets a small addition at the top of the function body:

```python
@app.command()
def find(query: ..., limit: ...):
    if output.is_json():
        store = _get_store()
        engine = QueryEngine(store)
        matches = engine.find_symbol(query, limit=limit)
        results = []
        for m in matches:
            file_info = store.get_file(m.symbol.file_id)
            file_path = file_info.path if file_info else ""
            results.append({
                "symbol": output.symbol_to_dict(m.symbol, file_path),
                "score": m.score,
            })
        output.emit("find", {
            "query": query,
            "count": len(results),
            "results": results,
        })
        return

    # ... existing Rich code unchanged ...
```

#### Command conversion order (by priority for agent usage):

| Priority | Command | Complexity | Notes |
|---|---|---|---|
| 1 | `find` | Low | Most used by agents for discovery |
| 2 | `definition` | Low | Core lookup |
| 3 | `callers` | Low | Call graph navigation |
| 4 | `callees` | Low | Call graph navigation |
| 5 | `refs` | Low | Reference lookup |
| 6 | `source` | Low | Source code retrieval — highest agent value |
| 7 | `stats` | Low | Already dict-shaped in the engine |
| 8 | `implementations` | Low | Inheritance queries |
| 9 | `imports` | Low | Dependency queries |
| 10 | `trace` | Medium | Graph result serialization |
| 11 | `blast-radius` | Medium | Same as trace, reverse |
| 12 | `graph` | Medium | Same as trace, bidirectional |
| 13 | `index` | Medium | Stats serialization |
| 14 | `export` | Low | Already JSON, just wrap in envelope |

### Phase 3: Error handling hardening

#### Current state (the problem)

There are **14 error exit points** across `main.py`, all following the same ad-hoc pattern:

```python
console.print("[red]Some error message.[/red]")
raise typer.Exit(1)
```

Problems with this:
- No structured error codes — agents can't distinguish "no index" from "symbol not found"
- Error messages contain Rich markup tags that leak into non-TTY pipes
- Unhandled exceptions (e.g., corrupt DB, disk full) produce raw Python tracebacks
- No consistency — each command has its own phrasing

#### 3.1 Top-level exception guard

Add a decorator/wrapper that catches all unhandled exceptions at the command level:

```python
import functools
import traceback

def cli_error_guard(command_name: str):
    """Decorator that catches unhandled exceptions and routes them to the output layer."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except typer.Exit:
                raise  # Let intentional exits pass through
            except Exception as exc:
                if output.is_json():
                    output.emit_error(
                        command_name,
                        "INTERNAL_ERROR",
                        f"Unexpected error: {exc}",
                        traceback=traceback.format_exc(),
                    )
                else:
                    err_console.print(f"[red]Error:[/red] {exc}")
                    err_console.print("[dim]Run with --verbose for full traceback.[/dim]")
                raise typer.Exit(2)
        return wrapper
    return decorator

# Usage:
@app.command()
@cli_error_guard("find")
def find(query: ..., limit: ...):
    ...
```

This ensures **zero raw tracebacks** reach agents — every failure is a valid JSON envelope.

#### 3.2 Per-helper error routing

The shared helpers (`_get_store`, `_resolve_symbol`) need to emit structured errors
in JSON mode instead of printing Rich markup:

| Helper | Current behavior | JSON mode behavior |
|---|---|---|
| `_get_store()` | `console.print("[red]No .node_walk/graph.db found.[/red]")` + `Exit(1)` | `emit_error(cmd, "NO_INDEX", "No .node_walk/graph.db found. Run 'node-walk index <path>' first.")` |
| `_resolve_symbol()` — no match | `console.print("[red]No symbol found matching: ...")` | `emit_error(cmd, "SYMBOL_NOT_FOUND", "No symbol found matching: '{query}'")` |
| `_resolve_symbol()` — ambiguous | Interactive `typer.prompt()` picker | Auto-pick top match; include `candidates` array in response metadata |
| `_resolve_symbol()` — bad choice | `console.print("[red]Invalid choice.[/red]")` | N/A (no prompts in JSON mode) |

To make this work cleanly, `_get_store` and `_resolve_symbol` need to accept a
`command_name` parameter so they know which command to include in the error envelope:

```python
def _get_store(repo_path: Path | None = None, cmd: str = "unknown") -> SQLiteGraphStore:
    db = _find_db(repo_path)
    if db is None:
        if output.is_json():
            output.emit_error(cmd, "NO_INDEX",
                "No .node_walk/graph.db found. Run 'node-walk index <path>' first.")
        err_console.print(
            "[red]No .node_walk/graph.db found.[/red] "
            "Run [bold]node-walk index <path>[/bold] first."
        )
        raise typer.Exit(1)
    return SQLiteGraphStore(db)
```

#### 3.3 Error path audit

Full mapping of every error exit point in `main.py` to its structured error code:

| Line | Function | Error | JSON Error Code | JSON Message |
|---|---|---|---|---|
| 67–71 | `_get_store` | No graph.db found | `NO_INDEX` | No .node_walk/graph.db found |
| 171 | `_resolve_symbol` | No symbol match | `SYMBOL_NOT_FOUND` | No symbol found matching: '{query}' |
| 193 | `_resolve_symbol` | Invalid picker choice | N/A | Not reachable in JSON mode |
| 307 | `definition` | `_resolve_symbol` failed | `SYMBOL_NOT_FOUND` | (delegated to `_resolve_symbol`) |
| 312 | `definition` | Symbol ID not in DB | `SYMBOL_NOT_FOUND` | Symbol not found in graph |
| 342 | `callers` | `_resolve_symbol` failed | `SYMBOL_NOT_FOUND` | (delegated) |
| 375 | `callees` | `_resolve_symbol` failed | `SYMBOL_NOT_FOUND` | (delegated) |
| 409 | `refs` | `_resolve_symbol` failed | `SYMBOL_NOT_FOUND` | (delegated) |
| 427 | `implementations` | `_resolve_symbol` failed | `SYMBOL_NOT_FOUND` | (delegated) |
| 444 | `imports` | `_resolve_symbol` failed | `SYMBOL_NOT_FOUND` | (delegated) |
| 474 | `trace` | `_resolve_symbol` failed | `SYMBOL_NOT_FOUND` | (delegated) |
| 495 | `blast_radius` | `_resolve_symbol` failed | `SYMBOL_NOT_FOUND` | (delegated) |
| 517 | `graph` | `_resolve_symbol` failed | `SYMBOL_NOT_FOUND` | (delegated) |
| 539–543 | `source` | `_resolve_symbol` or source unreadable | `SYMBOL_NOT_FOUND` / `SOURCE_UNAVAILABLE` | Could not retrieve source |
| 699 | `serve` | No graph.db found | `NO_INDEX` | No .node_walk/graph.db found |
| *any* | *any* | Unhandled exception | `INTERNAL_ERROR` | Unexpected error: {exception} |

#### 3.4 Improved Rich-mode errors (side benefit)

While routing errors through the output layer, also improve the human-facing messages.
Instead of bare `[red]No results.[/red]`, use consistent formatting:

```python
# Before (inconsistent):
console.print("[red]Symbol not found.[/red]")
console.print("[red]No symbol found matching:[/red] ...")
console.print("[red]Could not retrieve source.[/red]")

# After (consistent, actionable):
err_console.print(Panel(
    "[red bold]Symbol not found[/red bold]\n\n"
    f"No symbol matching [yellow]{query!r}[/yellow] exists in the graph.\n"
    "[dim]Try: node-walk find <partial-name>[/dim]",
    title="Error",
    border_style="red",
))
```

This isn't strictly required for the JSON work, but since we're touching every error
path anyway, it's cheap to do and improves the human experience too.

---

## 7. Files Changed

| File | Change |
|---|---|
| `src/node_walk/cli/output.py` | **NEW** — Output abstraction (JSON + Rich) |
| `src/node_walk/cli/main.py` | Add `--json` callback, branch each command on `output.is_json()` |
| `tests/test_cli_json.py` | **NEW** — Test JSON output for every command |
| `pyproject.toml` | No change (no new deps; `json` is stdlib) |
| `README.md` | Document `--json` flag and response format |

---

## 8. Testing Strategy

### 8.1 Unit tests (`tests/test_cli_json.py`)

Use Typer's `CliRunner` to invoke each command with `--json` and assert:

```python
from typer.testing import CliRunner
from node_walk.cli.main import app

runner = CliRunner()

def test_find_json():
    result = runner.invoke(app, ["--json", "find", "QueryEngine"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["command"] == "find"
    assert isinstance(data["data"]["results"], list)
    assert "score" in data["data"]["results"][0]

def test_find_json_not_found():
    result = runner.invoke(app, ["--json", "find", "NonexistentXYZ123"])
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["data"]["count"] == 0

def test_callers_json_no_index():
    # No .node_walk/ dir → should get NO_INDEX error
    result = runner.invoke(app, ["--json", "callers", "foo"])
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["error"]["code"] == "NO_INDEX"
```

### 8.2 Validation rules for all JSON tests

Every test verifies:
1. Output is valid JSON (no ANSI codes mixed in)
2. Envelope has all 4 keys: `ok`, `command`, `data`, `error`
3. `ok=true` → `data` is not null, `error` is null
4. `ok=false` → `data` is null, `error` has `code` + `message`
5. Exit code is 0 on success, non-zero on error

### 8.3 Integration smoke test

Script that indexes a small fixture repo and runs every command with `--json`,
piping through `python -m json.tool` to validate well-formedness.

---

## 9. Migration & Backward Compatibility

- **No breaking changes.** Default output is unchanged (Rich).
- `--json` is purely additive.
- `export` command already emits JSON to stdout; in `--json` mode it wraps in the
  envelope. Agents that currently parse raw `export` output should add
  `--json` and read `data` instead.
- The `rich_markup_mode="rich"` on the Typer app stays — it controls help text
  rendering, not command output.

---

## 10. Future Extensions (Not in This PR)

- **`--json` on `serve`**: Emit `{"ok": true, "url": "http://localhost:7777"}` and
  stay running (useful for agents that want to launch the server).
- **JSONL streaming for `index`**: Emit progress events as newline-delimited JSON for
  real-time agent consumption during long indexing runs.
- **`--quiet`**: Suppress all non-essential output (complementary to `--json`).
- **YAML output**: If anyone asks (unlikely — JSON is the universal agent format).
