"""
node_walk.analysis.python_analyzer — backward-compatibility shim.

The Python analyzer now lives in the node_walk.analysis.python sub-package:
  - node_walk.analysis.python.analyzer  (PythonAnalyzer)
  - node_walk.analysis.python.visitor   (SymbolCollector + helpers)
  - node_walk.analysis.python.scope     (Scope)

This module re-exports PythonAnalyzer so existing imports keep working.
"""

from node_walk.analysis.python.analyzer import PythonAnalyzer

__all__ = ["PythonAnalyzer"]
