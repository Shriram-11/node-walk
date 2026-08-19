"""
codegraph.storage.repository — backward-compatibility shim.

The GraphStore interface and SQLiteGraphStore implementation now live in:
  - codegraph.storage.base         (GraphStore ABC)
  - codegraph.storage.sqlite_store (SQLiteGraphStore)

This module re-exports both so existing imports continue to work.
"""

from codegraph.storage.base import GraphStore
from codegraph.storage.sqlite_store import SQLiteGraphStore

__all__ = ["GraphStore", "SQLiteGraphStore"]
