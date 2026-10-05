"""
Materialization pass: transforms resolved/probable relationship facts into final Graph relationships.
"""

from __future__ import annotations

from dataclasses import dataclass

from node_walk.ir.enums import FactStatus, FactType, RelationshipType, ResolutionStatus
from node_walk.ir.models import Relationship
from node_walk.storage.base import GraphStore


@dataclass
class MaterializationResult:
    relationships_created: int = 0
    relationships_deduplicated: int = 0


class Materializer:
    """
    Reads final fact states and materializes Relationship edges into the store.
    """

    def run(self, store: GraphStore) -> MaterializationResult:
        new_relationships: list[Relationship] = []

        # 1. CALL facts -> CALLS relationships
        call_facts = store.get_relationship_facts(fact_type=FactType.CALL)
        for fact in call_facts:
            if fact.status in (FactStatus.RESOLVED, FactStatus.PROBABLE) and fact.resolved_target_id:
                rel = Relationship(
                    source_id=fact.source_symbol_id,
                    target_id=fact.resolved_target_id,
                    type=RelationshipType.CALLS,
                    source_location=fact.source_location,
                    resolution=(
                        ResolutionStatus.RESOLVED
                        if fact.status == FactStatus.RESOLVED
                        else ResolutionStatus.PROBABLE
                    ),
                    metadata={
                        "fact_id": fact.id,
                        "call_text": fact.raw_text,
                        "callee_name": fact.simple_name,
                        "resolver": fact.resolver_name,
                    },
                )
                new_relationships.append(rel)

        # 2. INHERITANCE facts -> EXTENDS / IMPLEMENTS relationships
        inheritance_facts = store.get_relationship_facts(fact_type=FactType.INHERITANCE)
        for fact in inheritance_facts:
            if fact.status in (FactStatus.RESOLVED, FactStatus.PROBABLE) and fact.resolved_target_id:
                is_impl = fact.metadata.get("is_implements", False)
                rel_type = RelationshipType.IMPLEMENTS if is_impl else RelationshipType.EXTENDS
                rel = Relationship(
                    source_id=fact.source_symbol_id,
                    target_id=fact.resolved_target_id,
                    type=rel_type,
                    source_location=fact.source_location,
                    resolution=(
                        ResolutionStatus.RESOLVED
                        if fact.status == FactStatus.RESOLVED
                        else ResolutionStatus.PROBABLE
                    ),
                    metadata={
                        "fact_id": fact.id,
                        "target_name": fact.raw_text,
                        "resolver": fact.resolver_name,
                    },
                )
                new_relationships.append(rel)

        # 3. Deduplicate by (source_id, target_id, type)
        seen_keys: set[tuple[str, str, RelationshipType]] = set()
        deduped_relationships: list[Relationship] = []
        dedup_count = 0

        for rel in new_relationships:
            key = (rel.source_id, rel.target_id, rel.type)
            if key in seen_keys:
                dedup_count += 1
                continue
            seen_keys.add(key)
            deduped_relationships.append(rel)

        # 4. Store relationships
        if deduped_relationships:
            store.store_relationships(deduped_relationships)

        return MaterializationResult(
            relationships_created=len(deduped_relationships),
            relationships_deduplicated=dedup_count,
        )
