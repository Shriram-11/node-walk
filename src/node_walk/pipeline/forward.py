"""
Forward pass: file discovery, diffing, change invalidation, and symbol/fact extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from node_walk.analysis.base import FileDiscovery, LanguageAnalyzer
from node_walk.ir.enums import Language
from node_walk.ir.models import AnalysisResult, FileInfo
from node_walk.pipeline.diff import FileDiff
from node_walk.pipeline.invalidator import Invalidator
from node_walk.storage.base import GraphStore


ProgressCallback = Callable[[str, int, int], None]


@dataclass
class ForwardResult:
    files_discovered: int = 0
    files_analyzed: int = 0
    files_unchanged: int = 0
    files_removed: int = 0
    files_changed: int = 0
    files_added: int = 0
    symbols_extracted: int = 0
    facts_extracted: int = 0
    relationships_extracted: int = 0
    errors: list[str] = field(default_factory=list)
    affected_file_ids: set[str] = field(default_factory=set)


class ForwardPass:
    """
    Discovers files, detects changes (in incremental mode),
    invalidates stale data, and extracts fresh symbols + facts.
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

        # 1. Discover files on disk
        discovery = FileDiscovery(root_path, include_languages=list(lang_map.keys()))
        file_pairs = discovery.discover()
        total = len(file_pairs)

        files_unchanged = 0
        files_removed = 0
        files_changed = 0
        files_added = 0

        pairs_to_analyze = file_pairs

        if mode == "full":
            store.clear()
            files_added = total
        else:
            # Incremental mode: diff against stored files
            diff = FileDiff()
            diff_result = diff.diff([pair[0] for pair in file_pairs], store)

            files_unchanged = len(diff_result.unchanged)
            files_removed = len(diff_result.removed)
            files_changed = len(diff_result.changed)
            files_added = len(diff_result.added)

            # Invalidate removed files
            to_invalidate: list[str] = list(diff_result.removed)

            # For changed files, find existing stored file ID to invalidate
            for changed_info in diff_result.changed:
                stored_file = store.get_file_by_path(changed_info.path)
                if stored_file:
                    to_invalidate.append(stored_file.id)

            if to_invalidate:
                invalidator = Invalidator()
                invalidator.invalidate_files(store, to_invalidate)

            # Filter pairs_to_analyze to only added + changed
            active_paths = {f.path for f in diff_result.added} | {f.path for f in diff_result.changed}
            pairs_to_analyze = [
                (info, src) for (info, src) in file_pairs
                if info.path in active_paths
            ]

        # 2. Analyze files to be indexed
        results: list[AnalysisResult] = []
        errors: list[str] = []
        affected_file_ids: set[str] = set()

        for i, (file_info, source) in enumerate(pairs_to_analyze, start=1):
            if progress:
                progress(file_info.path, i, len(pairs_to_analyze))

            analyzer = lang_map.get(file_info.language)
            if not analyzer:
                continue

            try:
                result = analyzer.analyze(file_info, source)
                results.append(result)
                affected_file_ids.add(file_info.id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{file_info.path}: {exc}")

        # 3. Store fresh analysis results
        if results:
            store.store_results(results)

        symbols_total = sum(len(r.symbols) for r in results)
        facts_total = sum(len(r.relationship_facts) for r in results)
        rels_total = sum(len(r.relationships) for r in results)

        return ForwardResult(
            files_discovered=total,
            files_analyzed=len(results),
            files_unchanged=files_unchanged,
            files_removed=files_removed,
            files_changed=files_changed,
            files_added=files_added,
            symbols_extracted=symbols_total,
            facts_extracted=facts_total,
            relationships_extracted=rels_total,
            errors=errors,
            affected_file_ids=affected_file_ids,
        )
