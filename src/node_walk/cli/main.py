"""
CodeGraph CLI — Typer-based command-line interface.

All commands discover the graph database from the nearest .node_walk/
directory (walking up from cwd). The `index` command creates it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text
from rich.syntax import Syntax

from node_walk.indexer import Indexer
from node_walk.ir.enums import RelationshipType, SymbolKind
from node_walk.query.engine import QueryEngine, SymbolMatch, WalkResult, TraceResult
from node_walk.query.tree_formatter import format_ascii_tree, format_dot, format_mermaid
from node_walk.storage.sqlite_store import SQLiteGraphStore

# ---------------------------------------------------------------------------
# App and console setup
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="node-walk",
    help="Semantic code intelligence — navigate your codebase like a graph.",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DB_FILENAME = "graph.db"
_CG_DIR = ".node_walk"


def _find_db(start: Path | None = None) -> Path | None:
    """Walk up from *start* (or cwd) looking for a .node_walk/graph.db file."""
    current = (start or Path.cwd()).resolve()
    while True:
        candidate = current / _CG_DIR / _DB_FILENAME
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _get_store(repo_path: Path | None = None) -> SQLiteGraphStore:
    """Open the graph store, or exit with a helpful error."""
    db = _find_db(repo_path)
    if db is None:
        err_console.print(
            "[red]No .node_walk/graph.db found.[/red] "
            "Run [bold]node-walk index <path>[/bold] first."
        )
        raise typer.Exit(1)
    return SQLiteGraphStore(db)


def _get_engine(repo_path: Path | None = None) -> QueryEngine:
    return QueryEngine(_get_store(repo_path))


def _print_symbols_table(
    matches: list[SymbolMatch],
    title: str = "Results",
    store: "SQLiteGraphStore | None" = None,
) -> None:
    if not matches:
        console.print("[dim]No symbols found.[/dim]")
        return
    table = Table(title=title, box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Kind", style="yellow", width=12)
    table.add_column("Name", style="bold white")
    table.add_column("File", style="dim")
    table.add_column("Lines", style="dim", width=10)
    has_fuzzy = any(m.score < 0.90 for m in matches)
    if has_fuzzy:
        table.add_column("Score", style="magenta", width=8)

    _store = store or _get_store()
    for m in matches:
        sym = m.symbol
        file_info = _store.get_file(sym.file_id)
        file_label = Path(file_info.path).name if file_info else "?"
        row_args = [
            sym.kind.value,
            sym.qualified_name,
            file_label,
            f"{sym.start_line}-{sym.end_line}",
        ]
        if has_fuzzy:
            score_str = f"{m.score:.2f}"
            if m.score < 0.80:
                score_str = f"[yellow]{score_str} (fuzzy)[/yellow]"
            row_args.append(score_str)
        table.add_row(*row_args)
    console.print(table)


def _print_walk_results(results: list[WalkResult], title: str) -> None:
    if not results:
        console.print("[dim]No results.[/dim]")
        return
    table = Table(title=title, box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Depth", style="dim", width=7)
    table.add_column("Via", style="yellow", width=14)
    table.add_column("Kind", style="yellow", width=12)
    table.add_column("Symbol", style="bold white")
    for r in results:
        table.add_row(
            str(r.depth),
            r.via_relationship.value if r.via_relationship else "—",
            r.symbol.kind.value,
            r.symbol.qualified_name,
        )
    console.print(table)


def _output_graph(result: TraceResult, fmt: str, title: str, output: Path | None = None) -> None:
    fmt_lower = fmt.lower().strip()
    if fmt_lower == "tree":
        tree_text = format_ascii_tree(result)
        if output:
            output.write_text(tree_text, encoding="utf-8")
            console.print(f"[green]Saved tree to[/green] {output}")
        else:
            console.print(Panel(tree_text, title=title, border_style="cyan"))
    elif fmt_lower == "dot":
        dot_text = format_dot(result)
        if output:
            output.write_text(dot_text, encoding="utf-8")
            console.print(f"[green]Saved DOT graph to[/green] {output}")
        else:
            print(dot_text)
    elif fmt_lower == "mermaid":
        mermaid_text = format_mermaid(result)
        if output:
            output.write_text(mermaid_text, encoding="utf-8")
            console.print(f"[green]Saved Mermaid diagram to[/green] {output}")
        else:
            print(mermaid_text)
    else:  # default table
        _print_walk_results(result.nodes, title=title)


def _resolve_symbol(engine: QueryEngine, query: str) -> str | None:
    """
    Resolve a user-provided symbol name/qname to a symbol_id.
    If unambiguous (exact match or clear top candidate), returns it directly.
    If multiple close matches, prints a picker and prompts the user.
    Returns the chosen symbol_id, or None on failure.
    """
    matches = engine.find_symbol(query)
    if not matches:
        console.print(f"[red]No symbol found matching:[/red] {query!r}")
        return None

    if len(matches) == 1:
        return matches[0].symbol.id

    # If the top match is high confidence and clearly separated from the rest, resolve directly
    if matches[0].score >= 0.85 and (matches[0].score - matches[1].score >= 0.10):
        return matches[0].symbol.id

    # Multiple matches — let the user pick
    console.print(f"\n[bold]Multiple matches for[/bold] [yellow]{query!r}[/yellow]:\n")
    for i, m in enumerate(matches[:10], 1):
        sym = m.symbol
        fuzzy_note = " [dim yellow](fuzzy)[/dim yellow]" if m.score < 0.80 else ""
        console.print(f"  [cyan]{i}[/cyan]  {sym.kind.value:12} {sym.qualified_name}{fuzzy_note}")

    choice = typer.prompt("\nPick a number", default="1")
    try:
        idx = int(choice) - 1
        return matches[idx].symbol.id
    except (ValueError, IndexError):
        console.print("[red]Invalid choice.[/red]")
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def index(
    path: Annotated[Path, typer.Argument(help="Repository root to index.")] = Path("."),
    clear: Annotated[bool, typer.Option("--clear/--no-clear", help="Wipe existing graph before indexing.")] = True,
) -> None:
    """Index a repository and build the semantic graph."""
    root = path.resolve()
    db_path = root / _CG_DIR / _DB_FILENAME
    db_path.parent.mkdir(parents=True, exist_ok=True)

    store = SQLiteGraphStore(db_path)

    def progress(file_path: str, current: int, total: int) -> None:
        rel = Path(file_path).relative_to(root) if root in Path(file_path).parents else Path(file_path).name
        console.print(f"  [dim][{current}/{total}][/dim] {rel}", end="\r")

    console.print(f"\n[bold cyan]node-walk[/bold cyan] — indexing [bold]{root}[/bold]\n")

    indexer = Indexer(store, progress_callback=progress)
    stats = indexer.index(root, clear=clear)

    console.print()  # newline after \r progress
    console.print(
        Panel(
            f"[green]OK[/green] Files analyzed:       [bold]{stats.files_analyzed}[/bold] / {stats.files_discovered}\n"
            f"[green]OK[/green] Symbols extracted:     [bold]{stats.symbols_extracted}[/bold]\n"
            f"[green]OK[/green] Relationships:         [bold]{stats.relationships_extracted}[/bold]\n"
            f"[green]OK[/green] Resolved cross-file:  [bold]{stats.relationships_resolved}[/bold]\n"
            + (f"[yellow]WARN[/yellow] Errors: {len(stats.errors)}" if stats.errors else ""),
            title="Index complete",
            border_style="green",
        )
    )

    if stats.errors:
        console.print("\n[yellow]Errors:[/yellow]")
        for e in stats.errors[:10]:
            console.print(f"  [dim]{e}[/dim]")


@app.command()
def find(
    query: Annotated[str, typer.Argument(help="Symbol name or qualified name to search for.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results.")] = 20,
) -> None:
    """Search for symbols by name or qualified name."""
    store = _get_store()
    engine = QueryEngine(store)
    matches = engine.find_symbol(query, limit=limit)
    _print_symbols_table(matches, title=f"Results for {query!r}", store=store)


@app.command()
def definition(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Show the definition (file, lines, signature) of a symbol."""
    engine = _get_engine()
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)

    sym = engine.get_definition(sym_id)
    if not sym:
        console.print("[red]Symbol not found.[/red]")
        raise typer.Exit(1)

    store = _get_store()
    file_info = store.get_file(sym.file_id)
    file_path = file_info.path if file_info else "?"

    console.print(
        Panel(
            f"[bold]{sym.qualified_name}[/bold]\n\n"
            f"Kind:      [yellow]{sym.kind.value}[/yellow]\n"
            f"Language:  {sym.language.value}\n"
            f"File:      [dim]{file_path}[/dim]\n"
            f"Lines:     {sym.start_line}–{sym.end_line}\n"
            + (f"Signature: [dim]{sym.signature}[/dim]\n" if sym.signature else "")
            + (f"Async:     yes\n" if sym.is_async else "")
            + (f"\n[italic]{sym.docstring[:200]}[/italic]" if sym.docstring else ""),
            title="Definition",
            border_style="cyan",
        )
    )


