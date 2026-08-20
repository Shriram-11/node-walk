"""
Scope stack — tracks symbol nesting during AST traversal.

Kept separate from the visitor so the visitor module stays focused
on AST logic, not bookkeeping.
"""

from __future__ import annotations

from node_walk.ir.models import Symbol


class Scope:
    """
    Tracks the current nesting of symbols during AST traversal.

    The scope stack mirrors the Python scope rules: module → class →
    method/function → nested function. Push on entry, pop on exit.
    """

    def __init__(self) -> None:
        self._stack: list[Symbol] = []

    def push(self, sym: Symbol) -> None:
        self._stack.append(sym)

    def pop(self) -> Symbol | None:
        return self._stack.pop() if self._stack else None

    @property
    def current(self) -> Symbol | None:
        """The innermost enclosing symbol, or None at module top-level."""
        return self._stack[-1] if self._stack else None

    def qualified_prefix(self, module_qname: str) -> str:
        """
        Build the dotted qualified-name prefix for a new symbol.

        Combines the module qualified name with all symbols currently
        on the stack, e.g. "myapp.services.UserService".
        """
        parts = [module_qname] if module_qname else []
        parts.extend(s.name for s in self._stack)
        return ".".join(parts)
