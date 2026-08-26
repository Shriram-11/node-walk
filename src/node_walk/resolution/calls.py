from typing import Any

from node_walk.ir.enums import FactStatus, FactType, SymbolKind
from node_walk.ir.models import RelationshipFact, Symbol
from node_walk.storage.base import GraphStore
from node_walk.resolution.base import FactResolver, ResolutionResult


_BUILTINS = {
    "str", "int", "len", "isinstance", "print", "super", "range", "type",
    "list", "dict", "set", "tuple", "bool", "float", "enumerate", "zip",
    "map", "filter", "sorted", "reversed", "getattr", "setattr", "hasattr",
    "property", "staticmethod", "classmethod", "object", "repr", "abs",
    "min", "max", "open", "iter", "next", "any", "all", "vars", "dir",
    "id", "hex", "oct", "bin", "chr", "ord", "hash", "callable", "format", "round"
}

_COMMON_METHODS = {
    "get", "append", "pop", "items", "keys", "values", "strip", "split",
    "join", "encode", "decode", "format", "replace", "lower", "upper",
    "read", "write", "close", "rstrip", "lstrip", "startswith", "endswith",
    "update", "extend", "remove", "insert", "copy", "clear", "add", "discard",
    "isdigit", "isupper", "islower"
}


class NoiseFilterCallResolver(FactResolver):
    """
    Marks common untyped stdlib chains and builtins as IGNORED.
    This prevents the graph from being cluttered with calls that we aren't likely
    to resolve to project symbols anyway.
    """

    @property
    def name(self) -> str:
        return "NoiseFilterCallResolver"

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.fact_type != FactType.CALL:
            return None

        # Ignore builtins
        if not fact.receiver_text and fact.simple_name in _BUILTINS:
            return ResolutionResult(
                status=FactStatus.IGNORED,
                diagnostics={"reason": "python_builtin"}
            )

        # Ignore common methods when receiver is present
        if fact.receiver_text and fact.simple_name in _COMMON_METHODS:
            return ResolutionResult(
                status=FactStatus.IGNORED,
                diagnostics={"reason": "common_untyped_method"}
            )

        # Ignore stdlib modules if recognizable
        if fact.receiver_text in ("os.path", "json", "logging", "sys", "re", "datetime"):
            return ResolutionResult(
                status=FactStatus.IGNORED,
                diagnostics={"reason": f"stdlib_{fact.receiver_text}"}
            )

        return None


class ClassMemberCallResolver(FactResolver):
    """
    Resolves self.method() calls by looking up the method in the enclosing class.
    """

    @property
    def name(self) -> str:
        return "ClassMemberCallResolver"

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.fact_type != FactType.CALL:
            return None

        if fact.receiver_text not in ("self", "cls"):
            return None

        if not fact.scope_symbol_id:
            return None

        scope_symbol = store.get_symbol(fact.scope_symbol_id)
        if not scope_symbol or not scope_symbol.parent_id:
            return None

        parent_symbol = store.get_symbol(scope_symbol.parent_id)
        if not parent_symbol or parent_symbol.kind not in (SymbolKind.CLASS, SymbolKind.INTERFACE):
            return None

        # Look for a method or field in the parent class
        target_qname = f"{parent_symbol.qualified_name}.{fact.simple_name}"
        candidates = store.find_symbols_by_qualified_name(target_qname)

        if candidates:
            # Should normally just be one
            return ResolutionResult(
                status=FactStatus.RESOLVED,
                resolved_target_id=candidates[0].id,
                diagnostics={"strategy": "class_member",
                             "class": parent_symbol.name}
            )

        # If we didn't find it, it might be inherited or not indexed yet. Let's not fail it permanently.
        return None


class InFileCallResolver(FactResolver):
    """
    Resolves a call if there is exactly one symbol with that name in the same file.
    Good for local function calls or intra-module class instantiations.
    """

    @property
    def name(self) -> str:
        return "InFileCallResolver"

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.fact_type != FactType.CALL:
            return None

        if fact.receiver_text:
            # Only handle plain calls here, e.g. foo()
            return None

        # Look for symbols with the exact name in the same file
        candidates = [
            s for s in store.find_symbols_by_name(fact.simple_name, exact=True)
            if s.file_id == fact.file_id
        ]

        if not candidates:
            return None

        if len(candidates) == 1:
            return ResolutionResult(
                status=FactStatus.RESOLVED,
                resolved_target_id=candidates[0].id,
                diagnostics={"strategy": "in_file_exact"}
            )

        # Multiple candidates in the same file (e.g. overloaded or same name in different classes)
        # Without deeper scope analysis, we mark it PROBABLE with the first candidate.
        return ResolutionResult(
            status=FactStatus.PROBABLE,
            resolved_target_id=candidates[0].id,
            diagnostics={"strategy": "in_file_ambiguous",
                         "candidates": len(candidates)}
        )


