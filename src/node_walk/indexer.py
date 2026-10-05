"""
Indexer — thin orchestrator for file discovery, analysis, multi-pass resolution, and materialization.

Usage:
    from node_walk.indexer import Indexer
    from node_walk.storage.sqlite_store import SQLiteGraphStore

    store = SQLiteGraphStore(".node_walk/graph.db")
    indexer = Indexer(store)
    indexer.index("./my_repo")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from node_walk.analysis.base import LanguageAnalyzer
from node_walk.analysis.python import PythonAnalyzer
from node_walk.pipeline.backward import BackwardPass, BackwardResult
from node_walk.pipeline.forward import ForwardPass, ForwardResult
from node_walk.pipeline.materializer import Materializer, MaterializationResult
from node_walk.storage.base import GraphStore


@dataclass
class IndexStats:
    """Summary statistics from an index run."""

    forward: ForwardResult
    backward: BackwardResult
    materialization: MaterializationResult

    @property
    def files_discovered(self) -> int:
        return self.forward.files_discovered

    @property
    def files_analyzed(self) -> int:
        return self.forward.files_analyzed

    @property
    def symbols_extracted(self) -> int:
        return self.forward.symbols_extracted

    @property
    def relationships_extracted(self) -> int:
        return self.forward.relationships_extracted

    @property
    def relationships_resolved(self) -> int:
        return self.backward.total_resolved + self.backward.total_probable

    @property
    def errors(self) -> list[str]:
        return self.forward.errors

    def __repr__(self) -> str:
        return (
            f"IndexStats(files={self.files_analyzed}/{self.files_discovered}, "
            f"symbols={self.symbols_extracted}, "
            f"relationships={self.relationships_extracted}, "
            f"resolved={self.relationships_resolved}, "
            f"errors={len(self.errors)})"
        )


class Indexer:
    """
    Orchestrates the full indexing pipeline:
      ForwardPass -> BackwardPass -> Materializer
    """

    def __init__(
        self,
        store: GraphStore,
        analyzers: list[LanguageAnalyzer] | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self._store = store
        self._analyzers: list[LanguageAnalyzer] = analyzers or [PythonAnalyzer()]
        self._progress = progress_callback

    def index(
        self,
        root: str | Path,
        clear: bool = True,
        mode: Literal["full", "incremental"] = "full",
        max_iterations: int = 5,
    ) -> IndexStats:
        """
        Index *root* directory.

        In full mode (or clear=True), wipes existing graph before indexing.
        In incremental mode, only modified/added files are analyzed.
        """
        effective_mode: Literal["full", "incremental"] = "full" if clear else mode

        # 1. Forward Pass
        forward = ForwardPass()
        fwd_result = forward.run(
            root,
            self._store,
            self._analyzers,
            mode=effective_mode,
            progress=self._progress,
        )

        # 2. Backward Pass (multi-pass resolution)
        backward = BackwardPass(max_iterations=max_iterations)
        bwd_result = backward.run(self._store)

        # 3. Materialization Pass
        materializer = Materializer()
        mat_result = materializer.run(self._store)

        return IndexStats(
            forward=fwd_result,
            backward=bwd_result,
            materialization=mat_result,
        )
