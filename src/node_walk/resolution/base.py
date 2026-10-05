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


@dataclasses.dataclass
class RunResult:
    """Summary of fact status changes produced during a resolver run."""
    resolved: int = 0
    probable: int = 0
    ignored: int = 0
    unresolved: int = 0

    @property
    def total_decided(self) -> int:
        return self.resolved + self.probable + self.ignored + self.unresolved

    @property
    def progress_made(self) -> int:
        """Facts that reached a positive terminal status."""
        return self.resolved + self.probable

    def record(self, status: FactStatus) -> None:
        if status == FactStatus.RESOLVED:
            self.resolved += 1
        elif status == FactStatus.PROBABLE:
            self.probable += 1
        elif status == FactStatus.IGNORED:
            self.ignored += 1
        elif status == FactStatus.UNRESOLVED:
            self.unresolved += 1

    def __int__(self) -> int:
        return self.progress_made


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

    def run(self, store: GraphStore, facts: list[RelationshipFact]) -> RunResult:
        """
        Run the resolver against a list of facts, updating the store for any that are resolved.
        
        Returns:
            A RunResult with counts of facts moved into each status.
        """
        result = RunResult()
        for fact in facts:
            if fact.status in (FactStatus.RESOLVED, FactStatus.IGNORED):
                continue
            
            res = self.resolve(store, fact)
            if res is not None:
                store.update_relationship_fact(
                    fact.id,
                    status=res.status,
                    resolved_target_id=res.resolved_target_id,
                    resolver_name=self.name,
                    diagnostics=res.diagnostics,
                )
                result.record(res.status)
                
        return result
