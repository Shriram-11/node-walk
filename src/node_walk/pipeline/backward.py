"""
Backward pass: multi-iteration semantic resolution over relationship facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from node_walk.ir.enums import FactStatus, FactType
from node_walk.resolution.base import FactResolver
from node_walk.resolution.bindings import BindingResolver
from node_walk.resolution.calls import (
    ClassMemberCallResolver,
    ConstructorCallResolver,
    CrossFileCallResolver,
    InFileCallResolver,
    NoiseFilterCallResolver,
)
from node_walk.resolution.imports import ImportResolver
from node_walk.resolution.inheritance import InheritanceResolver
from node_walk.storage.base import GraphStore


DEFAULT_RESOLVER_SEQUENCE: list[tuple[type[FactResolver], FactType]] = [
    # Phase 1: Foundation — resolve what imports point to
    (ImportResolver, FactType.IMPORT),
    # Phase 2: Structural inheritance
    (InheritanceResolver, FactType.INHERITANCE),
    # Phase 3: Type knowledge — resolve local & attribute bindings
    (BindingResolver, FactType.BINDING),
    # Phase 4: Call resolution cascade
    (NoiseFilterCallResolver, FactType.CALL),
    (InFileCallResolver, FactType.CALL),
    (ClassMemberCallResolver, FactType.CALL),
    (ConstructorCallResolver, FactType.CALL),
    (CrossFileCallResolver, FactType.CALL),
]


@dataclass
class IterationStats:
    iteration: int
    facts_resolved: int = 0
    facts_probable: int = 0
    facts_ignored: int = 0
    facts_unresolved: int = 0
    resolver_stats: dict[str, int] = field(default_factory=dict)


@dataclass
class BackwardResult:
    iterations_run: int = 0
    converged: bool = False
    iteration_stats: list[IterationStats] = field(default_factory=list)
    total_resolved: int = 0
    total_probable: int = 0
    total_ignored: int = 0
    total_pending: int = 0


class BackwardPass:
    """
    Runs semantic resolvers in a loop until convergence or max_iterations is reached.
    """

    def __init__(
        self,
        resolver_sequence: Sequence[tuple[type[FactResolver], FactType]] | None = None,
        max_iterations: int = 5,
    ) -> None:
        self._resolver_sequence = list(resolver_sequence or DEFAULT_RESOLVER_SEQUENCE)
        self._max_iterations = max(1, max_iterations)

    def run(self, store: GraphStore) -> BackwardResult:
        iteration_stats_list: list[IterationStats] = []
        converged = False

        for iteration in range(1, self._max_iterations + 1):
            stats = IterationStats(iteration=iteration)
            iteration_resolved = 0

            # If store supports resetting unresolved facts back to pending between iterations, do so
            if iteration > 1 and hasattr(store, "reset_unresolved_facts_to_pending"):
                store.reset_unresolved_facts_to_pending()

            for resolver_cls, fact_type in self._resolver_sequence:
                pending_facts = store.get_relationship_facts(
                    fact_type=fact_type, status=FactStatus.PENDING
                )
                if not pending_facts:
                    continue

                resolver = resolver_cls()
                run_res = resolver.run(store, pending_facts)

                # Support both int return (Phase 1) and RunResult (Phase 2)
                resolved_count = (
                    run_res.progress_made if hasattr(run_res, "progress_made") else int(run_res)
                )

                if hasattr(run_res, "resolved"):
                    stats.facts_resolved += run_res.resolved
                    stats.facts_probable += run_res.probable
                    stats.facts_ignored += run_res.ignored
                    stats.facts_unresolved += run_res.unresolved
                else:
                    stats.facts_resolved += resolved_count

                stats.resolver_stats[resolver.name] = (
                    stats.resolver_stats.get(resolver.name, 0) + resolved_count
                )
                iteration_resolved += resolved_count

            iteration_stats_list.append(stats)

            # Check convergence: no facts were resolved/progressed in this iteration
            if iteration_resolved == 0:
                converged = True
                break

        # Calculate totals from store
        all_facts = store.get_relationship_facts()
        total_resolved = sum(1 for f in all_facts if f.status == FactStatus.RESOLVED)
        total_probable = sum(1 for f in all_facts if f.status == FactStatus.PROBABLE)
        total_ignored = sum(1 for f in all_facts if f.status == FactStatus.IGNORED)
        total_pending = sum(1 for f in all_facts if f.status == FactStatus.PENDING)

        return BackwardResult(
            iterations_run=len(iteration_stats_list),
            converged=converged,
            iteration_stats=iteration_stats_list,
            total_resolved=total_resolved,
            total_probable=total_probable,
            total_ignored=total_ignored,
            total_pending=total_pending,
        )
