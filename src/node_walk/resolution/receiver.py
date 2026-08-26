from dataclasses import dataclass
from typing import Optional

from node_walk.ir.enums import FactStatus, FactType, SymbolKind
from node_walk.ir.models import RelationshipFact, Symbol
from node_walk.storage.base import GraphStore


@dataclass
class ReceiverResolution:
    resolved_symbol_id: str
    confidence: str  # "RESOLVED", "PROBABLE", "UNRESOLVED"
    diagnostics: dict
    evidence: str


@dataclass
class SymbolResolution:
    resolved_symbol_id: str
    confidence: str
    diagnostics: dict


class BindingIndex:
    """
    Indexes resolved BINDING facts for fast lookup by file, scope, and path.
    """

    def __init__(self, store: GraphStore):
        self._store = store
        self._bindings_by_file: dict[str, list[RelationshipFact]] = {}
        
        # Load all resolved bindings
        for fact in self._store.get_relationship_facts(fact_type=FactType.BINDING):
            if fact.status in (FactStatus.RESOLVED, FactStatus.PROBABLE) and fact.resolved_target_id:
                self._bindings_by_file.setdefault(fact.file_id, []).append(fact)

    def get_binding(self, file_id: str, scope_id: str, path: str) -> RelationshipFact | None:
        """
        Look up the most applicable binding for a path in the given scope.
        """
        bindings = self._bindings_by_file.get(file_id, [])
        if not bindings:
            return None

        # Gather acceptable scope IDs (current scope + enclosing class)
        valid_scope_ids = {scope_id}
        
        caller = self._store.get_symbol(scope_id)
        if caller:
            current_id = caller.parent_id
            while current_id:
                current = self._store.get_symbol(current_id)
                if not current:
                    break
                if current.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE):
                    valid_scope_ids.add(current.id)
                    # Also include constructor method as a valid scope for class attributes
                    for m_id in self._get_init_methods(current.id):
                        valid_scope_ids.add(m_id)
                    break
                current_id = current.parent_id

        # Find exact path matches in valid scopes
        applicable = [
            b for b in bindings 
            if b.raw_text == path and b.scope_symbol_id in valid_scope_ids
        ]
        
        if applicable:
            # Sort by line number to get the latest assignment before usage, or just the first for now.
            return applicable[-1]

        return None

    def _get_init_methods(self, class_id: str) -> list[str]:
        # Return the IDs of any __init__ methods in the class
        inits = []
        cls_sym = self._store.get_symbol(class_id)
        if not cls_sym:
            return inits
        
        init_qname = f"{cls_sym.qualified_name}.__init__"
        for s in self._store.find_symbols_by_qualified_name(init_qname):
            inits.append(s.id)
        return inits


class ReceiverService:
    def __init__(self, store: GraphStore, index: BindingIndex):
        self._store = store
        self._index = index

    def resolve_receiver(self, call_fact: RelationshipFact) -> ReceiverResolution | None:
        receiver_text = call_fact.receiver_text
        if not receiver_text:
            return None
            
        # Support nested paths (self.repo.session)
        segments = receiver_text.split(".")
        
        # 0. Check exact path in BindingIndex (e.g., self.repository, self.repo.session)
        exact_binding = self._index.get_binding(call_fact.file_id, call_fact.scope_symbol_id, receiver_text)
        if exact_binding:
            confidence = "RESOLVED" if exact_binding.status == FactStatus.RESOLVED else "PROBABLE"
            return ReceiverResolution(
                resolved_symbol_id=exact_binding.resolved_target_id,
                confidence=confidence,
                diagnostics={"strategy": "binding_index_exact", "binding_id": exact_binding.id},
                evidence=exact_binding.metadata.get("binding_type", "unknown")
            )

        # 1. Resolve root segment
        root_segment = segments[0]
        current_resolved_id = None
        confidence = "RESOLVED"
        diagnostics = {}
        evidence = ""
        
        # Check if root is 'self'
        if root_segment == "self":
            caller = self._store.get_symbol(call_fact.scope_symbol_id)
            if caller:
                enclosing = self._get_enclosing_class(caller)
                if enclosing:
                    current_resolved_id = enclosing.id
                    diagnostics={"strategy": "self"}
                    evidence = "self"

        # 1. Check BindingIndex
        binding = self._index.get_binding(call_fact.file_id, call_fact.scope_symbol_id, root_segment)
        print(f"DEBUG: resolve_receiver path='{receiver_text}', root='{root_segment}', caller_id={call_fact.scope_symbol_id}, binding={binding}")
        if not current_resolved_id and binding:
                confidence = "RESOLVED" if binding.status == FactStatus.RESOLVED else "PROBABLE"
                current_resolved_id = binding.resolved_target_id
                diagnostics={"strategy": "binding_index", "binding_id": binding.id}
                evidence=binding.metadata.get("binding_type", "unknown")

        # Check imports for root
        if not current_resolved_id:
            import_facts = self._store.get_relationship_facts(
                fact_type=FactType.IMPORT, status=FactStatus.RESOLVED
            )
            for imp in import_facts:
                if imp.file_id == call_fact.file_id and imp.simple_name == root_segment and imp.resolved_target_id:
                    current_resolved_id = imp.resolved_target_id
                    confidence = "RESOLVED"
                    diagnostics={"strategy": "import_alias"}
                    evidence = "import"
                    break

        if not current_resolved_id:
            return None

        # 2. Apply remaining segments
        for segment in segments[1:]:
            res = self.resolve_member(
                ReceiverResolution(current_resolved_id, confidence, diagnostics, evidence), 
                segment
            )
            if not res:
                return None
            current_resolved_id = res.resolved_symbol_id
            confidence = res.confidence

        return ReceiverResolution(
            resolved_symbol_id=current_resolved_id,
            confidence=confidence,
            diagnostics=diagnostics,
            evidence=evidence
        )

    def resolve_member(self, receiver_res: ReceiverResolution, member_name: str) -> SymbolResolution | None:
        receiver_sym = self._store.get_symbol(receiver_res.resolved_symbol_id)
        if not receiver_sym:
            return None

        target_qname = f"{receiver_sym.qualified_name}.{member_name}"
        candidates = self._store.find_symbols_by_qualified_name(target_qname)
        if candidates:
            return SymbolResolution(
                resolved_symbol_id=candidates[0].id,
                confidence=receiver_res.confidence,
                diagnostics={"strategy": "member_lookup"}
            )
            
        return None

    def _get_enclosing_class(self, sym: Symbol) -> Symbol | None:
        current_id = sym.parent_id
        while current_id:
            current = self._store.get_symbol(current_id)
            if not current:
                return None
            if current.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE):
                return current
            current_id = current.parent_id
        return None
