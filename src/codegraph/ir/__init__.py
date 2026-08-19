"""
codegraph.ir — Code Intermediate Representation.

Re-exports everything from enums and models so consumers can do:

    from codegraph.ir import Symbol, SymbolKind, Relationship
    # or
    from codegraph.ir.models import Symbol
    # or
    from codegraph.ir.enums import SymbolKind
"""

from codegraph.ir.enums import (
    Language,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
)
from codegraph.ir.models import (
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
