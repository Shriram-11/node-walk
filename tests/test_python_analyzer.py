"""Tests for the Python analyzer — symbol extraction and relationships."""

from pathlib import Path

import pytest

from node_walk.analysis.python_analyzer import PythonAnalyzer
from node_walk.ir.models import (
    FileInfo,
    FactType,
    Language,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "simple_project"


def _analyze(filename: str):
    path = FIXTURE_DIR / filename
    source = path.read_text(encoding="utf-8")
    file_info = FileInfo(path=str(path), language=Language.PYTHON)
    analyzer = PythonAnalyzer()
    return analyzer.analyze(file_info, source)


class TestSymbolExtraction:
    def setup_method(self):
        self.result = _analyze("services.py")
        self.sym_names = {s.name for s in self.result.symbols}
        self.sym_kinds = {s.name: s.kind for s in self.result.symbols}

    def test_file_symbol_present(self):
        assert "services.py" in self.sym_names

    def test_class_extracted(self):
        assert "UserService" in self.sym_names
        assert self.sym_kinds["UserService"] == SymbolKind.CLASS

    def test_method_extracted(self):
        assert "create_user" in self.sym_names
        assert self.sym_kinds["create_user"] == SymbolKind.METHOD

    def test_function_extracted(self):
        assert "send_email" in self.sym_names
        assert self.sym_kinds["send_email"] == SymbolKind.FUNCTION

    def test_constant_extracted(self):
        assert "MAX_RETRIES" in self.sym_names
        assert self.sym_kinds["MAX_RETRIES"] == SymbolKind.CONSTANT

    def test_line_numbers(self):
        user_service = next(s for s in self.result.symbols if s.name == "UserService")
        assert user_service.start_line >= 1
        assert user_service.end_line > user_service.start_line

    def test_parent_id_set_for_method(self):
        class_sym = next(s for s in self.result.symbols if s.name == "UserService")
        method_sym = next(s for s in self.result.symbols if s.name == "create_user")
        assert method_sym.parent_id == class_sym.id

    def test_docstring_extracted(self):
        svc = next(s for s in self.result.symbols if s.name == "UserService")
        assert "user" in svc.docstring.lower()


class TestRelationshipExtraction:
    def setup_method(self):
        self.result = _analyze("services.py")
        self.rels = self.result.relationships
        self.facts = self.result.relationship_facts

    def test_contains_relationships_present(self):
        contains = [r for r in self.rels if r.type == RelationshipType.CONTAINS]
        assert len(contains) > 0

    def test_call_facts_present(self):
        call_facts = [f for f in self.facts if f.fact_type == FactType.CALL]
        assert len(call_facts) > 0

    def test_call_facts_capture_receiver_text(self):
        call_facts = [f for f in self.facts if f.fact_type == FactType.CALL]
        assert any(f.raw_text == "self._notify" and f.receiver_text == "self" for f in call_facts)


class TestAbcDetection:
    def test_abc_class_detected_as_interface(self):
        fixture = Path(__file__).parent / "fixtures" / "nested_project" / "base.py"
        source = fixture.read_text(encoding="utf-8")
        file_info = FileInfo(path=str(fixture), language=Language.PYTHON)
        result = PythonAnalyzer().analyze(file_info, source)
        repo = next((s for s in result.symbols if s.name == "Repository"), None)
        assert repo is not None
        assert repo.kind == SymbolKind.INTERFACE
