"""
Forward pass: file discovery, source analysis, symbol/fact extraction, and initial storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from node_walk.analysis.base import FileDiscovery, LanguageAnalyzer
from node_walk.ir.enums import Language
from node_walk.ir.models import AnalysisResult
from node_walk.storage.base import GraphStore


ProgressCallback = Callable[[str, int, int], None]


@dataclass
class ForwardResult:
    files_discovered: int = 0
    files_analyzed: int = 0
    files_unchanged: int = 0
    files_removed: int = 0
    symbols_extracted: int = 0
    facts_extracted: int = 0
    relationships_extracted: int = 0
    errors: list[str] = field(default_factory=list)
    affected_file_ids: set[str] = field(default_factory=set)


class ForwardPass:
    """
    Discovers files, analyzes them using appropriate LanguageAnalyzers,
    and stores raw symbols, facts, and containment relationships.
    """

    def run(
        self,
        root: str | Path,
        store: GraphStore,
        analyzers: list[LanguageAnalyzer],
        mode: Literal["full", "incremental"] = "full",
        progress: ProgressCallback | None = None,
    ) -> ForwardResult:
        root_path = Path(root).resolve()

        # Build language -> analyzer map
        lang_map: dict[Language, LanguageAnalyzer] = {}
        for a in analyzers:
            for lang in a.supported_languages:
                lang_map[lang] = a

        if mode == "full":
            store.clear()

        # 1. Discover files
        discovery = FileDiscovery(root_path, include_languages=list(lang_map.keys()))
        file_pairs = discovery.discover()
        total = len(file_pairs)

        # 2. Analyze
        results: list[AnalysisResult] = []
        errors: list[str] = []
        affected_file_ids: set[str] = set()

        for i, (file_info, source) in enumerate(file_pairs, start=1):
            if progress:
                progress(file_info.path, i, total)

            analyzer = lang_map.get(file_info.language)
            if not analyzer:
                continue

            try:
                result = analyzer.analyze(file_info, source)
                results.append(result)
                affected_file_ids.add(file_info.id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{file_info.path}: {exc}")

        # 3. Store
        store.store_results(results)

        symbols_total = sum(len(r.symbols) for r in results)
        facts_total = sum(len(r.relationship_facts) for r in results)
        rels_total = sum(len(r.relationships) for r in results)

        return ForwardResult(
            files_discovered=total,
            files_analyzed=len(results),
            files_unchanged=0,
            files_removed=0,
            symbols_extracted=symbols_total,
            facts_extracted=facts_total,
            relationships_extracted=rels_total,
            errors=errors,
            affected_file_ids=affected_file_ids,
        )
