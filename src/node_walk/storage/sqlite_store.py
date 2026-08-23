"""
SQLite implementation of GraphStore.

All SQL is contained here. The rest of the codebase only depends on
the abstract GraphStore interface from node_walk.storage.base.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from node_walk.ir.enums import (
    FactStatus,
    FactType,
    Language,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
)
from node_walk.ir.models import (
    AnalysisResult,
    FileInfo,
    Relationship,
    RelationshipFact,
    SourceLocation,
    Symbol,
)
from node_walk.storage import schema as _schema
from node_walk.storage.base import GraphStore


class SQLiteGraphStore(GraphStore):
    """
    SQLite-backed graph store.

    Usage::

        store = SQLiteGraphStore(".node_walk/graph.db")
        store.store_results(analysis_results)
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        _schema.initialize(self._conn)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def store_result(self, result: AnalysisResult) -> None:
        self.store_results([result])

    def store_relationships(self, relationships: list[Relationship]) -> None:
        """Bulk-insert all relationships in a single transaction."""
        with self._conn:
            for rel in relationships:
                self._insert_relationship(rel)

    def store_results(self, results: list[AnalysisResult]) -> None:
        """Bulk-insert all files, symbols, and relationships in a single transaction."""
        with self._conn:
            for res in results:
                self._upsert_file(res.file)
                for sym in res.symbols:
                    self._insert_symbol(sym)
                for rel in res.relationships:
                    self._insert_relationship(rel)
                for fact in res.relationship_facts:
                    self._insert_relationship_fact(fact)

    def clear(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM relationship_facts")
            self._conn.execute("DELETE FROM relationships")
            self._conn.execute("DELETE FROM symbols")
            self._conn.execute("DELETE FROM files")

    def update_relationship(
        self, rel_id: str, target_id: str, resolution: ResolutionStatus
    ) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE relationships SET target_id = ?, resolution = ? WHERE id = ?",
                (target_id, resolution.value, rel_id),
            )

    def store_fact(self, fact: RelationshipFact) -> None:
        self.store_facts([fact])

    def store_facts(self, facts: list[RelationshipFact]) -> None:
        with self._conn:
            for fact in facts:
                self._insert_relationship_fact(fact)

    def update_relationship_fact(
        self,
        fact_id: str,
        *,
        status: FactStatus,
        resolved_target_id: str = "",
        resolver_name: str = "",
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        diagnostics_json = json.dumps(diagnostics or {})
        with self._conn:
            self._conn.execute(
                """
                UPDATE relationship_facts
                SET status = ?, resolved_target_id = ?, resolver_name = ?, diagnostics_json = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    resolved_target_id,
                    resolver_name,
                    diagnostics_json,
                    fact_id,
                ),
            )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_file(self, file_id: str) -> FileInfo | None:
        row = self._conn.execute(
            "SELECT * FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        return self._row_to_file(row) if row else None

    def get_all_files(self) -> list[FileInfo]:
        rows = self._conn.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [self._row_to_file(r) for r in rows]

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        row = self._conn.execute(
            "SELECT * FROM symbols WHERE id = ?", (symbol_id,)
        ).fetchone()
        return self._row_to_symbol(row) if row else None

    def find_symbols_by_name(self, name: str, exact: bool = False) -> list[Symbol]:
        if exact:
            rows = self._conn.execute(
                "SELECT * FROM symbols WHERE name = ? ORDER BY qualified_name",
                (name,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM symbols WHERE name LIKE ? ORDER BY qualified_name",
                (f"%{name}%",),
            ).fetchall()
        return [self._row_to_symbol(r) for r in rows]

    def find_symbols_by_qualified_name(self, qname: str) -> list[Symbol]:
        rows = self._conn.execute(
            "SELECT * FROM symbols WHERE qualified_name = ? OR qualified_name LIKE ?",
            (qname, f"%{qname}%"),
        ).fetchall()
        return [self._row_to_symbol(r) for r in rows]

    def get_all_symbols(self) -> list[Symbol]:
        rows = self._conn.execute(
            "SELECT * FROM symbols ORDER BY qualified_name"
        ).fetchall()
        return [self._row_to_symbol(r) for r in rows]

    def get_all_symbol_names(self) -> list[tuple[str, str, str]]:
        rows = self._conn.execute(
            "SELECT id, name, qualified_name FROM symbols ORDER BY qualified_name"
        ).fetchall()
        return [(r["id"], r["name"], r["qualified_name"]) for r in rows]

    def get_relationships_from(
        self, symbol_id: str, rel_type: RelationshipType | None = None
    ) -> list[Relationship]:
        if rel_type:
            rows = self._conn.execute(
                "SELECT * FROM relationships WHERE source_id = ? AND type = ?",
                (symbol_id, rel_type.value),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM relationships WHERE source_id = ?", (symbol_id,)
            ).fetchall()
        return [self._row_to_rel(r) for r in rows]

    def get_relationships_to(
        self, symbol_id: str, rel_type: RelationshipType | None = None
    ) -> list[Relationship]:
        if rel_type:
            rows = self._conn.execute(
                "SELECT * FROM relationships WHERE target_id = ? AND type = ?",
                (symbol_id, rel_type.value),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM relationships WHERE target_id = ?", (symbol_id,)
            ).fetchall()
        return [self._row_to_rel(r) for r in rows]

    def get_all_unresolved_relationships(self) -> list[Relationship]:
        rows = self._conn.execute(
            "SELECT * FROM relationships WHERE target_id = '' OR resolution = 'unresolved'"
        ).fetchall()
        return [self._row_to_rel(r) for r in rows]

    def get_relationship_facts(
        self,
        fact_type: FactType | None = None,
        status: FactStatus | None = None,
    ) -> list[RelationshipFact]:
        sql = "SELECT * FROM relationship_facts"
        clauses: list[str] = []
        params: list[str] = []

        if fact_type:
            clauses.append("fact_type = ?")
            params.append(fact_type.value)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY source_symbol_id, source_line, source_col, id"

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def stats(self) -> dict[str, int]:
        files = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        symbols = self._conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        rels = self._conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
        facts = self._conn.execute("SELECT COUNT(*) FROM relationship_facts").fetchone()[0]
        unresolved = self._conn.execute(
            "SELECT COUNT(*) FROM relationships WHERE target_id = ''"
        ).fetchone()[0]

        kind_rows = self._conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM symbols GROUP BY kind"
        ).fetchall()
        kind_counts = {f"symbols_{r['kind'].lower()}": r["cnt"] for r in kind_rows}

        rel_rows = self._conn.execute(
            "SELECT type, COUNT(*) as cnt FROM relationships GROUP BY type"
        ).fetchall()
        rel_counts = {f"rel_{r['type'].lower()}": r["cnt"] for r in rel_rows}

        fact_type_rows = self._conn.execute(
            "SELECT fact_type, COUNT(*) as cnt FROM relationship_facts GROUP BY fact_type"
        ).fetchall()
        fact_type_counts = {f"facts_{r['fact_type'].lower()}": r["cnt"] for r in fact_type_rows}

        fact_status_rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM relationship_facts GROUP BY status"
        ).fetchall()
        fact_status_counts = {
            f"facts_status_{r['status'].lower()}": r["cnt"] for r in fact_status_rows
        }

        return {
            "files": files,
            "symbols": symbols,
            "relationships": rels,
            "relationship_facts": facts,
            "unresolved_relationships": unresolved,
            **kind_counts,
            **rel_counts,
            **fact_type_counts,
            **fact_status_counts,
        }

    # ------------------------------------------------------------------
    # Private helpers — insert
    # ------------------------------------------------------------------

    def _upsert_file(self, file: FileInfo) -> None:
        self._conn.execute(
            """
            INSERT INTO files (id, path, language, content_hash, size_bytes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                language     = excluded.language,
                content_hash = excluded.content_hash,
                size_bytes   = excluded.size_bytes,
                indexed_at   = datetime('now')
            """,
            (file.id, file.path, file.language.value, file.content_hash, file.size_bytes),
        )

    def _insert_symbol(self, sym: Symbol) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO symbols
                (id, name, qualified_name, kind, language, file_id,
                 start_line, end_line, signature, parent_id, docstring, is_async)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sym.id, sym.name, sym.qualified_name, sym.kind.value,
                sym.language.value, sym.file_id, sym.start_line, sym.end_line,
                sym.signature, sym.parent_id, sym.docstring, int(sym.is_async),
            ),
        )

    def _insert_relationship(self, rel: Relationship) -> None:
        loc = rel.source_location
        self._conn.execute(
            """
            INSERT OR IGNORE INTO relationships
                (id, source_id, target_id, type, source_file_id, source_line,
                 source_col, resolution, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rel.id, rel.source_id, rel.target_id, rel.type.value,
                loc.file_id if loc else None,
                loc.line if loc else None,
                loc.col if loc else None,
                rel.resolution.value,
                json.dumps(rel.metadata),
            ),
        )

    def _insert_relationship_fact(self, fact: RelationshipFact) -> None:
        loc = fact.source_location
        self._conn.execute(
            """
            INSERT OR IGNORE INTO relationship_facts
                (id, file_id, source_symbol_id, fact_type, raw_text, simple_name,
                 receiver_text, qualified_hint, source_line, source_col, scope_symbol_id,
                 status, resolved_target_id, resolver_name, metadata_json, diagnostics_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact.id,
                fact.file_id,
                fact.source_symbol_id,
                fact.fact_type.value,
                fact.raw_text,
                fact.simple_name,
                fact.receiver_text,
                fact.qualified_hint,
                loc.line if loc else None,
                loc.col if loc else None,
                fact.scope_symbol_id,
                fact.status.value,
                fact.resolved_target_id,
                fact.resolver_name,
                json.dumps(fact.metadata),
                json.dumps(fact.diagnostics),
            ),
        )

    # ------------------------------------------------------------------
    # Private helpers — row → model conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_file(row: sqlite3.Row) -> FileInfo:
        return FileInfo(
            id=row["id"],
            path=row["path"],
            language=Language(row["language"]),
            content_hash=row["content_hash"],
            size_bytes=row["size_bytes"],
        )

    @staticmethod
    def _row_to_symbol(row: sqlite3.Row) -> Symbol:
        return Symbol(
            id=row["id"],
            name=row["name"],
            qualified_name=row["qualified_name"],
            kind=SymbolKind(row["kind"]),
            language=Language(row["language"]),
            file_id=row["file_id"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            signature=row["signature"] or "",
            parent_id=row["parent_id"],
            docstring=row["docstring"] or "",
            is_async=bool(row["is_async"]),
        )

    @staticmethod
    def _row_to_rel(row: sqlite3.Row) -> Relationship:
        loc: SourceLocation | None = None
        if row["source_file_id"] and row["source_line"]:
            loc = SourceLocation(
                file_id=row["source_file_id"],
                line=row["source_line"],
                col=row["source_col"] or 0,
            )
        return Relationship(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"] or "",
            type=RelationshipType(row["type"]),
            source_location=loc,
            resolution=ResolutionStatus(row["resolution"]),
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _row_to_fact(row: sqlite3.Row) -> RelationshipFact:
        loc: SourceLocation | None = None
        if row["source_line"] is not None:
            loc = SourceLocation(
                file_id=row["file_id"],
                line=row["source_line"],
                col=row["source_col"] or 0,
            )
        return RelationshipFact(
            id=row["id"],
            file_id=row["file_id"],
            source_symbol_id=row["source_symbol_id"],
            fact_type=FactType(row["fact_type"]),
            raw_text=row["raw_text"],
            simple_name=row["simple_name"] or "",
            receiver_text=row["receiver_text"] or "",
            qualified_hint=row["qualified_hint"] or "",
            source_location=loc,
            scope_symbol_id=row["scope_symbol_id"],
            status=FactStatus(row["status"]),
            resolved_target_id=row["resolved_target_id"] or "",
            resolver_name=row["resolver_name"] or "",
            metadata=json.loads(row["metadata_json"]),
            diagnostics=json.loads(row["diagnostics_json"]),
        )