class ConstructorCallResolver(FactResolver):
    """
    If a call's target resolves to a CLASS, this remaps it to the CLASS.__init__ method,
    since the semantic target of a constructor call is the initializer.
    """

    @property
    def name(self) -> str:
        return "ConstructorCallResolver"

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.fact_type != FactType.CALL:
            return None

        # This resolver runs after InFile or Import resolvers.
        # But wait, if those didn't resolve it, we can also look for a globally unique class.
        # If it is already resolved to a CLASS, we remap it.
        # But `resolve` is called on facts that are PENDING.

        # Let's see if there is exactly one CLASS with this name globally.
        # (This acts as a fallback for cross-file without imports for now).
        if fact.receiver_text:
            # We only handle ClassName() right now, or maybe module.ClassName()
            pass

        candidates = [
            s for s in store.find_symbols_by_name(fact.simple_name, exact=True)
            if s.kind == SymbolKind.CLASS
        ]

        if len(candidates) == 1:
            class_sym = candidates[0]
            # Try to find __init__
            init_qname = f"{class_sym.qualified_name}.__init__"
            init_methods = store.find_symbols_by_qualified_name(init_qname)

            if init_methods:
                return ResolutionResult(
                    status=FactStatus.RESOLVED,
                    resolved_target_id=init_methods[0].id,
                    diagnostics={"strategy": "constructor_exact"}
                )
            else:
                # Resolve to the class itself if no __init__ exists
                return ResolutionResult(
                    status=FactStatus.RESOLVED,
                    resolved_target_id=class_sym.id,
                    diagnostics={"strategy": "constructor_class_only"}
                )

        return None


class CrossFileCallResolver(FactResolver):
    """
    Resolves calls across files by binding receivers, then looking up members.

    A call such as ``self.repository.get_by_id()`` is resolved in two steps:
    first ``self.repository`` is mapped to its bound symbol by a resolved
    BINDING fact, then ``get_by_id`` is looked up on that symbol's qualified
    name. The same path works for local variables, parameters, and imported
    module or class receivers.
    """

    @property
    def name(self) -> str:
        return "CrossFileCallResolver"

    def run(self, store: GraphStore, facts: list[RelationshipFact]) -> int:
        from node_walk.resolution.receiver import BindingIndex, ReceiverService
        self._index = BindingIndex(store)
        self._service = ReceiverService(store, self._index)
        return super().run(store, facts)

    def resolve(self, store: GraphStore, fact: RelationshipFact) -> ResolutionResult | None:
        if fact.fact_type != FactType.CALL:
            return None

        # 1. Try resolving through receiver chains
        if fact.receiver_text:
            receiver_res = self._service.resolve_receiver(fact)
            if receiver_res:
                member_res = self._service.resolve_member(receiver_res, fact.simple_name)
                if member_res:
                    return ResolutionResult(
                        status=FactStatus(member_res.confidence.lower()),
                        resolved_target_id=member_res.resolved_symbol_id,
                        diagnostics={
                            "strategy": "cross_file_receiver_chain",
                            "receiver_diagnostics": receiver_res.diagnostics,
                            "member_diagnostics": member_res.diagnostics,
                            "evidence": receiver_res.evidence
                        }
                    )
                else:
                    return ResolutionResult(
                        status=FactStatus.UNRESOLVED,
                        diagnostics={
                            "strategy": "cross_file_missing_member",
                            "receiver_diagnostics": receiver_res.diagnostics
                        }
                    )

        # 2. Try simple import match for direct function calls
        import_facts = store.get_relationship_facts(
            fact_type=FactType.IMPORT, status=FactStatus.RESOLVED
        )
        for imp in import_facts:
            if imp.file_id == fact.file_id and imp.simple_name == fact.simple_name and imp.resolved_target_id:
                return ResolutionResult(
                    status=FactStatus.RESOLVED,
                    resolved_target_id=imp.resolved_target_id,
                    diagnostics={"strategy": "cross_file_direct_import"}
                )

        # 4. Fallback: Unambiguous global match
        # If we couldn't resolve via imports, maybe the simple name is completely unique across the repo?
        all_matches = store.find_symbols_by_name(fact.simple_name, exact=True)
        # Filter out builtins or local variables (prefer functions, methods, classes)
        valid_matches = [
            m for m in all_matches
            if m.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS)
        ]

        # If the receiver text is present, filter by matching qualified name suffix
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
                diagnostics={
                    "strategy": "cross_file_global_fallback", "candidates": 1}
            )

        return ResolutionResult(
            status=FactStatus.UNRESOLVED,
            diagnostics={"strategy": "cross_file_not_found"}
        )
