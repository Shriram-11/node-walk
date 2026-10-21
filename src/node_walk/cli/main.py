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
from rich import box
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from node_walk.cli import output
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


@app.callback()
def main_callback(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON to stdout."),
    ] = False,
) -> None:
    """Global options for node-walk."""
    output.init(json_mode=json_output)


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


def _get_store(repo_path: Path | None = None, command_name: str = "node-walk") -> SQLiteGraphStore:
    """Open the graph store, or exit with a helpful error."""
    db = _find_db(repo_path)
    if db is None:
        if output.is_json():
            output.emit_error(
                command_name,
                "NO_INDEX",
                "No .node_walk/graph.db found. Run 'node-walk index <path>' first.",
            )
        output.print_rich_error(
            "No .node_walk/graph.db found.",
            "Run 'node-walk index <path>' first to build the code graph.",
            hint="Example: node-walk index .",
        )
        raise typer.Exit(1)
    # Set repo root on output context if not set yet
    if output._ctx.repo_root is None:
        output._ctx.repo_root = db.parent.parent
    return SQLiteGraphStore(db)


def _get_engine(repo_path: Path | None = None, command_name: str = "node-walk") -> QueryEngine:
    return QueryEngine(_get_store(repo_path, command_name=command_name))


def _print_symbols_table(
    matches: list[SymbolMatch],
    title: str = "Results",
    store: "SQLiteGraphStore | None" = None,
) -> None:
    console = output.get_console()
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
    console = output.get_console()
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


def _output_graph(result: TraceResult, fmt: str, title: str, output_path: Path | None = None) -> None:
    console = output.get_console()
    fmt_lower = fmt.lower().strip()
    if fmt_lower == "tree":
        tree_text = format_ascii_tree(result)
        if output_path:
            output_path.write_text(tree_text, encoding="utf-8")
            console.print(f"[green]Saved tree to[/green] {output_path}")
        else:
            console.print(Panel(tree_text, title=title, border_style="cyan"))
    elif fmt_lower == "dot":
        dot_text = format_dot(result)
        if output_path:
            output_path.write_text(dot_text, encoding="utf-8")
            console.print(f"[green]Saved DOT graph to[/green] {output_path}")
        else:
            print(dot_text)
    elif fmt_lower == "mermaid":
        mermaid_text = format_mermaid(result)
        if output_path:
            output_path.write_text(mermaid_text, encoding="utf-8")
            console.print(f"[green]Saved Mermaid diagram to[/green] {output_path}")
        else:
            print(mermaid_text)
    else:  # default table
        _print_walk_results(result.nodes, title=title)


