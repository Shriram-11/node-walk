from typing import Any

from node_walk.ir.enums import FactStatus, FactType, SymbolKind
from node_walk.ir.models import RelationshipFact, Symbol
from node_walk.storage.base import GraphStore
from node_walk.resolution.base import FactResolver, ResolutionResult


class BindingResolver(FactResolver):
    """
    Resolves BINDING facts (parameter type annotations and constructor assignments)
    to the actual class/symbol they refer to.
    """

    @property
    def name(self) -> str:
        return "BindingResolver"

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.fact_type != FactType.BINDING:
            return None

        hint = fact.qualified_hint
        if not hint:
            return ResolutionResult(
                status=FactStatus.UNRESOLVED,
                diagnostics={"strategy": "binding_no_hint"}
            )
            
        # Extract the base hint if it's dotted (e.g. models.User -> User)
        target_simple_name = hint.split(".")[-1]
        target_module_prefix = ".".join(hint.split(".")[:-1])

        # 1. Fetch import facts for the same file that are RESOLVED
        import_facts = store.get_relationship_facts(
            fact_type=FactType.IMPORT, status=FactStatus.RESOLVED
        )
        file_imports = [f for f in import_facts if f.file_id == fact.file_id]

        import_map: dict[str, str] = {}
        for imp in file_imports:
            if imp.resolved_target_id:
                import_map[imp.simple_name] = imp.resolved_target_id

        # 2. Check if the hint matches an imported symbol
        match_id = None
        
        # Case A: `module.ClassName` where `module` is imported
        if target_module_prefix and target_module_prefix in import_map:
            receiver_id = import_map[target_module_prefix]
            receiver_sym = store.get_symbol(receiver_id)
            if receiver_sym:
                target_qname = f"{receiver_sym.qualified_name}.{target_simple_name}"
                candidates = store.find_symbols_by_qualified_name(target_qname)
                if candidates:
                    match_id = candidates[0].id

        # Case B: `ClassName` where `ClassName` is imported directly
        elif not target_module_prefix and target_simple_name in import_map:
            match_id = import_map[target_simple_name]
            
        if match_id:
            return ResolutionResult(
                status=FactStatus.RESOLVED,
                resolved_target_id=match_id,
                diagnostics={"strategy": "binding_import_aware"}
            )

        # 3. Fallback: In-file exact match
        local_candidates = [
            s for s in store.find_symbols_by_name(target_simple_name, exact=True)
            if s.file_id == fact.file_id and s.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)
        ]
        if len(local_candidates) == 1:
            return ResolutionResult(
                status=FactStatus.RESOLVED,
                resolved_target_id=local_candidates[0].id,
                diagnostics={"strategy": "binding_in_file_exact"}
            )

        # 4. Fallback: Unambiguous global match
        all_matches = store.find_symbols_by_name(target_simple_name, exact=True)
        valid_matches = [
            m for m in all_matches 
            if m.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)
        ]
        
        if target_module_prefix:
            expected_suffix = f"{target_module_prefix}.{target_simple_name}"
            valid_matches = [
                m for m in valid_matches
                if m.qualified_name.endswith(f".{expected_suffix}")
            ]
            
        if len(valid_matches) == 1:
            return ResolutionResult(
                status=FactStatus.PROBABLE,
                resolved_target_id=valid_matches[0].id,
                diagnostics={"strategy": "binding_global_fallback", "candidates": 1}
            )

        return ResolutionResult(
            status=FactStatus.UNRESOLVED,
            diagnostics={"strategy": "binding_not_found"}
        )
