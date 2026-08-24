"""
PythonAnalyzer — thin adapter that wires Tree-sitter parsing to the visitor.

This module intentionally contains very little logic. Its job is to:
  1. Parse source text into a Tree-sitter syntax tree.
  2. Hand the tree to SymbolCollector.
  3. Return the collected AnalysisResult.

All symbol extraction and relationship logic lives in visitor.py.
"""

from __future__ import annotations

from node_walk.analysis.base import LanguageAnalyzer
from node_walk.analysis.python.visitor import SymbolCollector, _PARSER
from node_walk.ir.enums import Language
from node_walk.ir.models import AnalysisResult, FileInfo


class PythonAnalyzer(LanguageAnalyzer):
    """
    Tree-sitter-based Python language adapter.

    Thread-safe: the parser is a module-level singleton and parse()
    returns a new tree each call.
    """

    @property
    def supported_languages(self) -> list[Language]:
        return [Language.PYTHON]

    def analyze(self, file_info: FileInfo, source: str) -> AnalysisResult:
        """Parse *source* and return all extracted symbols and relationships."""
        source_bytes = source.encode("utf-8")
        tree = _PARSER.parse(source_bytes)

        collector = SymbolCollector(file_info, source_bytes)
        collector.visit(tree.root_node)

        return AnalysisResult(
            file=file_info,
            symbols=collector.symbols,
            relationships=collector.relationships,
            relationship_facts=collector.relationship_facts,
        )
