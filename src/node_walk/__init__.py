"""node-walk — Semantic code intelligence for humans and LLMs."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("node-walk")
except Exception:  # pragma: no cover
    __version__ = "0.0.0.dev"
