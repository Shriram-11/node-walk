"""Tests for SQLite storage layer."""

import tempfile
from pathlib import Path

import pytest

from codegraph.ir.models import (
    AnalysisResult,
    FileInfo,
    Language,
    Relationship,
    RelationshipType,
    ResolutionStatus,
    Symbol,
    SymbolKind,
)
from codegraph.storage.repository import SQLiteGraphStore


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