def _resolve_symbol(
    engine: QueryEngine,
    query: str,
    command_name: str = "node-walk",
) -> str | None:
    """
    Resolve a user-provided symbol name/qname to a symbol_id.
    In JSON mode, never prompts — auto-picks top match or emits error.
    """
    console = output.get_console()
    matches = engine.find_symbol(query)
    if not matches:
        if output.is_json():
            output.emit_error(
                command_name,
                "SYMBOL_NOT_FOUND",
                f"No symbol found matching: {query!r}",
            )
        output.print_rich_error(
            "Symbol not found",
            f"No symbol matching [yellow]{query!r}[/yellow] exists in the graph.",
            hint=f"Try running: node-walk find {query}",
        )
        return None

    if len(matches) == 1:
        return matches[0].symbol.id

    # If top match is high confidence and clearly separated, resolve directly
    if matches[0].score >= 0.85 and (matches[0].score - matches[1].score >= 0.10):
        return matches[0].symbol.id

    # In JSON mode: auto-select top candidate without interactive prompting
    if output.is_json():
        return matches[0].symbol.id

    # Multiple matches — let the human user pick
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
        output.print_rich_error("Invalid choice", "The selected number is outside valid range.")
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
@output.cli_error_guard("index")
def index(
    path: Annotated[Path, typer.Argument(help="Repository root to index.")] = Path("."),
    full: Annotated[bool, typer.Option("--full", help="Force a full clean re-index.")] = False,
    clear: Annotated[Optional[bool], typer.Option("--clear/--no-clear", help="Legacy option: wipe existing graph before indexing.")] = None,
    max_passes: Annotated[int, typer.Option("--max-passes", help="Max resolution iterations.")] = 5,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show per-resolver stats.")] = False,
) -> None:
    """Index a repository and build or update the semantic graph."""
    root = path.resolve()
    db_path = root / _CG_DIR / _DB_FILENAME
    db_exists = db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if clear is not None:
        mode = "full" if clear else "incremental"
    else:
        mode = "full" if full or not db_exists else "incremental"

    store = SQLiteGraphStore(db_path)
    output._ctx.repo_root = root

    console = output.get_console()

    def progress(file_path: str, current: int, total: int) -> None:
        if not output.is_json():
            rel = Path(file_path).relative_to(root) if root in Path(file_path).parents else Path(file_path).name
            console.print(f"  [dim][{current}/{total}][/dim] {rel}", end="\r")

    if not output.is_json():
        console.print(f"\n[bold cyan]node-walk[/bold cyan] — indexing [bold]{root}[/bold] ({mode})\n")

    indexer = Indexer(store, progress_callback=progress)
    stats = indexer.index(root, mode=mode, max_iterations=max_passes)

    if output.is_json():
        iteration_stats = [
            {
                "iteration": s.iteration,
                "facts_resolved": s.facts_resolved,
                "facts_probable": s.facts_probable,
                "facts_ignored": s.facts_ignored,
                "resolver_stats": s.resolver_stats,
            }
            for s in stats.backward.iteration_stats
        ]
        data = {
            "mode": mode,
            "forward": {
                "files_discovered": stats.forward.files_discovered,
                "files_changed": stats.forward.files_changed,
                "files_added": stats.forward.files_added,
                "files_removed": stats.forward.files_removed,
                "files_unchanged": stats.forward.files_unchanged,
                "files_analyzed": stats.forward.files_analyzed,
                "symbols_extracted": stats.forward.symbols_extracted,
                "facts_extracted": stats.forward.facts_extracted,
            },
            "backward": {
                "iterations_run": stats.backward.iterations_run,
                "converged": stats.backward.converged,
                "total_resolved": stats.backward.total_resolved,
                "total_probable": stats.backward.total_probable,
                "total_ignored": stats.backward.total_ignored,
                "total_pending": stats.backward.total_pending,
                "iteration_stats": iteration_stats,
            },
            "materialization": {
                "relationships_created": stats.materialization.relationships_created,
                "relationships_deduplicated": stats.materialization.relationships_deduplicated,
            },
            "errors": stats.errors,
        }
        output.emit("index", data)
        return

    console.print()  # newline after \r progress

    lines = [
        "[bold cyan]Forward pass[/bold cyan]",
        f"  Files discovered:    {stats.forward.files_discovered}",
    ]
    if mode == "incremental":
        lines.extend([
            f"  Files changed:       {stats.forward.files_changed}",
            f"  Files added:         {stats.forward.files_added}",
            f"  Files removed:       {stats.forward.files_removed}",
            f"  Files unchanged:     {stats.forward.files_unchanged}",
        ])
    lines.extend([
        f"  Symbols extracted:   {stats.forward.symbols_extracted}  (from {stats.forward.files_analyzed} files)",
        f"  Facts extracted:     {stats.forward.facts_extracted}",
        "",
        "[bold cyan]Backward pass[/bold cyan]",
    ])
    for stat in stats.backward.iteration_stats:
        status_msg = f"resolved {stat.facts_resolved}, probable {stat.facts_probable}, ignored {stat.facts_ignored}"
        if stat.iteration == stats.backward.iterations_run and stats.backward.converged and (stat.facts_resolved + stat.facts_probable == 0):
            lines.append(f"  Iteration {stat.iteration}:  converged (0 new)")
        else:
            lines.append(f"  Iteration {stat.iteration}:  {status_msg}")
        if verbose:
            for rname, count in stat.resolver_stats.items():
                if count > 0:
                    lines.append(f"    - {rname}: {count}")

    lines.extend([
        f"  Total:        {stats.backward.total_resolved} resolved, {stats.backward.total_probable} probable, {stats.backward.total_ignored} ignored, {stats.backward.total_pending} pending",
        "",
        "[bold cyan]Materialization[/bold cyan]",
        f"  Relationships created:  {stats.materialization.relationships_created}",
        f"  Deduplicated:           {stats.materialization.relationships_deduplicated}",
    ])

    if stats.errors:
        lines.append(f"\n[yellow]WARN Errors: {len(stats.errors)}[/yellow]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[green]Index complete[/green]",
            border_style="green",
        )
    )

    if stats.errors:
        console.print("\n[yellow]Errors:[/yellow]")
        for e in stats.errors[:10]:
            console.print(f"  [dim]{e}[/dim]")


