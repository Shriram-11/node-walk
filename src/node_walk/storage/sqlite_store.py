"""
SQLite implementation of GraphStore.

All SQL is contained here. The rest of the codebase only depends on
the abstract GraphStore interface from node_walk.storage.base.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from node_walk.ir.enums import Language, RelationshipType, ResolutionStatus, SymbolKind
from node_walk.ir.models import (
    AnalysisResult,
    FileInfo,
    Relationship,
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

    def store_results(self, results: list[AnalysisResult]) -> None:
        """Bulk-insert all files, symbols, and relationships in a single transaction."""
        with self._conn:
            for res in results:
                self._upsert_file(res.file)
                for sym in res.symbols:
                    self._insert_symbol(sym)
                for rel in res.relationships:
                    self._insert_relationship(rel)

    def clear(self) -> None:
        with self._conn:
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

    def stats(self) -> dict[str, int]:
        files = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        symbols = self._conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        rels = self._conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
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

        return {
            "files": files,
            "symbols": symbols,
            "relationships": rels,
            "unresolved_relationships": unresolved,
            **kind_counts,
            **rel_counts,
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