@app.command()
def callers(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Find all symbols that call the given symbol."""
    engine = _get_engine()
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)

    pairs = engine.get_callers(sym_id)
    if not pairs:
        console.print("[dim]No callers found.[/dim]")
        return

    table = Table(title=f"Callers of {symbol!r}", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Kind", style="yellow", width=12)
    table.add_column("Caller", style="bold white")
    table.add_column("Line", style="dim", width=6)
    table.add_column("Confidence", width=12)

    for sym, rel in pairs:
        loc = rel.source_location
        table.add_row(
            sym.kind.value,
            sym.qualified_name,
            str(loc.line) if loc else "—",
            f"[green]{rel.resolution.value}[/green]" if rel.resolution.value == "resolved"
            else f"[yellow]{rel.resolution.value}[/yellow]",
        )
    console.print(table)


@app.command()
def callees(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Find all symbols called by the given symbol."""
    engine = _get_engine()
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)

    pairs = engine.get_callees(sym_id)
    if not pairs:
        console.print("[dim]No callees found.[/dim]")
        return

    table = Table(title=f"Callees of {symbol!r}", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Kind", style="yellow", width=12)
    table.add_column("Callee", style="bold white")
    table.add_column("Line", style="dim", width=6)
    table.add_column("Confidence", width=12)

    for sym, rel in pairs:
        loc = rel.source_location
        table.add_row(
            sym.kind.value,
            sym.qualified_name,
            str(loc.line) if loc else "—",
            f"[green]{rel.resolution.value}[/green]" if rel.resolution.value == "resolved"
            else f"[yellow]{rel.resolution.value}[/yellow]",
        )
    console.print(table)


@app.command()
def refs(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Find all references to the given symbol."""
    store = _get_store()
    engine = QueryEngine(store)
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)
    pairs = engine.get_references(sym_id)
    if not pairs:
        console.print("[dim]No references found.[/dim]")
        return
    matches = [SymbolMatch(symbol=s, score=1.0) for s, _ in pairs]
    _print_symbols_table(matches, title=f"References to {symbol!r}", store=store)


@app.command()
def implementations(
    symbol: Annotated[str, typer.Argument(help="Class/interface name.")],
) -> None:
    """Find implementations or subclasses of the given class/interface."""
    store = _get_store()
    engine = QueryEngine(store)
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)
    syms = engine.get_implementations(sym_id)
    if not syms:
        console.print("[dim]No implementations found.[/dim]")
        return
    matches = [SymbolMatch(symbol=s) for s in syms]
    _print_symbols_table(matches, title=f"Implementations of {symbol!r}", store=store)


@app.command()
def imports(
    symbol: Annotated[str, typer.Argument(help="Symbol or file name.")],
) -> None:
    """Show what a symbol/module imports."""
    engine = _get_engine()
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)
    rels = engine.get_imports(sym_id)
    if not rels:
        console.print("[dim]No imports found.[/dim]")
        return
    table = Table(title=f"Imports of {symbol!r}", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Target", style="bold white")
    table.add_column("Status", width=12)
    for rel in rels:
        target = rel.metadata.get("target_name", rel.target_id or "?")
        status = (
            f"[green]{rel.resolution.value}[/green]"
            if rel.resolution.value == "resolved"
            else f"[yellow]{rel.resolution.value}[/yellow]"
        )
        table.add_row(target, status)
    console.print(table)


@app.command()
def trace(
    symbol: Annotated[str, typer.Argument(help="Starting symbol name.")],
    depth: Annotated[int, typer.Option("--depth", "-d", help="Max traversal depth.")] = 5,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Output format: table, tree, dot, mermaid.")] = "table",
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Save output to file.")] = None,
) -> None:
    """Trace outgoing CALLS and IMPORTS from a symbol (shows what it depends on)."""
    engine = _get_engine()
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)

    result = engine.trace_graph(sym_id, depth=depth)
    if not result or not result.nodes:
        console.print("[dim]No outgoing relationships found.[/dim]")
        return

    _output_graph(result, fmt, title=f"Trace from {symbol!r} (depth={depth})", output=output)


@app.command(name="blast-radius")
def blast_radius(
    symbol: Annotated[str, typer.Argument(help="Symbol name to assess impact for.")],
    depth: Annotated[int, typer.Option("--depth", "-d", help="Max traversal depth.")] = 3,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Output format: table, tree, dot, mermaid.")] = "table",
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Save output to file.")] = None,
) -> None:
    """Find everything that could be affected if this symbol changes."""
    engine = _get_engine()
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)

    result = engine.blast_radius_graph(sym_id, depth=depth)
    if not result or not result.nodes:
        console.print("[dim]No dependent relationships found.[/dim]")
        return

    _output_graph(result, fmt, title=f"Blast radius of {symbol!r} (depth={depth})", output=output)


@app.command()
def graph(
    symbol: Annotated[str, typer.Argument(help="Root symbol name or qualified name.")],
    depth: Annotated[int, typer.Option("--depth", "-d", help="Max traversal depth.")] = 3,
    direction: Annotated[str, typer.Option("--direction", help="Traversal direction: out, in, both.")] = "out",
    fmt: Annotated[str, typer.Option("--format", "-f", help="Output format: tree, dot, mermaid, table.")] = "tree",
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Save output to file.")] = None,
) -> None:
    """Visualize dependencies around a symbol as an ASCII tree, DOT graph, or Mermaid diagram."""
    engine = _get_engine()
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)

    dir_val: Any = direction.lower()
    if dir_val not in ("out", "in", "both"):
        dir_val = "out"

    result = engine.walk_graph(sym_id, direction=dir_val, depth=depth)
    if not result or not result.nodes:
        console.print("[dim]No graph connections found for this symbol.[/dim]")
        return

    _output_graph(result, fmt, title=f"Graph: {symbol!r} ({direction}, depth={depth})", output=output)


@app.command()
def source(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Display the exact source lines for a symbol."""
    engine = _get_engine()
    sym_id = _resolve_symbol(engine, symbol)
    if not sym_id:
        raise typer.Exit(1)
    src = engine.get_source(sym_id)
    if not src:
        console.print("[red]Could not retrieve source.[/red]")
        raise typer.Exit(1)

    code = "\n".join(src.lines)
    syntax = Syntax(
        code,
        "python",
        line_numbers=True,
        start_line=src.start_line,
        theme="monokai",
    )
    console.print(
        Panel(
            syntax,
            title=f"{Path(src.file_path).name}  :{src.start_line}-{src.end_line}",
            border_style="cyan",
        )
    )


@app.command()
def stats() -> None:
    """Show graph statistics: file count, symbol counts by kind, relationship counts."""
    engine = _get_engine()
    data = engine.stats()

    table = Table(title="Graph statistics", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Metric", style="bold white")
    table.add_column("Count", style="cyan", justify="right")

    priority = ["files", "symbols", "relationships", "unresolved_relationships"]
    for key in priority:
        if key in data:
            label = key.replace("_", " ").title()
            val = data[key]
            style = "red" if "unresolved" in key and val > 0 else "cyan"
            table.add_row(label, f"[{style}]{val}[/{style}]")

    # Symbol kinds
    kind_keys = sorted(k for k in data if k.startswith("symbols_") and k != "symbols")
    if kind_keys:
        table.add_section()
        for key in kind_keys:
            table.add_row(f"  {key.replace('symbols_', '').capitalize()}", str(data[key]))

    # Relationship types
    rel_keys = sorted(k for k in data if k.startswith("rel_"))
    if rel_keys:
        table.add_section()
        for key in rel_keys:
            table.add_row(f"  {key.replace('rel_', '').capitalize()}", str(data[key]))

    console.print(table)


@app.command(name="export")
def export_graph(
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Output file (default: stdout).")] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Output format: json")] = "json",
) -> None:
    """Export the entire graph as JSON."""
    store = _get_store()
    files = [f.model_dump() for f in store.get_all_files()]
    symbols = [s.model_dump() for s in store.get_all_symbols()]

    # Get all relationships
    all_rels = []
    for sym in store.get_all_symbols():
        rels = store.get_relationships_from(sym.id)
        for r in rels:
            all_rels.append(r.model_dump())

    # Deduplicate by id
    seen = set()
    unique_rels = []
    for r in all_rels:
        if r["id"] not in seen:
            seen.add(r["id"])
            unique_rels.append(r)

    data = {"files": files, "symbols": symbols, "relationships": unique_rels}
    text = json.dumps(data, indent=2, default=str)

    if output:
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]Exported to[/green] {output}")
    else:
        print(text)


