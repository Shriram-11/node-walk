"""
AST visitor — walks a Tree-sitter parse tree and collects
symbols and relationships for a single Python source file.

This module contains:
  - Module-level helper functions (node text, line numbers, docstrings)
  - Constants (ABC/Protocol base names, tree-sitter parser setup)
  - SymbolCollector — the stateful visitor class

Resolution strategy
-------------------
CONTAINS    Always resolved (structural, not semantic).
IMPORTS     Emitted as UNRESOLVED; resolved in the cross-file pass (Indexer).
CALLS       Best-effort: exact FQN match → RESOLVED; simple name match →
            PROBABLE; no match → UNRESOLVED (target_id = "").
EXTENDS /
IMPLEMENTS  Emitted as UNRESOLVED; resolved in the cross-file pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tree_sitter_python as tspython
from tree_sitter import Language as TSLanguage, Node, Parser

from codegraph.analysis.python.scope import Scope
from codegraph.ir.enums import Language, RelationshipType, ResolutionStatus, SymbolKind
from codegraph.ir.models import (
    AnalysisResult,
    FileInfo,
    Relationship,
    SourceLocation,
    Symbol,
)

# ---------------------------------------------------------------------------
# Tree-sitter parser (module-level singleton — safe to share across threads
# since parse() is called per-file and returns a new tree each time)
# ---------------------------------------------------------------------------

_PY_LANGUAGE = TSLanguage(tspython.language())
_PARSER = Parser(_PY_LANGUAGE)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Base class names that mark a class as an interface/protocol
_ABC_BASES: frozenset[str] = frozenset({"ABC", "ABCMeta"})
_PROTOCOL_BASES: frozenset[str] = frozenset({"Protocol"})


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def node_text(node: Node, source: bytes) -> str:
    """Return the UTF-8 text of a tree-sitter node."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def start_line(node: Node) -> int:
    """1-indexed start line of a node."""
    return node.start_point[0] + 1


def end_line(node: Node) -> int:
    """1-indexed end line of a node."""
    return node.end_point[0] + 1


def first_docstring(node: Node, source: bytes) -> str:
    """
    Extract the first string literal from a block body node.
    Returns an empty string if none is found.
    """
    for child in node.children:
        if child.type == "expression_statement":
            for grandchild in child.children:
                if grandchild.type in ("string", "concatenated_string"):
                    raw = node_text(grandchild, source)
                    for q in ('"""', "'''", '"', "'"):
                        if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                            return raw[len(q) : -len(q)].strip()
    return ""


def derive_module_qname(file_path: str) -> str:
    """
    Derive a dotted module qualified name from an absolute file path.

    Walks up the directory tree while __init__.py exists (i.e., while
    we are inside a package). Falls back to the file stem otherwise.

    Example: ``/repo/myapp/services/users.py`` → ``myapp.services.users``
    """
    p = Path(file_path)
    parts: list[str] = [p.stem if p.suffix == ".py" else p.name]
    current = p.parent

    while (current / "__init__.py").exists():
        parts.append(current.name)
        current = current.parent

    parts.reverse()
    return ".".join(parts)


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------


