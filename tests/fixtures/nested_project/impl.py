"""Concrete repository implementation extending the ABC base."""
from tests.fixtures.nested_project.base import Repository


class SqlRepository(Repository):
    """SQLite-backed repository implementation."""

    def save(self, entity):
        return entity

    def find_by_id(self, entity_id: int):
        return None

    def find_all(self):
        return []
