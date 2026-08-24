"""Tests for SQLite storage layer."""

import tempfile
from pathlib import Path

import pytest

from node_walk.ir.models import (
    AnalysisResult,
    FileInfo,
    Language,
    Relationship,
    RelationshipFact,
    RelationshipType,
    ResolutionStatus,
    SourceLocation,
    Symbol,
    SymbolKind,
)
from node_walk.ir.enums import FactStatus, FactType
from node_walk.storage.repository import SQLiteGraphStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test.db"
    s = SQLiteGraphStore(db)
    yield s
    s.close()


def _make_file(path="/tmp/test.py") -> FileInfo:
    return FileInfo(path=path, language=Language.PYTHON, content_hash="abc123")


def _make_symbol(file_id: str, name="foo", kind=SymbolKind.FUNCTION) -> Symbol:
    return Symbol(
        name=name,
        qualified_name=f"module.{name}",
        kind=kind,
        language=Language.PYTHON,
        file_id=file_id,
        start_line=1,
        end_line=5,
    )


class TestStoreRoundTrip:
    def test_file_stored_and_retrieved(self, store):
        f = _make_file()
        sym = _make_symbol(f.id)
        result = AnalysisResult(file=f, symbols=[sym], relationships=[])
        store.store_result(result)

        retrieved = store.get_file(f.id)
        assert retrieved is not None
        assert retrieved.path == f.path
        assert retrieved.language == Language.PYTHON

    def test_symbol_stored_and_retrieved(self, store):
        f = _make_file()
        sym = _make_symbol(f.id, name="my_func")
        store.store_result(AnalysisResult(file=f, symbols=[sym]))

        retrieved = store.get_symbol(sym.id)
        assert retrieved is not None
        assert retrieved.name == "my_func"
        assert retrieved.kind == SymbolKind.FUNCTION

    def test_relationship_stored(self, store):
        f = _make_file()
        sym_a = _make_symbol(f.id, name="caller")
        sym_b = _make_symbol(f.id, name="callee")
        rel = Relationship(
            source_id=sym_a.id,
            target_id=sym_b.id,
            type=RelationshipType.CALLS,
        )
        store.store_result(AnalysisResult(file=f, symbols=[sym_a, sym_b], relationships=[rel]))

        rels = store.get_relationships_from(sym_a.id, RelationshipType.CALLS)
        assert len(rels) == 1
        assert rels[0].target_id == sym_b.id

    def test_find_by_name(self, store):
        f = _make_file()
        sym = _make_symbol(f.id, name="create_user")
        store.store_result(AnalysisResult(file=f, symbols=[sym]))

        results = store.find_symbols_by_name("create_user", exact=True)
        assert any(s.name == "create_user" for s in results)

    def test_find_by_name_partial(self, store):
        f = _make_file()
        sym = _make_symbol(f.id, name="create_user")
        store.store_result(AnalysisResult(file=f, symbols=[sym]))

        results = store.find_symbols_by_name("create", exact=False)
        assert any(s.name == "create_user" for s in results)

    def test_stats_returns_counts(self, store):
        f = _make_file()
        sym = _make_symbol(f.id)
        store.store_result(AnalysisResult(file=f, symbols=[sym]))

        s = store.stats()
        assert s["files"] == 1
        assert s["symbols"] >= 1

    def test_clear_removes_all(self, store):
        f = _make_file()
        sym = _make_symbol(f.id)
        store.store_result(AnalysisResult(file=f, symbols=[sym]))
        store.clear()

        assert store.stats()["files"] == 0
        assert store.stats()["symbols"] == 0

    def test_update_relationship(self, store):
        f = _make_file()
        sym_a = _make_symbol(f.id, name="caller")
        sym_b = _make_symbol(f.id, name="callee")
        rel = Relationship(
            source_id=sym_a.id,
            target_id="",
            type=RelationshipType.CALLS,
            resolution=ResolutionStatus.UNRESOLVED,
        )
        store.store_result(AnalysisResult(file=f, symbols=[sym_a, sym_b], relationships=[rel]))

        store.update_relationship(rel.id, sym_b.id, ResolutionStatus.RESOLVED)
        updated = store.get_relationships_from(sym_a.id, RelationshipType.CALLS)
        assert updated[0].target_id == sym_b.id
        assert updated[0].resolution == ResolutionStatus.RESOLVED

    def test_relationship_fact_stored_and_retrieved(self, store):
        f = _make_file()
        sym = _make_symbol(f.id, name="caller")
        fact = RelationshipFact(
            file_id=f.id,
            source_symbol_id=sym.id,
            fact_type=FactType.CALL,
            raw_text="self.chat",
            simple_name="chat",
            receiver_text="self",
            source_location=SourceLocation(file_id=f.id, line=4, col=8),
            metadata={"call_kind": "attribute"},
        )

        store.store_result(AnalysisResult(file=f, symbols=[sym], relationship_facts=[fact]))

        facts = store.get_relationship_facts(FactType.CALL)
        assert len(facts) == 1
        assert facts[0].raw_text == "self.chat"
        assert facts[0].metadata["call_kind"] == "attribute"
        assert facts[0].status == FactStatus.PENDING

    def test_update_relationship_fact(self, store):
        f = _make_file()
        sym_a = _make_symbol(f.id, name="caller")
        sym_b = _make_symbol(f.id, name="callee")
        fact = RelationshipFact(
            file_id=f.id,
            source_symbol_id=sym_a.id,
            fact_type=FactType.CALL,
            raw_text="callee",
            simple_name="callee",
        )

        store.store_result(AnalysisResult(file=f, symbols=[sym_a, sym_b], relationship_facts=[fact]))
        store.update_relationship_fact(
            fact.id,
            status=FactStatus.RESOLVED,
            resolved_target_id=sym_b.id,
            resolver_name="unit-test",
            diagnostics={"reason": "exact simple-name match"},
        )

        facts = store.get_relationship_facts(FactType.CALL, FactStatus.RESOLVED)
        assert len(facts) == 1
        assert facts[0].resolved_target_id == sym_b.id
        assert facts[0].resolver_name == "unit-test"
        assert facts[0].diagnostics["reason"] == "exact simple-name match"
