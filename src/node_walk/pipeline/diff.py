"""
File diffing for incremental re-indexing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from node_walk.ir.models import FileInfo
from node_walk.storage.base import GraphStore


@dataclass
class DiffResult:
    """Summary of changes between disk files and stored graph files."""

    added: list[FileInfo] = field(default_factory=list)
    changed: list[FileInfo] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)       # list of file_ids
    unchanged: list[str] = field(default_factory=list)     # list of file_ids

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)


class FileDiff:
    """Compares discovered files against the stored file records."""

    def diff(self, discovered: list[FileInfo], store: GraphStore) -> DiffResult:
        stored_files = store.get_all_files()
        stored_by_path: dict[str, FileInfo] = {f.path: f for f in stored_files}

        discovered_paths: set[str] = set()
        added: list[FileInfo] = []
        changed: list[FileInfo] = []
        unchanged: list[str] = []

        for disc in discovered:
            discovered_paths.add(disc.path)
            stored = stored_by_path.get(disc.path)
            if stored is None:
                added.append(disc)
            elif stored.content_hash != disc.content_hash:
                changed.append(disc)
            else:
                unchanged.append(stored.id)

        removed: list[str] = [
            stored.id
            for stored in stored_files
            if stored.path not in discovered_paths
        ]

        return DiffResult(
            added=added,
            changed=changed,
            removed=removed,
            unchanged=unchanged,
        )
