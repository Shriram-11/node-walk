"""
Query engine — all semantic graph navigation operations.

This is the single layer that knows about graph traversal. The CLI,
future browser API, and LLM skills all go through this module.

All traversal operations (walk, trace, blast_radius) run directly on
SQLite using recursive CTEs — no in-memory graph library needed.
"""

from __future__ import annotations

import difflib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from node_walk.ir.enums import Language, RelationshipType, ResolutionStatus, SymbolKind
from node_walk.ir.models import Relationship, Symbol, SourceLocation
from node_walk.storage.base import GraphStore


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolMatch:
    """A symbol returned by find_symbol(), possibly with a match score."""
    symbol: Symbol
    score: float = 1.0   # 1.0 = exact, < 1.0 = fuzzy/partial


@dataclass(frozen=True)
class SourceRange:
    """Exact source location returned by get_source()."""
    file_path: str
    start_line: int
    end_line: int
    lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WalkResult:
    """A symbol reached during a bounded graph walk."""
    symbol: Symbol
    depth: int
    via_relationship: RelationshipType | None = None


@dataclass(frozen=True)
class TraceEdge:
    """A directed edge traversed during a graph walk/trace."""
    source: Symbol
    target: Symbol
    relationship: RelationshipType
    depth: int


@dataclass(frozen=True)
class TraceResult:
    """Structured tree/graph traversal result."""
    root: Symbol
    nodes: list[WalkResult]
    edges: list[TraceEdge]
    direction: Literal["out", "in", "both"] = "out"


# ---------------------------------------------------------------------------
# Query engine
# ---------------------------------------------------------------------------


