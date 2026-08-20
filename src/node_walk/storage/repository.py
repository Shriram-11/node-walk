"""
node_walk.storage.repository — backward-compatibility shim.

The GraphStore interface and SQLiteGraphStore implementation now live in:
  - node_walk.storage.base         (GraphStore ABC)
  - node_walk.storage.sqlite_store (SQLiteGraphStore)

This module re-exports both so existing imports continue to work.
"""

from node_walk.storage.base import GraphStore
from node_walk.storage.sqlite_store import SQLiteGraphStore

__all__ = ["GraphStore", "SQLiteGraphStore"]
