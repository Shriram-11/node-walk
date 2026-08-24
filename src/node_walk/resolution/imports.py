from typing import Any

from node_walk.ir.enums import FactStatus, FactType, SymbolKind
from node_walk.ir.models import RelationshipFact, Symbol
from node_walk.storage.base import GraphStore
from node_walk.resolution.base import FactResolver, ResolutionResult


class ImportResolver(FactResolver):
    """
    Resolves IMPORT facts to target modules or symbols.
    Builds a foundation for cross-file call resolution.
    """

    @property
    def name(self) -> str:
        return "ImportResolver"

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.fact_type != FactType.IMPORT:
            return None

        # fact.raw_text has the target, e.g., "json", "jarvis.model", or "jarvis.model.base.Model"
        target_name = fact.raw_text

        # 1. Try to find an exact qualified name match for a module or symbol
        candidates = store.find_symbols_by_qualified_name(target_name)
        if candidates:
            # We usually expect 1 match if the QName is fully qualified
            # Prefer modules or classes/functions over other kinds
            best = candidates[0]
            for c in candidates:
                if c.kind == SymbolKind.MODULE:
                    best = c
                    break
                    
            return ResolutionResult(
                status=FactStatus.RESOLVED,
                resolved_target_id=best.id,
                diagnostics={"strategy": "import_qname_exact"}
            )

        # 2. Try to match by suffix if it's a module
        # Example: from x import y -> target_name might just be "y" or "x.y"
        # We can look up all symbols with name == fact.simple_name
        candidates = store.find_symbols_by_name(fact.simple_name, exact=True)
        # Filter for candidates whose qualified name ends with the target_name
        valid_candidates = [
            c for c in candidates 
            if c.qualified_name == target_name or c.qualified_name.endswith(f".{target_name}")
        ]

        if len(valid_candidates) == 1:
            return ResolutionResult(
                status=FactStatus.RESOLVED,
                resolved_target_id=valid_candidates[0].id,
                diagnostics={"strategy": "import_suffix_exact"}
            )
        elif len(valid_candidates) > 1:
            # Pick the most specific (shortest qualified name usually means closest to the root)
            best = min(valid_candidates, key=lambda s: len(s.qualified_name))
            return ResolutionResult(
                status=FactStatus.PROBABLE,
                resolved_target_id=best.id,
                diagnostics={"strategy": "import_suffix_ambiguous", "candidates": len(valid_candidates)}
            )

        return ResolutionResult(
            status=FactStatus.UNRESOLVED,
            diagnostics={"strategy": "import_not_found"}
        )