class QueryEngine:
    """
    Semantic graph navigation engine.

    Instantiate with a GraphStore and call query methods directly.
    All methods return typed result objects, never raw DB rows.
    """

    def __init__(self, store: GraphStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Symbol lookup
    # ------------------------------------------------------------------

    def find_symbol(self, query: str, limit: int = 20) -> list[SymbolMatch]:
        """
        Find symbols whose name, qualified name, or dotted path matches *query*.

        Matching priority:
          1. Exact qualified_name match (score 1.0)
          2. Exact simple name match (score 0.95)
          3. Dotted path suffix match (e.g. "ModelAdapter.chat" -> "pkg.ModelAdapter.chat") (score 0.90)
          4. Case-insensitive exact name match (score 0.85)
          5. Case-insensitive suffix match on qualified_name (score 0.80)
          6. High-confidence fuzzy match (ratio >= 0.8) (score 0.70-0.79)
          7. Substring match on name (score 0.60)
          8. Moderate fuzzy match (ratio >= 0.6) (score 0.45-0.59)
        """
        query_stripped = query.strip()
        if not query_stripped:
            return []

        results: list[SymbolMatch] = []
        seen: set[str] = set()

        def add(sym: Symbol, score: float) -> None:
            if sym.id not in seen:
                seen.add(sym.id)
                results.append(SymbolMatch(symbol=sym, score=round(score, 3)))

        query_lower = query_stripped.lower()
        has_dot = "." in query_stripped

        # 1. Exact qualified name match
        for s in self._store.find_symbols_by_qualified_name(query_stripped):
            if s.qualified_name == query_stripped:
                add(s, 1.0)

        # 2. Exact name match
        for s in self._store.find_symbols_by_name(query_stripped, exact=True):
            add(s, 0.95)

        # 3. Dotted-path matching if query has dots
        if has_dot:
            # Look for symbols whose qualified_name ends with .query or is query
            all_syms = self._store.get_all_symbols()
            for s in all_syms:
                qname_lower = s.qualified_name.lower()
                if qname_lower == query_lower:
                    add(s, 1.0)
                elif qname_lower.endswith("." + query_lower):
                    add(s, 0.90)
                elif query_lower in qname_lower:
                    add(s, 0.75)
        else:
            # 4 & 5. Case-insensitive name and suffix matching
            for s in self._store.find_symbols_by_name(query_stripped, exact=False):
                s_name_lower = s.name.lower()
                s_qname_lower = s.qualified_name.lower()

                if s_name_lower == query_lower:
                    add(s, 0.85)
                elif s_qname_lower.endswith("." + query_lower):
                    add(s, 0.80)
                elif query_lower in s_name_lower:
                    add(s, 0.60)

        # 6 & 8. Fuzzy matching fallback / enhancement
        # Using SequenceMatcher across all symbol names (fast scan over id, name, qname)
        all_names = self._store.get_all_symbol_names()
        query_parts = query_lower.split(".") if has_dot else []

        fuzzy_candidates: list[tuple[str, float]] = []

        for sym_id, name, qname in all_names:
            if sym_id in seen:
                continue

            name_lower = name.lower()
            qname_lower = qname.lower()

            r_name = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()
            best_ratio = r_name

            if has_dot:
                # Compare against dotted suffix of identical part count
                qname_parts = qname_lower.split(".")
                if len(qname_parts) >= len(query_parts):
                    suffix = ".".join(qname_parts[-len(query_parts):])
                    r_suffix = difflib.SequenceMatcher(None, query_lower, suffix).ratio()
                    if r_suffix > best_ratio:
                        best_ratio = r_suffix
                r_qname = difflib.SequenceMatcher(None, query_lower, qname_lower).ratio()
                if r_qname > best_ratio:
                    best_ratio = r_qname

            if best_ratio >= 0.80:
                fuzzy_score = 0.70 + (best_ratio - 0.80) * 0.45
                fuzzy_candidates.append((sym_id, fuzzy_score))
            elif best_ratio >= 0.60:
                fuzzy_score = 0.45 + (best_ratio - 0.60) * 0.70
                fuzzy_candidates.append((sym_id, fuzzy_score))

        # Add fuzzy candidates
        for sym_id, score in sorted(fuzzy_candidates, key=lambda x: -x[1]):
            sym = self._store.get_symbol(sym_id)
            if sym:
                add(sym, score)

        results.sort(key=lambda m: -m.score)
        return results[:limit]

    def get_symbol_by_id(self, symbol_id: str) -> Symbol | None:
        return self._store.get_symbol(symbol_id)

    # ------------------------------------------------------------------
    # Definition
    # ------------------------------------------------------------------

    def get_definition(self, symbol_id: str) -> Symbol | None:
        """Return the canonical definition symbol for *symbol_id*."""
        return self._store.get_symbol(symbol_id)

    # ------------------------------------------------------------------
    # Callers / callees
    # ------------------------------------------------------------------

    def get_callers(self, symbol_id: str) -> list[tuple[Symbol, Relationship]]:
        """
        Return all symbols that directly call *symbol_id*.
        Returns (caller_symbol, relationship) pairs.
        """
        rels = self._store.get_relationships_to(symbol_id, RelationshipType.CALLS)
        return self._resolve_source_pairs(rels)

    def get_callees(self, symbol_id: str) -> list[tuple[Symbol, Relationship]]:
        """Return all symbols directly called by *symbol_id*."""
        rels = self._store.get_relationships_from(symbol_id, RelationshipType.CALLS)
        return self._resolve_target_pairs(rels)

    # ------------------------------------------------------------------
    # References
    # ------------------------------------------------------------------

    def get_references(self, symbol_id: str) -> list[tuple[Symbol, Relationship]]:
        """Return all symbols that reference (but don't call) *symbol_id*."""
        rels = self._store.get_relationships_to(symbol_id, RelationshipType.REFERENCES)
        return self._resolve_source_pairs(rels)

    # ------------------------------------------------------------------
    # Implementations / extensions
    # ------------------------------------------------------------------

    def get_implementations(self, symbol_id: str) -> list[Symbol]:
        """
        Return symbols that IMPLEMENTS or EXTENDS *symbol_id*.
        Useful for finding concrete implementations of ABCs / Protocols.
        """
        impls = self._store.get_relationships_to(symbol_id, RelationshipType.IMPLEMENTS)
        exts = self._store.get_relationships_to(symbol_id, RelationshipType.EXTENDS)
        syms: list[Symbol] = []
        for rel in impls + exts:
            s = self._store.get_symbol(rel.source_id)
            if s:
                syms.append(s)
        return syms

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def get_importers(self, symbol_id: str) -> list[Symbol]:
        """Return files/modules that import *symbol_id*."""
        rels = self._store.get_relationships_to(symbol_id, RelationshipType.IMPORTS)
        return [s for rel in rels if (s := self._store.get_symbol(rel.source_id))]

    def get_imports(self, symbol_id: str) -> list[Relationship]:
        """Return all IMPORTS relationships from *symbol_id*."""
        return self._store.get_relationships_from(symbol_id, RelationshipType.IMPORTS)

    # ------------------------------------------------------------------
    # Containment
    # ------------------------------------------------------------------

    def get_children(self, symbol_id: str) -> list[Symbol]:
        """Return symbols directly contained by *symbol_id*."""
        rels = self._store.get_relationships_from(symbol_id, RelationshipType.CONTAINS)
        return [s for rel in rels if (s := self._store.get_symbol(rel.target_id))]

    def get_parents(self, symbol_id: str) -> list[Symbol]:
        """Return symbols that contain *symbol_id*."""
        rels = self._store.get_relationships_to(symbol_id, RelationshipType.CONTAINS)
        return [s for rel in rels if (s := self._store.get_symbol(rel.source_id))]

    # ------------------------------------------------------------------
    # Bounded graph walk (BFS via SQLite recursive CTE)
    # ------------------------------------------------------------------

    def walk(
        self,
        start_id: str,
        rel_types: list[RelationshipType] | None = None,
        direction: Literal["out", "in", "both"] = "out",
        depth: int = 3,
    ) -> list[WalkResult]:
        """
        BFS walk from *start_id* along *rel_types* edges up to *depth* hops.

        direction:
          "out"  — follow source → target
          "in"   — follow target → source (reverse)
          "both" — both directions

        Returns a list of WalkResult ordered by depth.
        """
        rel_type_values = [r.value for r in rel_types] if rel_types else None
        rows = self._walk_sql(start_id, rel_type_values, direction, depth)

        results: list[WalkResult] = []
        seen_symbols: set[str] = set()

        for row in rows:
            sym_id = row["symbol_id"]
            if sym_id and sym_id != start_id and sym_id not in seen_symbols:
                seen_symbols.add(sym_id)
                sym = self._store.get_symbol(sym_id)
                if sym:
                    results.append(
                        WalkResult(
                            symbol=sym,
                            depth=row["depth"],
                            via_relationship=RelationshipType(row["rel_type"]) if row["rel_type"] else None,
                        )
                    )
        return results

    def walk_graph(
        self,
        start_id: str,
        rel_types: list[RelationshipType] | None = None,
        direction: Literal["out", "in", "both"] = "out",
        depth: int = 3,
    ) -> TraceResult | None:
        """
        Perform a structured BFS walk from *start_id*, returning both nodes and traversed edges.
        """
        root = self._store.get_symbol(start_id)
        if not root:
            return None

        rel_type_values = [r.value for r in rel_types] if rel_types else None
        rows = self._walk_sql(start_id, rel_type_values, direction, depth)

        nodes: list[WalkResult] = []
        edges: list[TraceEdge] = []
        seen_nodes: set[str] = set()
        seen_edges: set[tuple[str, str, str]] = set()

        # Cache symbol lookups
        sym_cache: dict[str, Symbol | None] = {root.id: root}

        def get_sym(sid: str) -> Symbol | None:
            if sid not in sym_cache:
                sym_cache[sid] = self._store.get_symbol(sid)
            return sym_cache[sid]

        for row in rows:
            from_id = row["from_id"]
            sym_id = row["symbol_id"]
            row_depth = row["depth"]
            row_rel = row["rel_type"]

            if sym_id and sym_id != start_id and sym_id not in seen_nodes:
                seen_nodes.add(sym_id)
                s = get_sym(sym_id)
                if s:
                    nodes.append(
                        WalkResult(
                            symbol=s,
                            depth=row_depth,
                            via_relationship=RelationshipType(row_rel) if row_rel else None,
                        )
                    )

            if from_id and sym_id and row_rel:
                rel_enum = RelationshipType(row_rel)
                # In 'out', from_id -> sym_id. In 'in', dependent was caller so sym_id -> from_id (or from_id is parent)
                if direction == "in":
                    edge_src_id, edge_tgt_id = sym_id, from_id
                else:
                    edge_src_id, edge_tgt_id = from_id, sym_id

                edge_key = (edge_src_id, edge_tgt_id, row_rel)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    src_sym = get_sym(edge_src_id)
                    tgt_sym = get_sym(edge_tgt_id)
                    if src_sym and tgt_sym:
                        edges.append(
                            TraceEdge(
                                source=src_sym,
                                target=tgt_sym,
                                relationship=rel_enum,
                                depth=row_depth,
                            )
                        )

        return TraceResult(root=root, nodes=nodes, edges=edges, direction=direction)

    def _walk_sql(
        self,
        start_id: str,
        rel_types: list[str] | None,
        direction: str,
        depth: int,
    ) -> list[sqlite3.Row]:
        """
        Execute a recursive CTE to perform bounded BFS on the relationships table.
        Returns raw rows with (from_id, symbol_id, depth, rel_type).
        """
        conn = self._store._conn  # type: ignore[attr-defined]

        if direction == "out":
            edge_join = "JOIN relationships r ON r.source_id = walk.symbol_id"
            next_sym = "r.target_id"
        elif direction == "in":
            edge_join = "JOIN relationships r ON r.target_id = walk.symbol_id"
            next_sym = "r.source_id"
        else:  # both
            edge_join = "JOIN relationships r ON (r.source_id = walk.symbol_id OR r.target_id = walk.symbol_id)"
            next_sym = "CASE WHEN r.source_id = walk.symbol_id THEN r.target_id ELSE r.source_id END"

        sql = f"""
        WITH RECURSIVE walk(from_id, symbol_id, depth, rel_type) AS (
            SELECT NULL, ?, 0, NULL
            UNION
            SELECT walk.symbol_id, {next_sym}, walk.depth + 1, r.type
            FROM walk
            {edge_join}
            WHERE walk.depth < ?
              AND {next_sym} != ''
        )
        SELECT from_id, symbol_id, depth, rel_type FROM walk ORDER BY depth
        """
        rows = conn.execute(sql, [start_id, depth]).fetchall()
        if rel_types:
            rows = [r for r in rows if r["rel_type"] is None or r["rel_type"] in rel_types]
        return rows

    # ------------------------------------------------------------------
    # Trace
    # ------------------------------------------------------------------

    def trace(
        self,
        start_id: str,
        depth: int = 5,
        rel_types: list[RelationshipType] | None = None,
    ) -> list[WalkResult]:
        """
        Follow outgoing edges (default: CALLS + IMPORTS) from start to depth.
        Returns all reachable nodes in BFS order.
        """
        if rel_types is None:
            rel_types = [RelationshipType.CALLS, RelationshipType.IMPORTS]
        return self.walk(start_id, rel_types=rel_types, direction="out", depth=depth)

    def trace_graph(
        self,
        start_id: str,
        depth: int = 5,
        rel_types: list[RelationshipType] | None = None,
    ) -> TraceResult | None:
        """
        Trace outgoing edges and return the full graph result (nodes + edges).
        """
        if rel_types is None:
            rel_types = [RelationshipType.CALLS, RelationshipType.IMPORTS]
        return self.walk_graph(start_id, rel_types=rel_types, direction="out", depth=depth)

    # ------------------------------------------------------------------
    # Blast radius
    # ------------------------------------------------------------------

    def blast_radius(
        self,
        start_id: str,
        depth: int = 3,
        rel_types: list[RelationshipType] | None = None,
    ) -> list[WalkResult]:
        """
        Walk *inward* (reverse edges) to find everything that depends on *start_id*.
        Default relationship types: CALLS, IMPORTS, REFERENCES.
        """
        if rel_types is None:
            rel_types = [
                RelationshipType.CALLS,
                RelationshipType.IMPORTS,
                RelationshipType.REFERENCES,
            ]
        return self.walk(start_id, rel_types=rel_types, direction="in", depth=depth)

    def blast_radius_graph(
        self,
        start_id: str,
        depth: int = 3,
        rel_types: list[RelationshipType] | None = None,
    ) -> TraceResult | None:
        """
        Assess blast radius and return the full graph result (nodes + edges).
        """
        if rel_types is None:
            rel_types = [
                RelationshipType.CALLS,
                RelationshipType.IMPORTS,
                RelationshipType.REFERENCES,
            ]
        return self.walk_graph(start_id, rel_types=rel_types, direction="in", depth=depth)

    # ------------------------------------------------------------------
    # Source retrieval
    # ------------------------------------------------------------------

    def get_source(self, symbol_id: str) -> SourceRange | None:
        """
        Return the exact source lines for a symbol.
        Reads from disk on every call (no caching in MVP).
        """
        sym = self._store.get_symbol(symbol_id)
        if not sym:
            return None

        file_info = self._store.get_file(sym.file_id)
        if not file_info:
            return None

        try:
            all_lines = Path(file_info.path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

        # Convert to 0-indexed slice
        start = max(0, sym.start_line - 1)
        end = min(len(all_lines), sym.end_line)
        snippet = all_lines[start:end]

        return SourceRange(
            file_path=file_info.path,
            start_line=sym.start_line,
            end_line=sym.end_line,
            lines=snippet,
        )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        return self._store.stats()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_source_pairs(self, rels: list[Relationship]) -> list[tuple[Symbol, Relationship]]:
        pairs: list[tuple[Symbol, Relationship]] = []
        for rel in rels:
            s = self._store.get_symbol(rel.source_id)
            if s:
                pairs.append((s, rel))
        return pairs

    def _resolve_target_pairs(self, rels: list[Relationship]) -> list[tuple[Symbol, Relationship]]:
        pairs: list[tuple[Symbol, Relationship]] = []
        for rel in rels:
            if rel.target_id:
                s = self._store.get_symbol(rel.target_id)
                if s:
                    pairs.append((s, rel))
        return pairs
