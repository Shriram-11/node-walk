"""
codegraph.analysis.python_analyzer — backward-compatibility shim.

The Python analyzer now lives in the codegraph.analysis.python sub-package:
  - codegraph.analysis.python.analyzer  (PythonAnalyzer)
  - codegraph.analysis.python.visitor   (SymbolCollector + helpers)
  - codegraph.analysis.python.scope     (Scope)

This module re-exports PythonAnalyzer so existing imports keep working.
"""

from codegraph.analysis.python.analyzer import PythonAnalyzer

__all__ = ["PythonAnalyzer"]
