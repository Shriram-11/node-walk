import pytest
from pathlib import Path

from node_walk.analysis.base import FileDiscovery
from node_walk.analysis.python import PythonAnalyzer
from node_walk.indexer import Indexer
from node_walk.ir.enums import FactStatus, FactType, Language, RelationshipType, SymbolKind
from node_walk.ir.models import AnalysisResult, FileInfo, Relationship, RelationshipFact, Symbol
from node_walk.pipeline.diff import FileDiff
from node_walk.pipeline.invalidator import Invalidator
from node_walk.storage.sqlite_store import SQLiteGraphStore


def test_file_diff_detects_changes(tmp_path):
    db_path = tmp_path / "test.db"
    store = SQLiteGraphStore(db_path)
    try:
        f1 = FileInfo(id="f1", path="/repo/unchanged.py", language=Language.PYTHON, content_hash="hash_a", size_bytes=10)
        f2 = FileInfo(id="f2", path="/repo/changed.py", language=Language.PYTHON, content_hash="hash_b_old", size_bytes=20)
        f3 = FileInfo(id="f3", path="/repo/removed.py", language=Language.PYTHON, content_hash="hash_c", size_bytes=30)
        store.store_results([
            AnalysisResult(file=f1),
            AnalysisResult(file=f2),
            AnalysisResult(file=f3),
        ])

        discovered = [
            FileInfo(id="d1", path="/repo/unchanged.py", language=Language.PYTHON, content_hash="hash_a", size_bytes=10),
            FileInfo(id="d2", path="/repo/changed.py", language=Language.PYTHON, content_hash="hash_b_new", size_bytes=25),
            FileInfo(id="d4", path="/repo/added.py", language=Language.PYTHON, content_hash="hash_d", size_bytes=15),
        ]

        diff = FileDiff().diff(discovered, store)
        assert diff.has_changes is True
        assert len(diff.unchanged) == 1
        assert diff.unchanged[0] == "f1"

        assert len(diff.changed) == 1
        assert diff.changed[0].path == "/repo/changed.py"
        assert diff.changed[0].content_hash == "hash_b_new"

        assert len(diff.added) == 1
        assert diff.added[0].path == "/repo/added.py"

        assert len(diff.removed) == 1
        assert diff.removed[0] == "f3"
    finally:
        store.close()


def test_invalidator_deletes_owned_data_and_cascades(tmp_path):
    db_path = tmp_path / "test.db"
    store = SQLiteGraphStore(db_path)
    try:
        f_a = FileInfo(id="fa", path="/repo/a.py", language=Language.PYTHON, content_hash="ha", size_bytes=10)
        f_b = FileInfo(id="fb", path="/repo/b.py", language=Language.PYTHON, content_hash="hb", size_bytes=10)

        sym_a = Symbol(
            id="sa",
            name="service_func",
            qualified_name="a.service_func",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_id="fa",
            start_line=1,
            end_line=5,
        )
        sym_b = Symbol(
            id="sb",
            name="caller_func",
            qualified_name="b.caller_func",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_id="fb",
            start_line=1,
            end_line=5,
        )

        # Fact in b calling a
        fact_b = RelationshipFact(
            id="fact_b",
            file_id="fb",
            source_symbol_id="sb",
            fact_type=FactType.CALL,
            raw_text="service_func()",
            simple_name="service_func",
            status=FactStatus.RESOLVED,
            resolved_target_id="sa",
        )

        store.store_results([
            AnalysisResult(file=f_a, symbols=[sym_a]),
            AnalysisResult(file=f_b, symbols=[sym_b], relationship_facts=[fact_b]),
        ])

        # Invalidate file A
        invalidator = Invalidator()
        stats = invalidator.invalidate_files(store, ["fa"])

        assert stats.files_invalidated == 1
        assert stats.symbols_deleted == 1
        assert stats.facts_reset == 1

        # Check that file A and symbol A are gone
        assert store.get_file("fa") is None
        assert store.get_symbol("sa") is None
        assert len(store.get_symbols_by_file("fa")) == 0

        # Check that fact in file B was cascaded to PENDING
        b_facts = store.get_relationship_facts(status=FactStatus.PENDING)
        assert len(b_facts) == 1
        assert b_facts[0].id == "fact_b"
        assert b_facts[0].resolved_target_id == ""
    finally:
        store.close()


