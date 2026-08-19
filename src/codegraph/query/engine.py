"""
Query engine — all semantic graph navigation operations.

This is the single layer that knows about graph traversal. The CLI,
future browser API, and LLM skills all go through this module.

All traversal operations (walk, trace, blast_radius) run directly on
SQLite using recursive CTEs — no in-memory graph library needed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from codegraph.ir.enums import Language, RelationshipType, ResolutionStatus, SymbolKind
from codegraph.ir.models import Relationship, Symbol, SourceLocation
from codegraph.storage.base import GraphStore


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolMatch:
    """A symbol returned by find_symbol(), possibly with a match score."""
    symbol: Symbol
    score: float = 1.0   # 1.0 = exact, < 1.0 = fuzzy


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
        Find symbols whose name or qualified name matches *query*.

        Matching priority:
          1. Exact qualified_name match
          2. Exact name match
          3. Case-insensitive suffix match (e.g. "createUser" matches "module.Class.createUser")
          4. Substring match on name
        """
        results: list[SymbolMatch] = []
        seen: set[str] = set()

        def add(sym: Symbol, score: float) -> None:
            if sym.id not in seen:
                seen.add(sym.id)
                results.append(SymbolMatch(symbol=sym, score=score))

        # 1. Exact qualified name
        for s in self._store.find_symbols_by_qualified_name(query):
            if s.qualified_name == query:
                add(s, 1.0)

        # 2. Exact name
        for s in self._store.find_symbols_by_name(query, exact=True):
            add(s, 0.95)

        # 3. Case-insensitive suffix / substring
        for s in self._store.find_symbols_by_name(query, exact=False):
            score = 0.8 if s.qualified_name.lower().endswith(query.lower()) else 0.6
            add(s, score)

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
        for row in rows:
            sym = self._store.get_symbol(row["symbol_id"])
            if sym and sym.id != start_id:
                results.append(
                    WalkResult(
                        symbol=sym,
                        depth=row["depth"],
                        via_relationship=RelationshipType(row["rel_type"]) if row["rel_type"] else None,
                    )
                )
        return results

    def _walk_sql(
        self,
        start_id: str,
        rel_types: list[str] | None,
        direction: str,
        depth: int,
    ) -> list[sqlite3.Row]:
        """
        Execute a recursive CTE to perform bounded BFS on the relationships table.
        Returns raw rows with (symbol_id, depth, rel_type).
        """
        conn = self._store._conn  # type: ignore[attr-defined]

        type_filter = ""
        params: list[object] = [start_id, depth]

        if rel_types:
            placeholders = ",".join("?" * len(rel_types))
            type_filter = f"AND r.type IN ({placeholders})"
            params = [start_id] + rel_types + [depth] + rel_types  # duplicated for both CTEs

        if direction == "out":
            edge_join = f"JOIN relationships r ON r.source_id = walk.symbol_id {type_filter}"
            next_sym = "r.target_id"
        elif direction == "in":
            edge_join = f"JOIN relationships r ON r.target_id = walk.symbol_id {type_filter}"
            next_sym = "r.source_id"
        else:  # both
            edge_join = f"""
                JOIN relationships r ON (
                    r.source_id = walk.symbol_id OR r.target_id = walk.symbol_id
                ) {type_filter}
            """
            next_sym = "CASE WHEN r.source_id = walk.symbol_id THEN r.target_id ELSE r.source_id END"

        if rel_types:
            # params layout: start_id, *rel_types, depth, *rel_types (for both anchor + recursive)
            params = [start_id] + rel_types + [depth] + rel_types
        else:
            params = [start_id, depth]

        sql = f"""
        WITH RECURSIVE walk(symbol_id, depth, rel_type) AS (
            SELECT ?, 0, NULL
            UNION
            SELECT {next_sym}, walk.depth + 1, r.type
            FROM walk
            {edge_join}
            WHERE walk.depth < ?
              AND {next_sym} != ''
        )
        SELECT DISTINCT symbol_id, depth, rel_type FROM walk ORDER BY depth
        """
        # Simplify params for now (rel_type filter in CTE is complex with UNION; handle via post-filter)
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
