"""Tests for Code IR models."""

import pytest
from pydantic import ValidationError

from node_walk.ir.models import (
    AnalysisResult,
    FileInfo,
    Language,
    Relationship,
    RelationshipType,
    ResolutionStatus,
    SourceLocation,
    Symbol,
    SymbolKind,
)


class TestFileInfo:
    def test_default_id_generated(self):
        f = FileInfo(path="/tmp/foo.py", language=Language.PYTHON)
        assert f.id
        assert len(f.id) == 36  # UUID4

    def test_immutable(self):
        f = FileInfo(path="/tmp/foo.py", language=Language.PYTHON)
        with pytest.raises(Exception):
            f.path = "/other"  # type: ignore[misc]


class TestSymbol:
    def test_basic_fields(self):
        sym = Symbol(
            name="create_user",
            qualified_name="myapp.services.UserService.create_user",
            kind=SymbolKind.METHOD,
            language=Language.PYTHON,
            file_id="file-1",
            start_line=10,
            end_line=20,
        )
        assert sym.name == "create_user"
        assert sym.kind == SymbolKind.METHOD
        assert sym.start_line == 10

    def test_default_signature_empty(self):
        sym = Symbol(
            name="x",
            qualified_name="mod.x",
            kind=SymbolKind.VARIABLE,
            language=Language.PYTHON,
            file_id="file-1",
            start_line=1,
            end_line=1,
        )
        assert sym.signature == ""

    def test_is_async_default_false(self):
        sym = Symbol(
            name="fn",
            qualified_name="mod.fn",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_id="file-1",
            start_line=1,
            end_line=1,
        )
        assert sym.is_async is False


class TestRelationship:
    def test_resolution_default_resolved(self):
        rel = Relationship(
            source_id="a",
            target_id="b",
            type=RelationshipType.CALLS,
        )
        assert rel.resolution == ResolutionStatus.RESOLVED

    def test_unresolved_empty_target(self):
        rel = Relationship(
            source_id="a",
            target_id="",
            type=RelationshipType.IMPORTS,
            resolution=ResolutionStatus.UNRESOLVED,
            metadata={"target_name": "os.path"},
        )
        assert rel.target_id == ""
        assert rel.metadata["target_name"] == "os.path"

    def test_with_source_location(self):
        loc = SourceLocation(file_id="f1", line=42, col=8)
        rel = Relationship(
            source_id="a",
            target_id="b",
            type=RelationshipType.CALLS,
            source_location=loc,
        )
        assert rel.source_location.line == 42


class TestAnalysisResult:
    def test_empty_result(self):
        f = FileInfo(path="/tmp/foo.py", language=Language.PYTHON)
        result = AnalysisResult(file=f)
        assert result.symbols == []
        assert result.relationships == []
