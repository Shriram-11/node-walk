"""
node_walk.ir — Code Intermediate Representation.

Re-exports everything from enums and models so consumers can do:

    from node_walk.ir import Symbol, SymbolKind, Relationship
    # or
    from node_walk.ir.models import Symbol
    # or
    from node_walk.ir.enums import SymbolKind
"""

from node_walk.ir.enums import (
    Language,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
)
from node_walk.ir.models import (
    AnalysisResult,
    FileInfo,
    Relationship,
    SourceLocation,
    Symbol,
)

__all__ = [
    # enums
    "Language",
    "RelationshipType",
    "ResolutionStatus",
    "SymbolKind",
    # models
    "AnalysisResult",
    "FileInfo",
    "Relationship",
    "SourceLocation",
    "Symbol",
]
