"""
CLI output — handles both Rich (human) and JSON (machine) output modes.
"""

from __future__ import annotations

import functools
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import typer
from rich.console import Console
from rich.panel import Panel

from node_walk.ir.models import Relationship, Symbol


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


def emit_error(command: str, code: str, message: str, exit_code: int = 1, **extra: Any) -> None:
    """Emit a failed JSON response to stdout and exit."""
    error = {"code": code, "message": message, **extra}
    response = {
        "ok": False,
        "command": command,
        "data": None,
        "error": error,
    }
    print(json.dumps(response, indent=2, default=str))
    sys.exit(exit_code)


def print_rich_error(title: str, message: str, hint: str | None = None) -> None:
    """Print an attractive, consistent Rich error panel to stderr."""
    err_cons = get_err_console()
    content = f"[red bold]{title}[/red bold]\n\n{message}"
    if hint:
        content += f"\n[dim]{hint}[/dim]"
    err_cons.print(Panel(content, title="Error", border_style="red"))


def cli_error_guard(command_name: str) -> Callable:
    """Decorator that catches unhandled exceptions and routes them cleanly."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except typer.Exit:
                raise
            except SystemExit:
                raise
            except Exception as exc:
                if is_json():
                    emit_error(
                        command_name,
                        "INTERNAL_ERROR",
                        f"Unexpected error: {exc}",
                        exit_code=2,
                        traceback=traceback.format_exc(),
                    )
                else:
                    err_cons = get_err_console()
                    err_cons.print(f"[red bold]Unexpected error:[/red bold] {exc}")
                    err_cons.print("[dim]Run with -v or inspect logs for full traceback.[/dim]")
                raise typer.Exit(2)
        return wrapper
    return decorator


def symbol_to_dict(sym: Symbol, file_path: str | None = None) -> dict[str, Any]:
    """Convert a Symbol to the standard SymbolJSON dict."""
    rel_path = file_path or ""
    if _ctx.repo_root and rel_path:
        try:
            rel_path = str(Path(rel_path).relative_to(_ctx.repo_root))
        except ValueError:
            pass
    elif rel_path:
        try:
            # Fallback relative to current working directory if repo_root is unset
            cwd = Path.cwd().resolve()
            p = Path(rel_path).resolve()
            if p.is_relative_to(cwd):
                rel_path = str(p.relative_to(cwd))
        except ValueError:
            pass

    return {
        "id": sym.id,
        "name": sym.name,
        "qualified_name": sym.qualified_name,
        "kind": sym.kind.value if hasattr(sym.kind, "value") else str(sym.kind),
        "language": sym.language.value if hasattr(sym.language, "value") else str(sym.language),
        "file": rel_path,
        "start_line": sym.start_line,
        "end_line": sym.end_line,
        "signature": sym.signature or None,
        "is_async": sym.is_async,
        "docstring": (sym.docstring[:500] if sym.docstring else None),
    }


def relationship_to_dict(rel: Relationship) -> dict[str, Any]:
    """Convert a Relationship to standard RelationshipJSON dict."""
    loc = None
    if rel.source_location:
        loc = {
            "line": rel.source_location.line,
            "col": rel.source_location.col,
        }
    return {
        "type": rel.type.value if hasattr(rel.type, "value") else str(rel.type),
        "resolution": rel.resolution.value if hasattr(rel.resolution, "value") else str(rel.resolution),
        "source_location": loc,
    }