class SymbolCollector:
    """
    Stateful visitor that walks a Tree-sitter Python AST and produces
    lists of Symbol and Relationship objects.

    Usage::

        collector = SymbolCollector(file_info, source_bytes)
        collector.visit(tree.root_node)
        result = AnalysisResult(
            file=file_info,
            symbols=collector.symbols,
            relationships=collector.relationships,
        )
    """

    def __init__(self, file_info: FileInfo, source_bytes: bytes) -> None:
        self.file_info = file_info
        self.source = source_bytes
        self.symbols: list[Symbol] = []
        self.relationships: list[Relationship] = []

        # Fast lookup: simple name → Symbol (for in-file resolution)
        self._local_by_name: dict[str, Symbol] = {}
        # Fast lookup: qualified name → Symbol (for in-file resolution)
        self._local_by_qname: dict[str, Symbol] = {}

        self._scope = Scope()
        self._module_qname = derive_module_qname(file_info.path)
        self._file_symbol = self._make_file_symbol()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def visit(self, root: Node) -> None:
        """Walk the module root node and collect everything."""
        self._visit_module(root)

    # ------------------------------------------------------------------
    # Module
    # ------------------------------------------------------------------

    def _visit_module(self, node: Node) -> None:
        doc = first_docstring(node, self.source)
        name = self._module_qname.split(".")[-1] if self._module_qname else "__main__"

        module_sym = Symbol(
            id=self._file_symbol.id,   # module and file share the same id
            name=name,
            qualified_name=self._module_qname or "__main__",
            kind=SymbolKind.MODULE,
            language=Language.PYTHON,
            file_id=self.file_info.id,
            start_line=1,
            end_line=end_line(node),
            docstring=doc,
        )
        self.symbols.append(self._file_symbol)
        self._register(module_sym, skip_append=True)  # shares file_symbol's slot

        self._scope.push(module_sym)
        for child in node.children:
            self._visit_top_level(child)
        self._scope.pop()

    # ------------------------------------------------------------------
    # Top-level statement dispatch
    # ------------------------------------------------------------------

    def _visit_top_level(self, node: Node) -> None:
        t = node.type
        if t in ("import_statement", "import_from_statement"):
            self._handle_import(node)
        elif t == "class_definition":
            self._handle_class(node)
        elif t in ("function_definition", "decorated_definition"):
            self._handle_function_or_decorated(node, parent_sym=self._scope.current)
        elif t == "expression_statement":
            for child in node.children:
                if child.type == "assignment":
                    self._handle_assignment(child)
                    break
        elif t == "assignment":
            self._handle_assignment(node)

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _handle_import(self, node: Node) -> None:
        """Handle ``import x`` and ``from x import y`` statements."""
        loc = SourceLocation(file_id=self.file_info.id, line=start_line(node))
        file_sym = self._file_symbol

        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    self._emit_import(file_sym, node_text(child, self.source), loc)
                elif child.type == "aliased_import":
                    for sub in child.children:
                        if sub.type == "dotted_name":
                            self._emit_import(file_sym, node_text(sub, self.source), loc)
                            break

        elif node.type == "import_from_statement":
            module_parts: list[str] = []
            imported_names: list[str] = []
            reading_module = True

            for child in node.children:
                if child.type in ("from", "import"):
                    if child.type == "import":
                        reading_module = False
                    continue
                if reading_module:
                    if child.type in ("dotted_name", "relative_import"):
                        module_parts.append(node_text(child, self.source))
                else:
                    if child.type == "dotted_name":
                        imported_names.append(node_text(child, self.source))
                    elif child.type == "aliased_import":
                        for sub in child.children:
                            if sub.type == "dotted_name":
                                imported_names.append(node_text(sub, self.source))
                                break
                    elif child.type == "wildcard_import":
                        imported_names.append("*")

            module_str = ".".join(module_parts)
            if imported_names:
                for name in imported_names:
                    full = (
                        f"{module_str}.{name}"
                        if module_str and name != "*"
                        else module_str or name
                    )
                    self._emit_import(file_sym, full, loc)
            elif module_str:
                self._emit_import(file_sym, module_str, loc)

    def _emit_import(self, source_sym: Symbol, target_name: str, loc: SourceLocation) -> None:
        self.relationships.append(
            Relationship(
                source_id=source_sym.id,
                target_id="",
                type=RelationshipType.IMPORTS,
                source_location=loc,
                resolution=ResolutionStatus.UNRESOLVED,
                metadata={"target_name": target_name},
            )
        )

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _handle_class(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = node_text(name_node, self.source)

        bases = self._extract_base_names(node)
        is_interface = bool(_ABC_BASES & set(bases) or _PROTOCOL_BASES & set(bases))
        kind = SymbolKind.INTERFACE if is_interface else SymbolKind.CLASS

        parent_sym = self._scope.current
        qname = f"{self._scope.qualified_prefix(self._module_qname)}.{name}"

        body_node = node.child_by_field_name("body")
        doc = first_docstring(body_node, self.source) if body_node else ""

        class_sym = Symbol(
            name=name,
            qualified_name=qname,
            kind=kind,
            language=Language.PYTHON,
            file_id=self.file_info.id,
            start_line=start_line(node),
            end_line=end_line(node),
            parent_id=parent_sym.id if parent_sym else None,
            docstring=doc,
        )
        self._register(class_sym)
        if parent_sym:
            self._emit_contains(parent_sym.id, class_sym.id, start_line(node))

        # Inheritance edges (unresolved at this stage)
        for base_name in bases:
            if base_name in _ABC_BASES | _PROTOCOL_BASES:
                continue
            rel_type = RelationshipType.IMPLEMENTS if is_interface else RelationshipType.EXTENDS
            self.relationships.append(
                Relationship(
                    source_id=class_sym.id,
                    target_id="",
                    type=rel_type,
                    resolution=ResolutionStatus.UNRESOLVED,
                    metadata={"target_name": base_name},
                )
            )

        if body_node:
            self._scope.push(class_sym)
            for child in body_node.children:
                ct = child.type
                if ct in ("function_definition", "decorated_definition"):
                    self._handle_function_or_decorated(child, parent_sym=class_sym)
                elif ct == "class_definition":
                    self._handle_class(child)
                elif ct in ("expression_statement", "assignment"):
                    self._handle_class_field(child, class_sym)
            self._scope.pop()

    def _extract_base_names(self, class_node: Node) -> list[str]:
        """Return simple base class names for a class_definition node."""
        bases: list[str] = []
        args_node = class_node.child_by_field_name("superclasses")
        if not args_node:
            return bases
        for child in args_node.children:
            if child.type in ("identifier", "attribute"):
                bases.append(node_text(child, self.source).split(".")[-1])
        return bases

    def _handle_class_field(self, node: Node, class_sym: Symbol) -> None:
        """Handle class-level assignments (FIELD / CONSTANT symbols)."""
        # Unwrap expression_statement if needed
        target_node = node
        if node.type == "expression_statement":
            for child in node.children:
                if child.type == "assignment":
                    target_node = child
                    break
            else:
                return

        for target_name in self._extract_assignment_targets(target_node):
            if not target_name.isidentifier():
                continue
            kind = SymbolKind.CONSTANT if target_name.isupper() else SymbolKind.FIELD
            sym = Symbol(
                name=target_name,
                qualified_name=f"{class_sym.qualified_name}.{target_name}",
                kind=kind,
                language=Language.PYTHON,
                file_id=self.file_info.id,
                start_line=start_line(node),
                end_line=end_line(node),
                parent_id=class_sym.id,
            )
            self._register(sym)
            self._emit_contains(class_sym.id, sym.id, start_line(node))

    # ------------------------------------------------------------------
    # Functions and methods
    # ------------------------------------------------------------------

    def _handle_function_or_decorated(
        self, node: Node, parent_sym: Symbol | None
    ) -> None:
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type == "function_definition":
                    self._handle_function(child, parent_sym)
                    return
                if child.type == "class_definition":
                    self._handle_class(child)
                    return
        else:
            self._handle_function(node, parent_sym)

    def _handle_function(self, node: Node, parent_sym: Symbol | None) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = node_text(name_node, self.source)

        is_method = parent_sym is not None and parent_sym.kind in (
            SymbolKind.CLASS,
            SymbolKind.INTERFACE,
        )
        kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION
        is_async = any(c.type == "async" for c in node.children)

        params_node = node.child_by_field_name("parameters")
        return_node = node.child_by_field_name("return_type")
        sig_parts = []
        if params_node:
            sig_parts.append(node_text(params_node, self.source))
        if return_node:
            sig_parts.append(f"-> {node_text(return_node, self.source)}")
        signature = " ".join(sig_parts)

        qname = f"{self._scope.qualified_prefix(self._module_qname)}.{name}"
        body_node = node.child_by_field_name("body")
        doc = first_docstring(body_node, self.source) if body_node else ""

        func_sym = Symbol(
            name=name,
            qualified_name=qname,
            kind=kind,
            language=Language.PYTHON,
            file_id=self.file_info.id,
            start_line=start_line(node),
            end_line=end_line(node),
            signature=signature,
            parent_id=parent_sym.id if parent_sym else self._file_symbol.id,
            docstring=doc,
            is_async=is_async,
        )
        self._register(func_sym)

        container = parent_sym or self._file_symbol
        self._emit_contains(container.id, func_sym.id, start_line(node))

        if body_node:
            self._scope.push(func_sym)
            self._collect_calls(body_node, func_sym)
            for child in body_node.children:
                if child.type in ("function_definition", "decorated_definition"):
                    self._handle_function_or_decorated(child, parent_sym=func_sym)
                elif child.type == "class_definition":
                    self._handle_class(child)
            self._scope.pop()

    # ------------------------------------------------------------------
    # Call sites
    # ------------------------------------------------------------------

    def _collect_calls(self, node: Node, caller_sym: Symbol) -> None:
        """Recursively find all call expressions within a function/method body."""
        if node.type == "call":
            self._handle_call(node, caller_sym)
        for child in node.children:
            if child.type not in ("function_definition", "class_definition", "decorated_definition"):
                self._collect_calls(child, caller_sym)

    def _handle_call(self, node: Node, caller_sym: Symbol) -> None:
        func_node = node.child_by_field_name("function")
        if not func_node:
            return

        call_text = node_text(func_node, self.source)
        callee_name = call_text.split(".")[-1]
        loc = SourceLocation(
            file_id=self.file_info.id,
            line=start_line(node),
            col=node.start_point[1],
        )

        target_id = ""
        resolution = ResolutionStatus.UNRESOLVED

        fqn_match = self._local_by_qname.get(call_text)
        name_match = self._local_by_name.get(callee_name)
        if fqn_match:
            target_id = fqn_match.id
            resolution = ResolutionStatus.RESOLVED
        elif name_match:
            target_id = name_match.id
            resolution = ResolutionStatus.PROBABLE

        self.relationships.append(
            Relationship(
                source_id=caller_sym.id,
                target_id=target_id,
                type=RelationshipType.CALLS,
                source_location=loc,
                resolution=resolution,
                metadata={"call_text": call_text, "callee_name": callee_name},
            )
        )

    # ------------------------------------------------------------------
    # Module-level assignments
    # ------------------------------------------------------------------

    def _handle_assignment(self, node: Node) -> None:
        """Handle module-level VARIABLE / CONSTANT symbols."""
        for target_name in self._extract_assignment_targets(node):
            if not target_name.isidentifier():
                continue
            kind = SymbolKind.CONSTANT if target_name.isupper() else SymbolKind.VARIABLE
            qname = f"{self._module_qname}.{target_name}" if self._module_qname else target_name
            sym = Symbol(
                name=target_name,
                qualified_name=qname,
                kind=kind,
                language=Language.PYTHON,
                file_id=self.file_info.id,
                start_line=start_line(node),
                end_line=end_line(node),
                parent_id=self._file_symbol.id,
            )
            self._register(sym)
            self._emit_contains(self._file_symbol.id, sym.id, start_line(node))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _register(self, sym: Symbol, skip_append: bool = False) -> None:
        if not skip_append:
            self.symbols.append(sym)
        self._local_by_name[sym.name] = sym
        self._local_by_qname[sym.qualified_name] = sym

    def _emit_contains(self, source_id: str, target_id: str, line: int) -> None:
        self.relationships.append(
            Relationship(
                source_id=source_id,
                target_id=target_id,
                type=RelationshipType.CONTAINS,
                source_location=SourceLocation(file_id=self.file_info.id, line=line),
            )
        )

    def _make_file_symbol(self) -> Symbol:
        return Symbol(
            name=Path(self.file_info.path).name,
            qualified_name=self.file_info.path,
            kind=SymbolKind.FILE,
            language=Language.PYTHON,
            file_id=self.file_info.id,
            start_line=1,
            end_line=1,
        )

    @staticmethod
    def _extract_assignment_targets(node: Node) -> list[str]:
        targets: list[str] = []
        for child in node.children:
            if child.type == "identifier":
                text = child.text
                if text:
                    targets.append(text.decode("utf-8", errors="replace"))
            elif child.type == "pattern_list":
                for sub in child.children:
                    if sub.type == "identifier" and sub.text:
                        targets.append(sub.text.decode("utf-8", errors="replace"))
        return [t for t in targets if t]
