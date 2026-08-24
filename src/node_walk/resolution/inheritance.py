from typing import Any

from node_walk.ir.enums import FactStatus, FactType, SymbolKind
from node_walk.ir.models import RelationshipFact, Symbol
from node_walk.storage.base import GraphStore
from node_walk.resolution.base import FactResolver, ResolutionResult


class InheritanceResolver(FactResolver):
    """
    Resolves INHERITANCE facts across files using import facts and global uniqueness.
    """

    @property
    def name(self) -> str:
        return "InheritanceResolver"

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.fact_type != FactType.INHERITANCE:
            return None

        # 1. Fetch import facts for the same file that are RESOLVED
        import_facts = store.get_relationship_facts(
            fact_type=FactType.IMPORT, status=FactStatus.RESOLVED
        )
        file_imports = [f for f in import_facts if f.file_id == fact.file_id]

        import_map: dict[str, str] = {}
        for imp in file_imports:
            if imp.resolved_target_id:
                import_map[imp.simple_name] = imp.resolved_target_id

        # 2. Check if the receiver or simple name matches an imported symbol
        match_id = None
        
        # Case A: `module.BaseClass` where `module` is imported
        if fact.receiver_text and fact.receiver_text in import_map:
            receiver_id = import_map[fact.receiver_text]
            receiver_sym = store.get_symbol(receiver_id)
            if receiver_sym:
                target_qname = f"{receiver_sym.qualified_name}.{fact.simple_name}"
                candidates = store.find_symbols_by_qualified_name(target_qname)
                if candidates:
                    match_id = candidates[0].id

        # Case B: `BaseClass` where `BaseClass` is imported directly
        elif not fact.receiver_text and fact.simple_name in import_map:
            match_id = import_map[fact.simple_name]
            
        if match_id:
            return ResolutionResult(
                status=FactStatus.RESOLVED,
                resolved_target_id=match_id,
                diagnostics={"strategy": "inheritance_import_aware"}
            )

        # 3. Fallback: In-file exact match
        local_candidates = [
            s for s in store.find_symbols_by_name(fact.simple_name, exact=True)
            if s.file_id == fact.file_id and s.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)
        ]
        if len(local_candidates) == 1:
            return ResolutionResult(
                status=FactStatus.RESOLVED,
                resolved_target_id=local_candidates[0].id,
                diagnostics={"strategy": "inheritance_in_file_exact"}
            )

        # 4. Fallback: Unambiguous global match
        all_matches = store.find_symbols_by_name(fact.simple_name, exact=True)
        valid_matches = [
            m for m in all_matches 
            if m.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)
        ]
        
        if fact.receiver_text:
            expected_suffix = f"{fact.receiver_text}.{fact.simple_name}"
            valid_matches = [
                m for m in valid_matches
                if m.qualified_name.endswith(f".{expected_suffix}")
            ]
            
        if len(valid_matches) == 1:
            return ResolutionResult(
                status=FactStatus.PROBABLE,
                resolved_target_id=valid_matches[0].id,
                diagnostics={"strategy": "inheritance_global_fallback", "candidates": 1}
            )

        return ResolutionResult(
            status=FactStatus.UNRESOLVED,
            diagnostics={"strategy": "inheritance_not_found"}
        )
