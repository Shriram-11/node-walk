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

        calls = store.get_relationships_from(create_user.id, RelationshipType.CALLS)
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

        run_sym = next(s for s in store.get_all_symbols() if s.qualified_name == "service.run")
        create_user = next(
            s for s in store.get_all_symbols() if s.qualified_name == "service.UserService.create_user"
        )

        calls = store.get_relationships_from(run_sym.id, RelationshipType.CALLS)
        assert any(rel.target_id == create_user.id for rel in calls)
    finally:
        store.close()
