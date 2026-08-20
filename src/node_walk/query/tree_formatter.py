"""
Tree and graph formatters for node-walk query results.

Provides ASCII tree, Graphviz DOT, and Mermaid diagram formatters
for TraceResult instances.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TextIO
from node_walk.ir.enums import RelationshipType, SymbolKind
from node_walk.query.engine import TraceResult, TraceEdge
from node_walk.ir.models import Symbol


def format_ascii_tree(result: TraceResult) -> str:
    """
    Format a TraceResult as an ASCII/Unicode hierarchy tree.
    """
    if not result.nodes and not result.edges:
        return f"{result.root.qualified_name} ({result.root.kind.value})\n  (no relationships found)"

    # Build adjacency list: parent_id -> list of (child_sym, relationship)
    # If direction is "out", root is source, targets are children
    # If direction is "in", root is target, sources (dependents) are displayed
    adj: dict[str, list[tuple[Symbol, RelationshipType]]] = defaultdict(list)
    seen_edges: set[tuple[str, str, str]] = set()

    for edge in result.edges:
        if result.direction == "in":
            # For in-direction (blast-radius): root -> callers
            parent_id = edge.target.id
            child_sym = edge.source
        else:
            # For out-direction (trace): root -> callees/imports
            parent_id = edge.source.id
            child_sym = edge.target

        edge_key = (parent_id, child_sym.id, edge.relationship.value)
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            adj[parent_id].append((child_sym, edge.relationship))

    lines: list[str] = []
    root_header = f"{result.root.qualified_name} [{result.root.kind.value}]"
    if result.direction == "in":
        root_header += " (Impacted Root)"
    lines.append(root_header)

    visited: set[str] = {result.root.id}

    def _render_children(parent_id: str, prefix: str) -> None:
        children = adj.get(parent_id, [])
        count = len(children)
        for i, (child, rel) in enumerate(children):
            is_last = (i == count - 1)
            connector = "\\-- " if is_last else "|-- "
            rel_tag = f"--[{rel.value}]--> "
            line_str = f"{prefix}{connector}{rel_tag}{child.qualified_name} [{child.kind.value}]"
            
            if child.id in visited:
                lines.append(f"{line_str} (cycle/seen)")
                continue

            lines.append(line_str)
            visited.add(child.id)
            new_prefix = prefix + ("    " if is_last else "|   ")
            _render_children(child.id, new_prefix)

    _render_children(result.root.id, "")
    return "\n".join(lines)


def _sanitize_dot_id(sym_id: str) -> str:
    return f"node_{sym_id.replace('-', '_')}"


def format_dot(result: TraceResult, graph_name: str = "CodeGraph") -> str:
    """
    Format a TraceResult as a Graphviz DOT diagram.
    """
    lines: list[str] = [
        f"digraph {graph_name} {{",
        '  rankdir="LR";',
        '  node [shape="box", style="rounded,filled", fontname="Helvetica", fontsize=10];',
        '  edge [fontname="Helvetica", fontsize=9];',
        "",
    ]

    # Collect all unique symbols
    symbols: dict[str, Symbol] = {result.root.id: result.root}
    for n in result.nodes:
        symbols[n.symbol.id] = n.symbol
    for e in result.edges:
        symbols[e.source.id] = e.source
        symbols[e.target.id] = e.target

    # Color palette for node kinds
    kind_colors = {
        SymbolKind.CLASS: "#E1F5FE",       # light blue
        SymbolKind.INTERFACE: "#E8EAF6",   # indigo tint
        SymbolKind.FUNCTION: "#E8F5E9",    # light green
        SymbolKind.METHOD: "#FFF3E0",      # light orange
        SymbolKind.MODULE: "#F3E5F5",      # light purple
        SymbolKind.PACKAGE: "#FCE4EC",     # pink tint
        SymbolKind.FILE: "#ECEFF1",        # light grey
        SymbolKind.CONSTANT: "#FFFDE7",    # pale yellow
        SymbolKind.VARIABLE: "#FAFAFA",
        SymbolKind.FIELD: "#FAFAFA",
    }

    # Node definitions
    for sym_id, sym in symbols.items():
        dot_id = _sanitize_dot_id(sym_id)
        fill = kind_colors.get(sym.kind, "#FFFFFF")
        is_root = (sym_id == result.root.id)
        penwidth = "2.0" if is_root else "1.0"
        color = "#0288D1" if is_root else "#B0BEC5"

        label = f"{sym.qualified_name}\\n[{sym.kind.value}]"
        lines.append(
            f'  {dot_id} [label="{label}", fillcolor="{fill}", color="{color}", penwidth={penwidth}];'
        )

    lines.append("")

    # Edge definitions
    rel_styles = {
        RelationshipType.CALLS: 'color="#1976D2", style="solid"',
        RelationshipType.IMPORTS: 'color="#7B1FA2", style="dashed"',
        RelationshipType.REFERENCES: 'color="#616161", style="dotted"',
        RelationshipType.EXTENDS: 'color="#388E3C", style="bold"',
        RelationshipType.IMPLEMENTS: 'color="#00796B", style="dashed"',
        RelationshipType.CONTAINS: 'color="#BDBDBD", style="dotted"',
    }

    seen_edges: set[tuple[str, str, str]] = set()
    for edge in result.edges:
        src_dot = _sanitize_dot_id(edge.source.id)
        tgt_dot = _sanitize_dot_id(edge.target.id)
        edge_key = (src_dot, tgt_dot, edge.relationship.value)
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            style = rel_styles.get(edge.relationship, 'color="#757575"')
            lines.append(f'  {src_dot} -> {tgt_dot} [label="{edge.relationship.value}", {style}];')

    lines.append("}")
    return "\n".join(lines)


def format_mermaid(result: TraceResult) -> str:
    """
    Format a TraceResult as a Mermaid graph.
    """
    lines: list[str] = [
        "graph LR",
    ]

    symbols: dict[str, Symbol] = {result.root.id: result.root}
    for n in result.nodes:
        symbols[n.symbol.id] = n.symbol
    for e in result.edges:
        symbols[e.source.id] = e.source
        symbols[e.target.id] = e.target

    # Node definitions
    for sym_id, sym in symbols.items():
        m_id = _sanitize_dot_id(sym_id)
        display = f'"{sym.qualified_name}<br/>({sym.kind.value})"'
        if sym_id == result.root.id:
            lines.append(f"  {m_id}[{display}]:::rootClass")
        else:
            lines.append(f"  {m_id}[{display}]")

    seen_edges: set[tuple[str, str, str]] = set()
    for edge in result.edges:
        src_id = _sanitize_dot_id(edge.source.id)
        tgt_id = _sanitize_dot_id(edge.target.id)
        edge_key = (src_id, tgt_id, edge.relationship.value)
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            lines.append(f"  {src_id} -- {edge.relationship.value} --> {tgt_id}")

    lines.append("  classDef rootClass fill:#0288D1,stroke:#01579B,stroke-width:2px,color:#fff")
    return "\n".join(lines)
