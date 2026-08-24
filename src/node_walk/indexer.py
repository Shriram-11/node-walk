"""
Indexer — orchestrates file discovery, analysis, storage, and cross-file resolution.

Usage:
    from node_walk.indexer import Indexer
    from node_walk.storage.repository import SQLiteGraphStore

    store = SQLiteGraphStore(".node_walk/graph.db")
    indexer = Indexer(store)
    indexer.index("./my_repo")
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from node_walk.analysis.base import FileDiscovery, LanguageAnalyzer
from node_walk.analysis.python import PythonAnalyzer
from node_walk.ir.models import AnalysisResult, Relationship
from node_walk.ir.enums import Language, RelationshipType, ResolutionStatus, FactStatus, FactType
from node_walk.storage.base import GraphStore
from node_walk.resolution.calls import (
    NoiseFilterCallResolver,
    ClassMemberCallResolver,
    InFileCallResolver,
    ConstructorCallResolver,
    CrossFileCallResolver,
)
from node_walk.resolution.imports import ImportResolver


class Indexer:
    """
    Orchestrates a full index run:
      1. Discover files
      2. Analyze each file with the appropriate LanguageAnalyzer
      3. Store all results
      4. Run cross-file resolution pass (fix EXTENDS/IMPLEMENTS/CALLS/IMPORTS)
    """

    def __init__(
        self,
        store: GraphStore,
        analyzers: list[LanguageAnalyzer] | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self._store = store
        self._analyzers: list[LanguageAnalyzer] = analyzers or [PythonAnalyzer()]
        self._progress = progress_callback  # (file_path, current, total)

        # Build language → analyzer map
        self._lang_map: dict[Language, LanguageAnalyzer] = {}
        for a in self._analyzers:
            for lang in a.supported_languages:
                self._lang_map[lang] = a

    def index(self, root: str | Path, clear: bool = True) -> IndexStats:
        """
        Index *root* directory.

        If *clear* is True (default), wipes the existing graph first
        (full re-index). Set to False for incremental updates (future).
        """
        root = Path(root).resolve()

        if clear:
            self._store.clear()

        # --- 1. Discover files ---
        discovery = FileDiscovery(root, include_languages=list(self._lang_map.keys()))
        file_pairs = discovery.discover()
        total = len(file_pairs)

        # --- 2. Analyze ---
        results: list[AnalysisResult] = []
        errors: list[str] = []

        for i, (file_info, source) in enumerate(file_pairs, start=1):
            if self._progress:
                self._progress(file_info.path, i, total)

            analyzer = self._lang_map.get(file_info.language)
            if not analyzer:
                continue

            try:
                result = analyzer.analyze(file_info, source)
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{file_info.path}: {exc}")

        # --- 3. Store ---
        self._store.store_results(results)

        # --- 4. Run Resolver Pipeline ---
        resolved_facts_count = self._run_resolvers()

        # --- 5. Cross-file resolution (Legacy fallback) ---
        resolved_legacy_count = self._resolve_cross_file(results)

        symbols_total = sum(len(r.symbols) for r in results)
        rels_total = sum(len(r.relationships) for r in results)

        return IndexStats(
            files_discovered=total,
            files_analyzed=len(results),
            symbols_extracted=symbols_total,
            relationships_extracted=rels_total,
            relationships_resolved=resolved_legacy_count + resolved_facts_count,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Resolution Pipeline
    # ------------------------------------------------------------------

    def _run_resolvers(self) -> int:
        """
        Run semantic resolution passes on raw facts, and materialize the results
        into the main relationships table.
        """
        # First, run ImportResolver on IMPORT facts
        import_facts = self._store.get_relationship_facts(
            fact_type=FactType.IMPORT, status=FactStatus.PENDING
        )
        import_resolver = ImportResolver()
        total_resolved = import_resolver.run(self._store, import_facts)

        # Then, run CALL resolvers
        call_facts = self._store.get_relationship_facts(
            fact_type=FactType.CALL, status=FactStatus.PENDING
        )

        resolvers = [
            NoiseFilterCallResolver(),
            InFileCallResolver(),
            ClassMemberCallResolver(),
            ConstructorCallResolver(),
            CrossFileCallResolver(),
        ]
        for resolver in resolvers:
            count = resolver.run(self._store, call_facts)
            total_resolved += count

        # Materialize resolved / probable CALL facts into relationships
        # We need to fetch the updated facts since resolver.run mutated them in memory but let's be safe
        updated_call_facts = self._store.get_relationship_facts(fact_type=FactType.CALL)
        
        new_relationships = []
        for fact in updated_call_facts:
            if fact.status in (FactStatus.RESOLVED, FactStatus.PROBABLE) and fact.resolved_target_id:
                rel = Relationship(
                    source_id=fact.source_symbol_id,
                    target_id=fact.resolved_target_id,
                    type=RelationshipType.CALLS,
                    source_location=fact.source_location,
                    resolution=(
                        ResolutionStatus.RESOLVED 
                        if fact.status == FactStatus.RESOLVED 
                        else ResolutionStatus.PROBABLE
                    ),
                    metadata={
                        "fact_id": fact.id,
                        "call_text": fact.raw_text,
                        "callee_name": fact.simple_name,
                        "resolver": fact.resolver_name,
                    },
                )
                new_relationships.append(rel)

        if new_relationships:
            self._store.store_relationships(new_relationships)

        return total_resolved

    # ------------------------------------------------------------------
    # Cross-file resolution (Legacy)
    # ------------------------------------------------------------------

    def _resolve_cross_file(self, results: list[AnalysisResult]) -> int:
        """
        Fix unresolved relationships by matching target_name metadata
        against all known symbols across the entire indexed corpus.

        Returns the number of newly resolved relationships.
        """
        # Build a lookup: simple name → [Symbol], qualified_name → Symbol
        name_to_syms: dict[str, list] = {}
        qname_to_sym: dict[str, object] = {}

        all_symbols = self._store.get_all_symbols()
        for sym in all_symbols:
            name_to_syms.setdefault(sym.name, []).append(sym)
            qname_to_sym[sym.qualified_name] = sym

        unresolved = self._store.get_all_unresolved_relationships()
        resolved_count = 0

        for rel in unresolved:
            target_name: str = rel.metadata.get("target_name", "")
            if not target_name:
                continue

            # Try exact qualified name first
            match = qname_to_sym.get(target_name)
            if match is None:
                # Try suffix: e.g. "UserService" in "myapp.services.UserService"
                suffix_matches = [
                    s for s in all_symbols
                    if s.qualified_name.endswith(f".{target_name}") or s.name == target_name
                ]
                if len(suffix_matches) == 1:
                    match = suffix_matches[0]
                elif len(suffix_matches) > 1:
                    # Multiple matches — pick the most specific (shortest qualified name)
                    match = min(suffix_matches, key=lambda s: len(s.qualified_name))

            if match:
                resolution = (
                    ResolutionStatus.RESOLVED
                    if qname_to_sym.get(target_name) == match
                    else ResolutionStatus.PROBABLE
                )
                self._store.update_relationship(rel.id, match.id, resolution)  # type: ignore[arg-type]
                resolved_count += 1

        return resolved_count


# ---------------------------------------------------------------------------
# Stats dataclass
# ---------------------------------------------------------------------------


class IndexStats:
    """Summary statistics from an index run."""

    def __init__(
        self,
        files_discovered: int,
        files_analyzed: int,
        symbols_extracted: int,
        relationships_extracted: int,
        relationships_resolved: int,
        errors: list[str],
    ) -> None:
        self.files_discovered = files_discovered
        self.files_analyzed = files_analyzed
        self.symbols_extracted = symbols_extracted
        self.relationships_extracted = relationships_extracted
        self.relationships_resolved = relationships_resolved
        self.errors = errors

    def __repr__(self) -> str:
        return (
            f"IndexStats(files={self.files_analyzed}/{self.files_discovered}, "
            f"symbols={self.symbols_extracted}, "
            f"relationships={self.relationships_extracted}, "
            f"resolved={self.relationships_resolved}, "
            f"errors={len(self.errors)})"
        )
