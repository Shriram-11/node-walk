from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any

from node_walk.ir.enums import FactStatus
from node_walk.ir.models import RelationshipFact
from node_walk.storage.base import GraphStore


@dataclasses.dataclass(frozen=True)
class ResolutionResult:
    """The outcome of attempting to resolve a fact."""
    status: FactStatus
    resolved_target_id: str = ""
    diagnostics: dict[str, Any] = dataclasses.field(default_factory=dict)


class FactResolver(ABC):
    """
    Base class for a resolution pass.
    
    A resolver takes a set of pending facts and attempts to resolve them
    using the provided storage layer for lookups.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The name of this resolver, e.g., 'InFileCallResolver'."""
        ...

    @abstractmethod
    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        """
        Attempt to resolve a single fact.
        
        Returns:
            A ResolutionResult if this resolver made a decision (even an IGNORED or UNRESOLVED one),
            or None if this resolver does not handle this fact or defers to a later pass.
        """
        ...

    def run(self, store: GraphStore, facts: list[RelationshipFact]) -> int:
        """
        Run the resolver against a list of facts, updating the store for any that are resolved.
        
        Returns:
            The number of facts that were updated by this resolver.
        """
        resolved_count = 0
        for fact in facts:
            if fact.status in (FactStatus.RESOLVED, FactStatus.IGNORED):
                continue
            
            result = self.resolve(store, fact)
            if result is not None:
                store.update_relationship_fact(
                    fact.id,
                    status=result.status,
                    resolved_target_id=result.resolved_target_id,
                    resolver_name=self.name,
                    diagnostics=result.diagnostics,
                )
                # Update our local copy so subsequent resolvers see the new status if we pass the same list around
                # (Though usually we'll only pass pending facts)
                fact_dict = fact.model_dump()
                fact_dict.update({
                    "status": result.status,
                    "resolved_target_id": result.resolved_target_id,
                    "resolver_name": self.name,
                    "diagnostics": result.diagnostics,
                })
                # If facts are immutable (frozen=True in Pydantic v2), we can't easily mutate the list item inline safely without rebuilding it.
                # Since the indexer will re-fetch or filter pending facts between passes, we don't strictly need to mutate the object here.
                resolved_count += 1
                
        return resolved_count
