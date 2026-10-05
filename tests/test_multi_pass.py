import pytest
from pathlib import Path

from node_walk.ir.enums import FactStatus, FactType, Language, RelationshipType, SymbolKind
from node_walk.ir.models import FileInfo, RelationshipFact, Symbol
from node_walk.pipeline.backward import BackwardPass
from node_walk.resolution.base import FactResolver, ResolutionResult, RunResult
from node_walk.storage.base import GraphStore
from node_walk.storage.sqlite_store import SQLiteGraphStore


def test_run_result_tracking():
    res = RunResult()
    assert res.progress_made == 0
    assert res.total_decided == 0

    res.record(FactStatus.RESOLVED)
    res.record(FactStatus.PROBABLE)
    res.record(FactStatus.IGNORED)
    res.record(FactStatus.UNRESOLVED)

    assert res.resolved == 1
    assert res.probable == 1
    assert res.ignored == 1
    assert res.unresolved == 1
    assert res.progress_made == 2
    assert res.total_decided == 4
    assert int(res) == 2


def _setup_file_and_symbols(store: SQLiteGraphStore, file_id: str = "f1", symbol_ids: list[str] | None = None) -> None:
    from node_walk.ir.models import AnalysisResult
    f = FileInfo(id=file_id, path=f"/test/{file_id}.py", language=Language.PYTHON, content_hash="abc", size_bytes=10)
    syms = [
        Symbol(
            id=sid,
            name=sid,
            qualified_name=f"mod.{sid}",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_id=file_id,
            start_line=1,
            end_line=5,
        )
        for sid in (symbol_ids or ["s1"])
    ]
    store.store_result(AnalysisResult(file=f, symbols=syms))


def test_sqlite_store_reset_unresolved_facts_to_pending(tmp_path):
    db_path = tmp_path / "test.db"
    store = SQLiteGraphStore(db_path)
    try:
        _setup_file_and_symbols(store, "f1", ["s1", "s2"])
        fact1 = RelationshipFact(
            file_id="f1",
            source_symbol_id="s1",
            fact_type=FactType.CALL,
            raw_text="foo()",
            simple_name="foo",
            status=FactStatus.UNRESOLVED,
        )
        fact2 = RelationshipFact(
            file_id="f1",
            source_symbol_id="s1",
            fact_type=FactType.CALL,
            raw_text="bar()",
            simple_name="bar",
            status=FactStatus.RESOLVED,
            resolved_target_id="s2",
        )
        store.store_facts([fact1, fact2])

        assert store.get_pending_fact_count() == 0

        reset_count = store.reset_unresolved_facts_to_pending()
        assert reset_count == 1
        assert store.get_pending_fact_count() == 1

        pending_facts = store.get_relationship_facts(status=FactStatus.PENDING)
        assert len(pending_facts) == 1
        assert pending_facts[0].id == fact1.id
        assert pending_facts[0].status == FactStatus.PENDING

        resolved_facts = store.get_relationship_facts(status=FactStatus.RESOLVED)
        assert len(resolved_facts) == 1
        assert resolved_facts[0].id == fact2.id
    finally:
        store.close()


class DependentOnLaterResolver(FactResolver):
    @property
    def name(self) -> str:
        return "DependentOnLaterResolver"

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.simple_name == "needs_b":
            b_facts = store.get_relationship_facts(status=FactStatus.RESOLVED)
            if any(f.resolved_target_id == "target_b" for f in b_facts):
                return ResolutionResult(
                    status=FactStatus.RESOLVED,
                    resolved_target_id="target_a",
                )
            return ResolutionResult(status=FactStatus.UNRESOLVED)
        return None


class ProducesBResolver(FactResolver):
    @property
    def name(self) -> str:
        return "ProducesBResolver"

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.simple_name == "provides_b":
            return ResolutionResult(
                status=FactStatus.RESOLVED,
                resolved_target_id="target_b",
            )
        return None


def test_backward_pass_multi_iteration_convergence(tmp_path):
    db_path = tmp_path / "test.db"
    store = SQLiteGraphStore(db_path)
    try:
        _setup_file_and_symbols(store, "f1", ["s1"])
        fact_a = RelationshipFact(
            file_id="f1",
            source_symbol_id="s1",
            fact_type=FactType.CALL,
            raw_text="needs_b()",
            simple_name="needs_b",
            status=FactStatus.PENDING,
        )
        fact_b = RelationshipFact(
            file_id="f1",
            source_symbol_id="s1",
            fact_type=FactType.CALL,
            raw_text="provides_b()",
            simple_name="provides_b",
            status=FactStatus.PENDING,
        )
        store.store_facts([fact_a, fact_b])

        resolver_sequence = [
            (DependentOnLaterResolver, FactType.CALL),
            (ProducesBResolver, FactType.CALL),
        ]
        backward = BackwardPass(resolver_sequence=resolver_sequence, max_iterations=5)
        result = backward.run(store)

        assert result.converged is True
        assert result.total_resolved == 2
        assert result.total_pending == 0
        assert len(result.iteration_stats) == 3  # Iteration 1 (resolved B), Iteration 2 (resolved A), Iteration 3 (converged 0)
    finally:
        store.close()


def test_backward_pass_max_iterations_cap(tmp_path):
    db_path = tmp_path / "test.db"
    store = SQLiteGraphStore(db_path)
    try:
        _setup_file_and_symbols(store, "f1", ["s1"])
        fact = RelationshipFact(
            file_id="f1",
            source_symbol_id="s1",
            fact_type=FactType.CALL,
            raw_text="unresolvable()",
            simple_name="unresolvable",
            status=FactStatus.PENDING,
        )
        store.store_facts([fact])

        class AlwaysUnresolvedResolver(FactResolver):
            @property
            def name(self) -> str:
                return "AlwaysUnresolvedResolver"

            def resolve(self, store, fact):
                return ResolutionResult(status=FactStatus.UNRESOLVED)

        backward = BackwardPass(
            resolver_sequence=[(AlwaysUnresolvedResolver, FactType.CALL)],
            max_iterations=3,
        )
        result = backward.run(store)

        assert result.converged is True  # Converged because 0 progress was made in iteration 1
        assert result.iterations_run == 1
    finally:
        store.close()
