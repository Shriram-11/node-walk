"""Tests for the query engine."""

import pytest
from pathlib import Path

from node_walk.analysis.python_analyzer import PythonAnalyzer
from node_walk.indexer import Indexer
from node_walk.ir.models import (
    AnalysisResult,
    FileInfo,
    Language,
    Relationship,
    RelationshipType,
    ResolutionStatus,
    Symbol,
    SymbolKind,
)
from node_walk.query.engine import QueryEngine
from node_walk.storage.repository import SQLiteGraphStore

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "simple_project"


@pytest.fixture
def engine(tmp_path):
    """Engine pre-loaded with the simple_project fixture."""
    db = tmp_path / "test.db"
    store = SQLiteGraphStore(db)
    indexer = Indexer(store)
    indexer.index(FIXTURE_DIR)
    eng = QueryEngine(store)
    yield eng
    store.close()


class TestFindSymbol:
    def test_finds_exact_name(self, engine):
        results = engine.find_symbol("UserService")
        assert any(m.symbol.name == "UserService" for m in results)

    def test_finds_partial_name(self, engine):
        results = engine.find_symbol("create")
        assert any("create" in m.symbol.name for m in results)

    def test_returns_empty_for_nonexistent(self, engine):
        results = engine.find_symbol("__absolutely_not_a_symbol__")
        assert results == []

    def test_results_sorted_by_score(self, engine):
        results = engine.find_symbol("UserService")
        if len(results) > 1:
            scores = [m.score for m in results]
            assert scores == sorted(scores, reverse=True)


class TestDefinition:
    def test_get_definition_returns_symbol(self, engine):
        matches = engine.find_symbol("create_user")
        assert matches
        sym_id = matches[0].symbol.id
        sym = engine.get_definition(sym_id)
        assert sym is not None
        assert sym.name == "create_user"

    def test_definition_has_line_numbers(self, engine):
        matches = engine.find_symbol("UserService")
        sym = engine.get_definition(matches[0].symbol.id)
        assert sym.start_line >= 1
        assert sym.end_line >= sym.start_line


class TestCallersCallees:
    def test_callers_found(self, engine):
        # bootstrap calls UserService constructor — find something that gets called
        matches = engine.find_symbol("bootstrap")
        if matches:
            sym_id = matches[0].symbol.id
            callees = engine.get_callees(sym_id)
            assert isinstance(callees, list)

    def test_callee_list_is_typed(self, engine):
        matches = engine.find_symbol("create_user")
        if matches:
            sym_id = matches[0].symbol.id
            callees = engine.get_callees(sym_id)
            for sym, rel in callees:
                assert hasattr(sym, "name")
                assert rel.type == RelationshipType.CALLS


class TestSourceRetrieval:
    def test_get_source_returns_range(self, engine):
        matches = engine.find_symbol("send_email")
        assert matches
        src = engine.get_source(matches[0].symbol.id)
        assert src is not None
        assert src.start_line >= 1
        assert len(src.lines) > 0

    def test_source_contains_def_keyword(self, engine):
        matches = engine.find_symbol("send_email")
        src = engine.get_source(matches[0].symbol.id)
        assert any("def" in line for line in src.lines)


class TestWalk:
    def test_walk_returns_results(self, engine):
        matches = engine.find_symbol("UserService")
        if matches:
            results = engine.walk(matches[0].symbol.id, depth=2)
            assert isinstance(results, list)

    def test_walk_depth_respected(self, engine):
        matches = engine.find_symbol("UserService")
        if matches:
            results = engine.walk(matches[0].symbol.id, depth=1)
            for r in results:
                assert r.depth <= 1


class TestStats:
    def test_stats_has_expected_keys(self, engine):
        s = engine.stats()
        assert "files" in s
        assert "symbols" in s
        assert "relationships" in s

    def test_stats_nonzero(self, engine):
        s = engine.stats()
        assert s["files"] >= 1
        assert s["symbols"] >= 5
