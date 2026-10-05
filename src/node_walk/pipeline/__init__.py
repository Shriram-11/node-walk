"""
Pipeline orchestration layer for multi-pass indexing and incremental re-indexing.
"""

from __future__ import annotations

from node_walk.pipeline.forward import ForwardPass, ForwardResult
from node_walk.pipeline.backward import BackwardPass, BackwardResult, IterationStats
from node_walk.pipeline.materializer import Materializer, MaterializationResult
from node_walk.pipeline.diff import FileDiff, DiffResult
from node_walk.pipeline.invalidator import Invalidator, InvalidationStats

__all__ = [
    "ForwardPass",
    "ForwardResult",
    "BackwardPass",
    "BackwardResult",
    "IterationStats",
    "Materializer",
    "MaterializationResult",
    "FileDiff",
    "DiffResult",
    "Invalidator",
    "InvalidationStats",
]
