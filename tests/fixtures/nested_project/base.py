"""
Nested project fixture — tests packages, relative imports, ABCs, inheritance.
"""
from abc import ABC, abstractmethod


class Repository(ABC):
    """Abstract repository interface."""

    @abstractmethod
    def save(self, entity):
        ...

    @abstractmethod
    def find_by_id(self, entity_id: int):
        ...

    def find_all(self):
        return []