def test_full_index_then_incremental_workflow(tmp_path):
    project = tmp_path / "my_project"
    project.mkdir()

    service_file = project / "service.py"
    service_file.write_text(
        """
class Greeter:
    def greet(self, name: str) -> str:
        return f"Hello, {name}"
""".strip(),
        encoding="utf-8",
    )

    main_file = project / "main.py"
    main_file.write_text(
        """
from service import Greeter

def main():
    g = Greeter()
    return g.greet("world")
""".strip(),
        encoding="utf-8",
    )

    db_path = tmp_path / "graph.db"
    store = SQLiteGraphStore(db_path)
    try:
        indexer = Indexer(store)

        # 1. Full Index
        stats1 = indexer.index(project, mode="full")
        assert stats1.files_analyzed == 2
        assert stats1.forward.files_discovered == 2
        assert stats1.relationships_resolved >= 1

        greet_sym = next(s for s in store.get_all_symbols() if s.name == "greet")
        main_sym = next(s for s in store.get_all_symbols() if s.name == "main")
        calls = store.get_relationships_from(main_sym.id, RelationshipType.CALLS)
        assert any(c.target_id == greet_sym.id for c in calls)

        # 2. Incremental Index with No Changes
        stats2 = indexer.index(project, mode="incremental")
        assert stats2.files_analyzed == 0
        assert stats2.forward.files_unchanged == 2
        assert stats2.forward.files_changed == 0

        # Verify graph is still intact
        calls2 = store.get_relationships_from(main_sym.id, RelationshipType.CALLS)
        assert any(c.target_id == greet_sym.id for c in calls2)

        # 3. Modify service.py (change method name to greet_user)
        service_file.write_text(
            """
class Greeter:
    def greet_user(self, name: str) -> str:
        return f"Hello, {name}"
""".strip(),
            encoding="utf-8",
        )

        stats3 = indexer.index(project, mode="incremental")
        assert stats3.files_analyzed == 1
        assert stats3.forward.files_changed == 1
        assert stats3.forward.files_unchanged == 1

        # Now main.py's call to greet() cannot resolve to greet_user()
        new_symbols = {s.name for s in store.get_all_symbols()}
        assert "greet_user" in new_symbols
        assert "greet" not in new_symbols

        # 4. Add helper.py
        helper_file = project / "helper.py"
        helper_file.write_text(
            """
def helper_func():
    return 42
""".strip(),
            encoding="utf-8",
        )

        stats4 = indexer.index(project, mode="incremental")
        assert stats4.files_analyzed == 1
        assert stats4.forward.files_added == 1
        assert stats4.forward.files_unchanged == 2

        assert any(s.name == "helper_func" for s in store.get_all_symbols())

        # 5. Delete helper.py
        helper_file.unlink()

        stats5 = indexer.index(project, mode="incremental")
        assert stats5.files_analyzed == 0
        assert stats5.forward.files_removed == 1
        assert not any(s.name == "helper_func" for s in store.get_all_symbols())
    finally:
        store.close()


def test_rename_file(tmp_path):
    project = tmp_path / "rename_project"
    project.mkdir()

    old_file = project / "old_module.py"
    old_file.write_text(
        """
def compute():
    return 100
""".strip(),
        encoding="utf-8",
    )

    db_path = tmp_path / "graph.db"
    store = SQLiteGraphStore(db_path)
    try:
        indexer = Indexer(store)
        indexer.index(project, mode="full")
        assert any(s.qualified_name == "old_module.compute" for s in store.get_all_symbols())

        # Rename old_module.py to new_module.py
        new_file = project / "new_module.py"
        new_file.write_text(old_file.read_text(encoding="utf-8"), encoding="utf-8")
        old_file.unlink()

        stats = indexer.index(project, mode="incremental")
        assert stats.forward.files_removed == 1
        assert stats.forward.files_added == 1

        syms = store.get_all_symbols()
        assert not any(s.qualified_name == "old_module.compute" for s in syms)
        assert any(s.qualified_name == "new_module.compute" for s in syms)
    finally:
        store.close()


def test_full_and_incremental_produce_same_graph(tmp_path):
    project = tmp_path / "cmp_project"
    project.mkdir()

    f_a = project / "a.py"
    f_a.write_text(
        """
class Calculator:
    def add(self, x, y):
        return x + y
""".strip(),
        encoding="utf-8",
    )

    f_b = project / "b.py"
    f_b.write_text(
        """
from a import Calculator

def run():
    c = Calculator()
    return c.add(1, 2)
""".strip(),
        encoding="utf-8",
    )

    # First, run incremental workflow in store1
    store1 = SQLiteGraphStore(tmp_path / "graph1.db")
    Indexer(store1).index(project, mode="full")

    # Now modify a.py
    f_a.write_text(
        """
class Calculator:
    def add(self, x, y):
        return x + y

    def sub(self, x, y):
        return x - y
""".strip(),
        encoding="utf-8",
    )
    Indexer(store1).index(project, mode="incremental")

    # Now run clean full index on store2
    store2 = SQLiteGraphStore(tmp_path / "graph2.db")
    Indexer(store2).index(project, mode="full")

    try:
        # Compare symbols
        syms1 = {(s.name, s.qualified_name, s.kind.value) for s in store1.get_all_symbols()}
        syms2 = {(s.name, s.qualified_name, s.kind.value) for s in store2.get_all_symbols()}
        assert syms1 == syms2

        # Compare relationship structure: (source_qname, target_qname, type)
        def get_rel_tuples(store: SQLiteGraphStore):
            tuples = set()
            for s in store.get_all_symbols():
                for rel in store.get_relationships_from(s.id):
                    target_sym = store.get_symbol(rel.target_id)
                    target_qname = target_sym.qualified_name if target_sym else ""
                    tuples.add((s.qualified_name, target_qname, rel.type.value))
            return tuples

        assert get_rel_tuples(store1) == get_rel_tuples(store2)
    finally:
        store1.close()
        store2.close()
