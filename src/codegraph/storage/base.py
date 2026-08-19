"""
Storage base — abstract GraphStore interface.

All storage backends must implement GraphStore. This keeps the
query engine and indexer decoupled from any concrete technology.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from codegraph.ir.models import AnalysisResult, FileInfo, Relationship, Symbol
from codegraph.ir.enums import RelationshipType, ResolutionStatus


class GraphStore(ABC):
    """Abstract graph storage backend."""

    # --- Write operations ---------------------------------------------------

    @abstractmethod
    def store_result(self, result: AnalysisResult) -> None:
        """Persist a single file's analysis result (file + symbols + relationships)."""
        ...

    @abstractmethod
    def store_results(self, results: list[AnalysisResult]) -> None:
        """Persist multiple files' results in a single transaction."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all data. Used before a full re-index."""
        ...

    @abstractmethod
    def update_relationship(
        self, rel_id: str, target_id: str, resolution: ResolutionStatus
    ) -> None:
        """Update a relationship's resolved target and status (cross-file resolution pass)."""
        ...

    # --- Read operations ----------------------------------------------------

    @abstractmethod
    def get_file(self, file_id: str) -> FileInfo | None: ...

    @abstractmethod
    def get_all_files(self) -> list[FileInfo]: ...

    @abstractmethod
    def get_symbol(self, symbol_id: str) -> Symbol | None: ...

    @abstractmethod
    def find_symbols_by_name(self, name: str, exact: bool = False) -> list[Symbol]: ...

    @abstractmethod
    def find_symbols_by_qualified_name(self, qname: str) -> list[Symbol]: ...

    @abstractmethod
    def get_all_symbols(self) -> list[Symbol]: ...

    @abstractmethod
    def get_relationships_from(
        self, symbol_id: str, rel_type: RelationshipType | None = None
    ) -> list[Relationship]: ...

    @abstractmethod
    def get_relationships_to(
        self, symbol_id: str, rel_type: RelationshipType | None = None
    ) -> list[Relationship]: ...

    @abstractmethod
    def get_all_unresolved_relationships(self) -> list[Relationship]: ...

    # --- Stats --------------------------------------------------------------

    @abstractmethod
    def stats(self) -> dict[str, int]: ...
