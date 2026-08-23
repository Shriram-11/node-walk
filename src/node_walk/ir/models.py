"""
Code IR data models — Pydantic v2 models for the semantic graph.

All models are immutable (frozen=True). Language analyzers produce
AnalysisResult objects; the storage and query layers consume them.

Enums live in node_walk.ir.enums — import from there if you only
need enum values without the Pydantic models.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from node_walk.ir.enums import (
    FactStatus,
    FactType,
    Language,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
)


class FileInfo(BaseModel):
    """Represents a source file discovered during indexing."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    path: str                            # absolute path on disk
    language: Language = Language.UNKNOWN
    content_hash: str = ""               # SHA-256 hex; empty until computed
    size_bytes: int = 0

    model_config = {"frozen": True}


class Symbol(BaseModel):
    """
    A named code entity (class, function, method, variable, …).

    Every symbol belongs to a file and may have a parent symbol
    (e.g., a method inside a class).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str                            # simple name: "createUser"
    qualified_name: str                  # fully-qualified: "pkg.module.Class.method"
    kind: SymbolKind
    language: Language = Language.PYTHON
    file_id: str                         # FK → FileInfo.id
    start_line: int                      # 1-indexed
    end_line: int                        # 1-indexed, inclusive
    signature: str = ""                  # e.g. "(self, user: User) -> None"
    parent_id: str | None = None         # FK → Symbol.id of enclosing scope
    docstring: str = ""                  # first docstring if present
    is_async: bool = False

    model_config = {"frozen": True}


class SourceLocation(BaseModel):
    """A precise location in source code (for relationship call sites)."""

    file_id: str
    line: int       # 1-indexed
    col: int = 0    # 0-indexed column

    model_config = {"frozen": True}


class Relationship(BaseModel):
    """
    A directed edge between two symbols in the semantic graph.

    source → [type] → target

    The ``resolution`` field captures how confident the analyzer was
    when resolving the target symbol. UNRESOLVED relationships are
    stored with target_id = "" so callers can filter them out.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str                               # FK → Symbol.id
    target_id: str                               # FK → Symbol.id; "" if unresolved
    type: RelationshipType
    source_location: SourceLocation | None = None  # call-site / import-site
    resolution: ResolutionStatus = ResolutionStatus.RESOLVED
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


class RelationshipFact(BaseModel):
    """
    A raw semantic observation captured during extraction before final resolution.

    Unlike Relationship, a fact represents "what we saw in source" rather than the
    final graph edge we concluded from that source.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_id: str
    source_symbol_id: str
    fact_type: FactType
    raw_text: str
    simple_name: str = ""
    receiver_text: str = ""
    qualified_hint: str = ""
    source_location: SourceLocation | None = None
    scope_symbol_id: str | None = None
    status: FactStatus = FactStatus.PENDING
    resolved_target_id: str = ""
    resolver_name: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


class AnalysisResult(BaseModel):
    """
    Complete output from analyzing a single file.

    Produced by a LanguageAnalyzer and consumed by the storage layer.
    """

    file: FileInfo
    symbols: list[Symbol] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    relationship_facts: list[RelationshipFact] = Field(default_factory=list)

    model_config = {"frozen": True}