@app.command(name="help")
def show_help() -> None:
    """Show this help message and exit."""
    console.print(
        Panel(
            "[bold white]node-walk[/bold white] - Local Semantic Code Intelligence for Python\n"
            "[dim]Lightweight, local-first, SQLite-backed indexer and query engine.[/dim]",
            border_style="cyan",
            expand=False,
        )
    )

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
    table.add_column("Command", style="bold yellow", width=22)
    table.add_column("Description", style="white")

    table.add_section()
    table.add_row("[bold white]Project Setup[/bold white]", "")
    table.add_row("index <path>", "Analyze and index a Python codebase.")

    table.add_section()
    table.add_row("[bold white]Code Navigation[/bold white]", "")
    table.add_row("find <query>", "Search symbols by name, dotted path, or fuzzy typo.")
    table.add_row("definition <name>", "Show definition metadata for a symbol.")
    table.add_row("source <name>", "Display the exact source code block of a symbol.")

    table.add_section()
    table.add_row("[bold white]Graph Relationships[/bold white]", "")
    table.add_row("callers <name>", "Find all functions/methods calling this symbol.")
    table.add_row("callees <name>", "Find all functions/methods called by this symbol.")
    table.add_row("refs <name>", "Find all references/usages of this symbol.")
    table.add_row("implementations <name>", "Find implementations or subclasses of a class/ABC.")
    table.add_row("imports <name>", "Find all imports of a module/symbol.")

    table.add_section()
    table.add_row("[bold white]Advanced Traversals & Visualizations[/bold white]", "")
    table.add_row("trace <name>", "Trace outgoing dependencies (tree, table, dot, mermaid).")
    table.add_row("blast-radius <name>", "Show transitive impact paths (tree, table, dot, mermaid).")
    table.add_row("graph <name>", "Render ASCII tree, Graphviz DOT, or Mermaid diagrams.")

    table.add_section()
    table.add_row("[bold white]Utilities[/bold white]", "")
    table.add_row("stats", "Show database statistics (file, symbol, edge counts).")
    table.add_row("export", "Export the entire semantic graph as JSON.")

    console.print(table)
    console.print("\n[dim]To see options for any command, run: [bold]node-walk <command> --help[/bold][/dim]")



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    app()

