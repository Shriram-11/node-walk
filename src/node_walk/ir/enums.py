"""
Code IR enums — all enumerated types used across the graph model.

Kept separate from data models so enum values can be imported
without pulling in Pydantic (e.g. in lightweight scripts or tests).
"""

from __future__ import annotations

from enum import StrEnum


class SymbolKind(StrEnum):
    """Canonical kinds of code symbols across all supported languages."""

    FILE = "FILE"
    MODULE = "MODULE"
    PACKAGE = "PACKAGE"
    CLASS = "CLASS"
    INTERFACE = "INTERFACE"   # ABCs and typing.Protocol in Python
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    VARIABLE = "VARIABLE"
    CONSTANT = "CONSTANT"
    FIELD = "FIELD"           # class-level attribute / instance variable


class RelationshipType(StrEnum):
    """Canonical relationship types between symbols."""

    # Structural
    CONTAINS = "CONTAINS"       # parent → child (module → class, class → method, …)

    # Dependencies
    IMPORTS = "IMPORTS"         # file/module → imported symbol or module

    # Call graph
    CALLS = "CALLS"             # caller → callee

    # References
    REFERENCES = "REFERENCES"   # any name usage that isn't a call

    # Inheritance / interface
    EXTENDS = "EXTENDS"         # subclass → base class
    IMPLEMENTS = "IMPLEMENTS"   # concrete class → ABC / Protocol
    OVERRIDES = "OVERRIDES"     # overriding method → overridden method


class Language(StrEnum):
    """Supported programming languages."""

    PYTHON = "python"
    UNKNOWN = "unknown"


class ResolutionStatus(StrEnum):
    """How confidently a relationship was resolved."""

    RESOLVED = "resolved"       # statically certain
    PROBABLE = "probable"       # high-confidence heuristic
    UNRESOLVED = "unresolved"   # could not determine target
