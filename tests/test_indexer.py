from pathlib import Path

from node_walk.indexer import Indexer
from node_walk.ir.enums import RelationshipType
from node_walk.storage.sqlite_store import SQLiteGraphStore


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "simple_project"


def test_indexer_materializes_class_member_call_from_facts(tmp_path):
    db_path = tmp_path / "graph.db"
    store = SQLiteGraphStore(db_path)
    try:
        stats = Indexer(store).index(FIXTURE_DIR)
        assert stats.files_analyzed >= 1

        create_user = next(
            s for s in store.get_all_symbols()
            if s.qualified_name == "services.UserService.create_user"
        )
        notify = next(
            s for s in store.get_all_symbols()
            if s.qualified_name == "services.UserService._notify"
        )

        calls = store.get_relationships_from(
            create_user.id, RelationshipType.CALLS)
        assert any(rel.target_id == notify.id for rel in calls)

        notify_facts = [
            f for f in store.get_relationship_facts()
            if f.source_symbol_id == create_user.id and f.raw_text == "self._notify"
        ]
        assert len(notify_facts) == 1
        assert notify_facts[0].resolved_target_id == notify.id
    finally:
        store.close()


def test_indexer_resolves_receiver_method_via_local_constructor_binding(tmp_path):
    project = tmp_path / "mini_project"
    project.mkdir()
    (project / "service.py").write_text(
        """
class UserService:
    def create_user(self):
        return True


def run():
    svc = UserService()
    return svc.create_user()
""".strip(),
        encoding="utf-8",
    )

    db_path = tmp_path / "graph2.db"
    store = SQLiteGraphStore(db_path)
    try:
        stats = Indexer(store).index(project)
        assert stats.files_analyzed == 1

        run_sym = next(s for s in store.get_all_symbols()
                       if s.qualified_name == "service.run")
        create_user = next(
            s for s in store.get_all_symbols() if s.qualified_name == "service.UserService.create_user"
        )

        calls = store.get_relationships_from(
            run_sym.id, RelationshipType.CALLS)
        assert any(rel.target_id == create_user.id for rel in calls)
    finally:
        store.close()


def test_indexer_resolves_receiver_method_via_typed_parameter_binding(tmp_path):
    project = tmp_path / "typed_project"
    project.mkdir()
    (project / "controller.py").write_text(
        """
class TransactionService:
    def get_by_id(self, session, txn_id):
        return txn_id


def get_transaction_by_id(session, service: TransactionService):
    return service.get_by_id(session, 1)
""".strip(),
        encoding="utf-8",
    )

    db_path = tmp_path / "graph3.db"
    store = SQLiteGraphStore(db_path)
    try:
        stats = Indexer(store).index(project)
        assert stats.files_analyzed == 1

        controller = next(
            s for s in store.get_all_symbols() if s.qualified_name == "controller.get_transaction_by_id"
        )
        service_method = next(
            s for s in store.get_all_symbols() if s.qualified_name == "controller.TransactionService.get_by_id"
        )

        calls = store.get_relationships_from(
            controller.id, RelationshipType.CALLS)
        assert any(rel.target_id == service_method.id for rel in calls)
    finally:
        store.close()


def test_indexer_resolves_dotted_receiver_method_via_attribute_binding(tmp_path):
    project = tmp_path / "attribute_project"
    project.mkdir()
    (project / "service.py").write_text(
        """
class TransactionRepository:
    def get_by_id(self, txn_id):
        return txn_id


class TransactionService:
    def __init__(self, repository: TransactionRepository):
        self.repository = repository

    def get_transaction(self, txn_id):
        return self.repository.get_by_id(txn_id)
""".strip(),
        encoding="utf-8",
    )

    db_path = tmp_path / "graph4.db"
    store = SQLiteGraphStore(db_path)
    try:
        Indexer(store).index(project)

        service_method = next(
            s for s in store.get_all_symbols()
            if s.qualified_name == "service.TransactionService.get_transaction"
        )
        repository_method = next(
            s for s in store.get_all_symbols()
            if s.qualified_name == "service.TransactionRepository.get_by_id"
        )

        calls = store.get_relationships_from(
            service_method.id, RelationshipType.CALLS)
        assert any(rel.target_id == repository_method.id for rel in calls)
    finally:
        store.close()

def test_indexer_resolves_fastapi_depends(tmp_path):
    project = tmp_path / "fastapi_project"
    project.mkdir()
    (project / "main.py").write_text(
        """
class UserService:
    def get_by_id(self, user_id):
        return user_id

def get_service() -> UserService:
    return UserService()

def get_user(service: UserService = Depends(get_service)):
    return service.get_by_id(1)
""".strip(),
        encoding="utf-8",
    )

    db_path = tmp_path / "graph5.db"
    store = SQLiteGraphStore(db_path)
    try:
        from node_walk.indexer import Indexer
        Indexer(store).index(project)

        get_user = next(
            s for s in store.get_all_symbols()
            if s.qualified_name == "main.get_user"
        )
        service_method = next(
            s for s in store.get_all_symbols()
            if s.qualified_name == "main.UserService.get_by_id"
        )

        calls = store.get_relationships_from(
            get_user.id, RelationshipType.CALLS)
        assert any(rel.target_id == service_method.id for rel in calls)
    finally:
        store.close()