@app.command()
@output.cli_error_guard("find")
def find(
    query: Annotated[str, typer.Argument(help="Symbol name or qualified name to search for.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results.")] = 20,
) -> None:
    """Search for symbols by name or qualified name."""
    store = _get_store(command_name="find")
    engine = QueryEngine(store)
    matches = engine.find_symbol(query, limit=limit)

    if output.is_json():
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

    _print_symbols_table(matches, title=f"Results for {query!r}", store=store)


@app.command()
@output.cli_error_guard("definition")
def definition(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Show the definition (file, lines, signature) of a symbol."""
    engine = _get_engine(command_name="definition")
    sym_id = _resolve_symbol(engine, symbol, command_name="definition")
    if not sym_id:
        raise typer.Exit(1)

    sym = engine.get_definition(sym_id)
    if not sym:
        if output.is_json():
            output.emit_error("definition", "SYMBOL_NOT_FOUND", "Symbol not found in graph.")
        output.print_rich_error("Symbol not found", "Definition could not be retrieved from graph.")
        raise typer.Exit(1)

    store = _get_store(command_name="definition")
    file_info = store.get_file(sym.file_id)
    file_path = file_info.path if file_info else "?"

    if output.is_json():
        output.emit("definition", {
            "symbol": output.symbol_to_dict(sym, file_path),
            "parent_id": sym.parent_id,
        })
        return

    console = output.get_console()
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
@output.cli_error_guard("callers")
def callers(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Find all symbols that call the given symbol."""
    engine = _get_engine(command_name="callers")
    sym_id = _resolve_symbol(engine, symbol, command_name="callers")
    if not sym_id:
        raise typer.Exit(1)

    pairs = engine.get_callers(sym_id)
    store = _get_store(command_name="callers")

    if output.is_json():
        results = []
        for sym, rel in pairs:
            file_info = store.get_file(sym.file_id)
            results.append({
                "symbol": output.symbol_to_dict(sym, file_info.path if file_info else ""),
                "relationship": output.relationship_to_dict(rel),
            })
        output.emit("callers", {
            "target": symbol,
            "count": len(results),
            "results": results,
        })
        return

    console = output.get_console()
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
@output.cli_error_guard("callees")
def callees(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Find all symbols called by the given symbol."""
    engine = _get_engine(command_name="callees")
    sym_id = _resolve_symbol(engine, symbol, command_name="callees")
    if not sym_id:
        raise typer.Exit(1)

    pairs = engine.get_callees(sym_id)
    store = _get_store(command_name="callees")

    if output.is_json():
        results = []
        for sym, rel in pairs:
            file_info = store.get_file(sym.file_id)
            results.append({
                "symbol": output.symbol_to_dict(sym, file_info.path if file_info else ""),
                "relationship": output.relationship_to_dict(rel),
            })
        output.emit("callees", {
            "target": symbol,
            "count": len(results),
            "results": results,
        })
        return

    console = output.get_console()
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
@output.cli_error_guard("refs")
def refs(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Find all references to the given symbol."""
    store = _get_store(command_name="refs")
    engine = QueryEngine(store)
    sym_id = _resolve_symbol(engine, symbol, command_name="refs")
    if not sym_id:
        raise typer.Exit(1)

    pairs = engine.get_references(sym_id)

    if output.is_json():
        results = []
        for s, _ in pairs:
            file_info = store.get_file(s.file_id)
            results.append({
                "symbol": output.symbol_to_dict(s, file_info.path if file_info else ""),
                "score": 1.0,
            })
        output.emit("refs", {
            "target": symbol,
            "count": len(results),
            "results": results,
        })
        return

    console = output.get_console()
    if not pairs:
        console.print("[dim]No references found.[/dim]")
        return
    matches = [SymbolMatch(symbol=s, score=1.0) for s, _ in pairs]
    _print_symbols_table(matches, title=f"References to {symbol!r}", store=store)


@app.command()
@output.cli_error_guard("implementations")
def implementations(
    symbol: Annotated[str, typer.Argument(help="Class/interface name.")],
) -> None:
    """Find implementations or subclasses of the given class/interface."""
    store = _get_store(command_name="implementations")
    engine = QueryEngine(store)
    sym_id = _resolve_symbol(engine, symbol, command_name="implementations")
    if not sym_id:
        raise typer.Exit(1)

    syms = engine.get_implementations(sym_id)

    if output.is_json():
        results = []
        for s in syms:
            file_info = store.get_file(s.file_id)
            results.append({
                "symbol": output.symbol_to_dict(s, file_info.path if file_info else ""),
                "score": 1.0,
            })
        output.emit("implementations", {
            "target": symbol,
            "count": len(results),
            "results": results,
        })
        return

    console = output.get_console()
    if not syms:
        console.print("[dim]No implementations found.[/dim]")
        return
    matches = [SymbolMatch(symbol=s) for s in syms]
    _print_symbols_table(matches, title=f"Implementations of {symbol!r}", store=store)


@app.command()
@output.cli_error_guard("imports")
def imports(
    symbol: Annotated[str, typer.Argument(help="Symbol or file name.")],
) -> None:
    """Show what a symbol/module imports."""
    engine = _get_engine(command_name="imports")
    sym_id = _resolve_symbol(engine, symbol, command_name="imports")
    if not sym_id:
        raise typer.Exit(1)

    rels = engine.get_imports(sym_id)

    if output.is_json():
        results = []
        for rel in rels:
            target = rel.metadata.get("target_name", rel.target_id or "?")
            results.append({
                "target_name": target,
                "relationship": output.relationship_to_dict(rel),
            })
        output.emit("imports", {
            "symbol": symbol,
            "count": len(results),
            "results": results,
        })
        return

    console = output.get_console()
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
@output.cli_error_guard("trace")
def trace(
    symbol: Annotated[str, typer.Argument(help="Starting symbol name.")],
    depth: Annotated[int, typer.Option("--depth", "-d", help="Max traversal depth.")] = 5,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Output format: table, tree, dot, mermaid.")] = "table",
    output_path: Annotated[Optional[Path], typer.Option("--output", "-o", help="Save output to file.")] = None,
) -> None:
    """Trace outgoing CALLS and IMPORTS from a symbol (shows what it depends on)."""
    engine = _get_engine(command_name="trace")
    store = _get_store(command_name="trace")
    sym_id = _resolve_symbol(engine, symbol, command_name="trace")
    if not sym_id:
        raise typer.Exit(1)

    result = engine.trace_graph(sym_id, depth=depth)

    if output.is_json():
        if not result or not result.nodes:
            output.emit("trace", {
                "root": None,
                "direction": "out",
                "depth": depth,
                "nodes": [],
                "edges": [],
            })
            return
        root_file = store.get_file(result.root.file_id)
        nodes = []
        for n in result.nodes:
            f = store.get_file(n.symbol.file_id)
            nodes.append({
                "symbol": output.symbol_to_dict(n.symbol, f.path if f else ""),
                "depth": n.depth,
                "via": n.via_relationship.value if n.via_relationship else None,
            })
        edges = [
            {
                "source": e.source.id,
                "target": e.target.id,
                "type": e.relationship.value,
                "depth": e.depth,
            }
            for e in result.edges
        ]
        output.emit("trace", {
            "root": output.symbol_to_dict(result.root, root_file.path if root_file else ""),
            "direction": "out",
            "depth": depth,
            "nodes": nodes,
            "edges": edges,
        })
        return

    console = output.get_console()
    if not result or not result.nodes:
        console.print("[dim]No outgoing relationships found.[/dim]")
        return

    _output_graph(result, fmt, title=f"Trace from {symbol!r} (depth={depth})", output_path=output_path)


@app.command(name="blast-radius")
@output.cli_error_guard("blast-radius")
def blast_radius(
    symbol: Annotated[str, typer.Argument(help="Symbol name to assess impact for.")],
    depth: Annotated[int, typer.Option("--depth", "-d", help="Max traversal depth.")] = 3,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Output format: table, tree, dot, mermaid.")] = "table",
    output_path: Annotated[Optional[Path], typer.Option("--output", "-o", help="Save output to file.")] = None,
) -> None:
    """Find everything that could be affected if this symbol changes."""
    engine = _get_engine(command_name="blast-radius")
    store = _get_store(command_name="blast-radius")
    sym_id = _resolve_symbol(engine, symbol, command_name="blast-radius")
    if not sym_id:
        raise typer.Exit(1)

    result = engine.blast_radius_graph(sym_id, depth=depth)

    if output.is_json():
        if not result or not result.nodes:
            output.emit("blast-radius", {
                "root": None,
                "direction": "in",
                "depth": depth,
                "nodes": [],
                "edges": [],
            })
            return
        root_file = store.get_file(result.root.file_id)
        nodes = []
        for n in result.nodes:
            f = store.get_file(n.symbol.file_id)
            nodes.append({
                "symbol": output.symbol_to_dict(n.symbol, f.path if f else ""),
                "depth": n.depth,
                "via": n.via_relationship.value if n.via_relationship else None,
            })
        edges = [
            {
                "source": e.source.id,
                "target": e.target.id,
                "type": e.relationship.value,
                "depth": e.depth,
            }
            for e in result.edges
        ]
        output.emit("blast-radius", {
            "root": output.symbol_to_dict(result.root, root_file.path if root_file else ""),
            "direction": "in",
            "depth": depth,
            "nodes": nodes,
            "edges": edges,
        })
        return

    console = output.get_console()
    if not result or not result.nodes:
        console.print("[dim]No dependent relationships found.[/dim]")
        return

    _output_graph(result, fmt, title=f"Blast radius of {symbol!r} (depth={depth})", output_path=output_path)


@app.command()
@output.cli_error_guard("graph")
def graph(
    symbol: Annotated[str, typer.Argument(help="Root symbol name or qualified name.")],
    depth: Annotated[int, typer.Option("--depth", "-d", help="Max traversal depth.")] = 3,
    direction: Annotated[str, typer.Option("--direction", help="Traversal direction: out, in, both.")] = "out",
    fmt: Annotated[str, typer.Option("--format", "-f", help="Output format: tree, dot, mermaid, table.")] = "tree",
    output_path: Annotated[Optional[Path], typer.Option("--output", "-o", help="Save output to file.")] = None,
) -> None:
    """Visualize dependencies around a symbol as an ASCII tree, DOT graph, or Mermaid diagram."""
    engine = _get_engine(command_name="graph")
    store = _get_store(command_name="graph")
    sym_id = _resolve_symbol(engine, symbol, command_name="graph")
    if not sym_id:
        raise typer.Exit(1)

    dir_val: Any = direction.lower()
    if dir_val not in ("out", "in", "both"):
        dir_val = "out"

    result = engine.walk_graph(sym_id, direction=dir_val, depth=depth)

    if output.is_json():
        if not result or not result.nodes:
            output.emit("graph", {
                "root": None,
                "direction": dir_val,
                "depth": depth,
                "nodes": [],
                "edges": [],
            })
            return
        root_file = store.get_file(result.root.file_id)
        nodes = []
        for n in result.nodes:
            f = store.get_file(n.symbol.file_id)
            nodes.append({
                "symbol": output.symbol_to_dict(n.symbol, f.path if f else ""),
                "depth": n.depth,
                "via": n.via_relationship.value if n.via_relationship else None,
            })
        edges = [
            {
                "source": e.source.id,
                "target": e.target.id,
                "type": e.relationship.value,
                "depth": e.depth,
            }
            for e in result.edges
        ]
        output.emit("graph", {
            "root": output.symbol_to_dict(result.root, root_file.path if root_file else ""),
            "direction": dir_val,
            "depth": depth,
            "nodes": nodes,
            "edges": edges,
        })
        return

    console = output.get_console()
    if not result or not result.nodes:
        console.print("[dim]No graph connections found for this symbol.[/dim]")
        return

    _output_graph(result, fmt, title=f"Graph: {symbol!r} ({direction}, depth={depth})", output_path=output_path)


@app.command()
@output.cli_error_guard("source")
def source(
    symbol: Annotated[str, typer.Argument(help="Symbol name or qualified name.")],
) -> None:
    """Display the exact source lines for a symbol."""
    engine = _get_engine(command_name="source")
    store = _get_store(command_name="source")
    sym_id = _resolve_symbol(engine, symbol, command_name="source")
    if not sym_id:
        raise typer.Exit(1)

    sym = engine.get_definition(sym_id)
    src = engine.get_source(sym_id)
    if not src:
        if output.is_json():
            output.emit_error("source", "SOURCE_UNAVAILABLE", "Could not retrieve source.")
        output.print_rich_error("Could not retrieve source", f"Source lines unavailable for {symbol!r}.")
        raise typer.Exit(1)

    code = "\n".join(src.lines)

    if output.is_json():
        rel_file = src.file_path
        if output._ctx.repo_root:
            try:
                rel_file = str(Path(src.file_path).relative_to(output._ctx.repo_root))
            except ValueError:
                pass
        output.emit("source", {
            "symbol": output.symbol_to_dict(sym, src.file_path) if sym else None,
            "file": rel_file,
            "start_line": src.start_line,
            "end_line": src.end_line,
            "source": code,
        })
        return

    console = output.get_console()
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
@output.cli_error_guard("stats")
def stats() -> None:
    """Show graph statistics: file count, symbol counts by kind, relationship counts."""
    engine = _get_engine(command_name="stats")
    data = engine.stats()

    if output.is_json():
        symbols_by_kind = {
            k.replace("symbols_", "").upper(): v
            for k, v in data.items()
            if k.startswith("symbols_") and k != "symbols"
        }
        relationships_by_type = {
            k.replace("rel_", "").upper(): v
            for k, v in data.items()
            if k.startswith("rel_")
        }
        json_data = {
            "files": data.get("files", 0),
            "symbols": data.get("symbols", 0),
            "relationships": data.get("relationships", 0),
            "unresolved_relationships": data.get("unresolved_relationships", 0),
            "symbols_by_kind": symbols_by_kind,
            "relationships_by_type": relationships_by_type,
        }
        output.emit("stats", json_data)
        return

    console = output.get_console()
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
@output.cli_error_guard("export")
def export_graph(
    output_path: Annotated[Optional[Path], typer.Option("--output", "-o", help="Output file (default: stdout).")] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Output format: json")] = "json",
) -> None:
    """Export the entire graph as JSON."""
    store = _get_store(command_name="export")
    files = [f.model_dump() for f in store.get_all_files()]
    symbols = [s.model_dump() for s in store.get_all_symbols()]

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

    raw_payload = {"files": files, "symbols": symbols, "relationships": unique_rels}

    if output.is_json():
        if output_path:
            output_path.write_text(json.dumps(raw_payload, indent=2, default=str), encoding="utf-8")
            output.emit("export", {"output_file": str(output_path), "files_count": len(files), "symbols_count": len(symbols), "relationships_count": len(unique_rels)})
        else:
            output.emit("export", raw_payload)
        return

    text = json.dumps(raw_payload, indent=2, default=str)
    console = output.get_console()
    if output_path:
        output_path.write_text(text, encoding="utf-8")
        console.print(f"[green]Exported to[/green] {output_path}")
    else:
        print(text)


@app.command(name="help")
def show_help() -> None:
    """Show this help message and exit."""
    console = output.get_console()
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

    table.add_section()
    table.add_row("[bold white]Browser Explorer[/bold white]", "")
    table.add_row("serve", "Launch the interactive graph explorer in your browser.")

    console.print(table)
    console.print("\n[dim]To see options for any command, run: [bold]node-walk <command> --help[/bold][/dim]")


@app.command()
@output.cli_error_guard("serve")
def serve(
    port: Annotated[int, typer.Option("--port", "-p", help="Port to listen on.")] = 7777,
    host: Annotated[str, typer.Option("--host", help="Host to bind to.")] = "localhost",
    no_open: Annotated[bool, typer.Option("--no-open", help="Do not open the browser automatically.")] = False,
) -> None:
    """Launch the interactive graph explorer in your browser."""
    db = _find_db()
    if db is None:
        if output.is_json():
            output.emit_error(
                "serve",
                "NO_INDEX",
                "No .node_walk/graph.db found. Run 'node-walk index <path>' first.",
            )
        output.print_rich_error(
            "No .node_walk/graph.db found.",
            "Run 'node-walk index <path>' first.",
        )
        raise typer.Exit(1)

    url = f"http://{host}:{port}"
    if output.is_json():
        output.emit("serve", {"url": url, "host": host, "port": port, "db": str(db)})
        # Start server without blocking or with requested parameters
        from node_walk.web.server import start_server
        start_server(db, host=host, port=port, open_browser=not no_open, block=True)
        return

    console = output.get_console()
    console.print(
        Panel(
            f"[bold cyan]Graph explorer[/bold cyan] starting at [bold]{url}[/bold]\n"
            f"[dim]DB:[/dim] {db}\n"
            "[dim]Press Ctrl+C to stop.[/dim]",
            title="node-walk serve",
            border_style="cyan",
        )
    )

    from node_walk.web.server import start_server

    start_server(db, host=host, port=port, open_browser=not no_open, block=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
