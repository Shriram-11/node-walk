import pytest

from node_walk.ir.enums import FactStatus, FactType, Language, SymbolKind
from node_walk.ir.models import AnalysisResult, FileInfo, RelationshipFact, Symbol
from node_walk.analysis.python import PythonAnalyzer
from node_walk.resolution.bindings import BindingResolver
from node_walk.storage.sqlite_store import SQLiteGraphStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "graph.db"
    store = SQLiteGraphStore(str(db_path))
    yield store
    store.close()


def test_binding_resolver_local_match(store):
    finfo = FileInfo(path="test.py", language=Language.PYTHON)
    
    target_sym = Symbol(
        name="TransactionService",
        qualified_name="TransactionService",
        kind=SymbolKind.CLASS,
        language=Language.PYTHON,
        file_id=finfo.id,
        start_line=1,
        end_line=2,
    )
    
    func_sym = Symbol(
        name="my_func",
        qualified_name="my_func",
        kind=SymbolKind.FUNCTION,
        language=Language.PYTHON,
        file_id=finfo.id,
        start_line=3,
        end_line=5,
    )
    
    fact = RelationshipFact(
        file_id=finfo.id,
        source_symbol_id=func_sym.id,
        fact_type=FactType.BINDING,
        raw_text="service",
        simple_name="service",
        qualified_hint="TransactionService",
        status=FactStatus.PENDING,
    )
    
    result_obj = AnalysisResult(
        file=finfo,
        symbols=[target_sym, func_sym],
        relationships=[],
        relationship_facts=[fact]
    )
    store.store_results([result_obj])
    
    resolver = BindingResolver()
    stored_fact = store.get_relationship_facts(fact_type=FactType.BINDING)[0]
    
    res = resolver.resolve(store, stored_fact)
    assert res is not None
    assert res.status == FactStatus.RESOLVED
    assert res.resolved_target_id == target_sym.id


def test_python_analyzer_emits_self_attribute_binding_fact():
    source = """
class TransactionRepository:
    pass


class TransactionService:
    def __init__(self, repository: TransactionRepository):
        self.repository = repository
""".strip()

    finfo = FileInfo(path="service.py", language=Language.PYTHON)
    result = PythonAnalyzer().analyze(finfo, source)

    binding_facts = [f for f in result.relationship_facts if f.fact_type == FactType.BINDING]
    assert any(
        f.raw_text == "repository" and f.qualified_hint == "TransactionRepository"
        for f in binding_facts
    )
    assert any(
        f.raw_text == "self.repository" and f.qualified_hint == "TransactionRepository"
        for f in binding_facts
    )
