"""
Cascade invalidation for incremental re-indexing.
"""

from __future__ import annotations

from dataclasses import dataclass

from node_walk.storage.base import GraphStore


@dataclass
class InvalidationStats:
    files_invalidated: int = 0
    symbols_deleted: int = 0
    facts_reset: int = 0


class Invalidator:
    """
    Surgically removes stale symbols, relationships, facts, and file records
    for changed or removed files, and resets dependent facts in other files to PENDING.
    """

    def invalidate_files(self, store: GraphStore, file_ids: list[str]) -> InvalidationStats:
        if not file_ids:
            return InvalidationStats()

        all_deleted_symbol_ids: set[str] = set()

        for fid in file_ids:
            symbols = store.get_symbols_by_file(fid)
            for s in symbols:
                all_deleted_symbol_ids.add(s.id)
            store.delete_file_data(fid)

        # Cascade reset: reset facts that targeted any deleted symbol
        facts_reset = 0
        if all_deleted_symbol_ids:
            facts_reset = store.reset_facts_targeting(all_deleted_symbol_ids)

        return InvalidationStats(
            files_invalidated=len(file_ids),
            symbols_deleted=len(all_deleted_symbol_ids),
            facts_reset=facts_reset,
        )
