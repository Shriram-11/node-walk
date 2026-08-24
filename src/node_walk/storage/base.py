"""
Storage base — abstract GraphStore interface.

All storage backends must implement GraphStore. This keeps the
query engine and indexer decoupled from any concrete technology.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from node_walk.ir.enums import FactStatus, FactType, RelationshipType, ResolutionStatus
from node_walk.ir.models import AnalysisResult, FileInfo, Relationship, RelationshipFact, Symbol


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
    def store_relationships(self, relationships: list[Relationship]) -> None:
        """Persist multiple relationships."""
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

    @abstractmethod
    def store_fact(self, fact: RelationshipFact) -> None:
        """Persist a single relationship fact."""
        ...

    @abstractmethod
    def store_facts(self, facts: list[RelationshipFact]) -> None:
        """Persist multiple relationship facts."""
        ...

    @abstractmethod
    def update_relationship_fact(
        self,
        fact_id: str,
        *,
        status: FactStatus,
        resolved_target_id: str = "",
        resolver_name: str = "",
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        """Update the resolution state for a stored relationship fact."""
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
    def get_all_symbol_names(self) -> list[tuple[str, str, str]]:
        """Return (id, name, qualified_name) tuples for all symbols."""
        ...

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

    @abstractmethod
    def get_relationship_facts(
        self,
        fact_type: FactType | None = None,
        status: FactStatus | None = None,
    ) -> list[RelationshipFact]: ...

    # --- Stats --------------------------------------------------------------

    @abstractmethod
    def stats(self) -> dict[str, int]: ...
