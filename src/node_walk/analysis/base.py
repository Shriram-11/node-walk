"""
Language analysis base — adapter interface and file discovery.

All language-specific analyzers must implement LanguageAnalyzer.
FileDiscovery handles walking a repository and routing files to
the correct analyzer based on extension / content sniffing.
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from pathlib import Path

from node_walk.ir.models import AnalysisResult, FileInfo, Language

# ---------------------------------------------------------------------------
# File extension → language mapping
# ---------------------------------------------------------------------------

_EXTENSION_MAP: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
}

# Directories that are never useful to index
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "dist",
        "build",
        ".node_walk",
        ".eggs",
        "*.egg-info",
    }
)

# Files that are never useful to index
_SKIP_FILE_PATTERNS: frozenset[str] = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
    }
)


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.endswith(".egg-info")


def _compute_hash(path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Language analyzer interface
# ---------------------------------------------------------------------------


class LanguageAnalyzer(ABC):
    """
    Abstract base for language-specific analyzers.

    Each implementation parses source files for one language and
    returns an AnalysisResult containing the discovered symbols and
    relationships (expressed as Code IR objects).
    """

    @property
    @abstractmethod
    def supported_languages(self) -> list[Language]:
        """Languages this analyzer handles."""
        ...

    @abstractmethod
    def analyze(self, file_info: FileInfo, source: str) -> AnalysisResult:
        """
        Parse *source* (the full text of *file_info.path*) and return
        an AnalysisResult with all extracted symbols and relationships.

        The analyzer must NOT perform I/O; the caller reads the file and
        passes the text in. This makes analyzers trivially testable.
        """
        ...


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


class FileDiscovery:
    """
    Walks a repository root, detects file languages, and produces FileInfo
    objects ready for analysis.

    Does not perform analysis itself — that is delegated to analyzers.
    """

    def __init__(self, root: str | Path, include_languages: list[Language] | None = None):
        self.root = Path(root).resolve()
        self._include = set(include_languages) if include_languages else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(self) -> list[tuple[FileInfo, str]]:
        """
        Walk the repository and return a list of (FileInfo, source_text)
        tuples for every file that should be analyzed.

        Skips binary files and directories in the skip list.
        Returns files in a deterministic order (sorted by path).
        """
        results: list[tuple[FileInfo, str]] = []

        for dirpath, dirnames, filenames in os.walk(self.root):
            # Prune directories in-place so os.walk skips them
            dirnames[:] = sorted(
                d for d in dirnames if not _should_skip_dir(d)
            )

            for filename in sorted(filenames):
                if filename in _SKIP_FILE_PATTERNS:
                    continue

                full_path = Path(dirpath) / filename
                language = self._detect_language(full_path)

                if language == Language.UNKNOWN:
                    continue
                if self._include and language not in self._include:
                    continue

                source = self._read_safe(full_path)
                if source is None:
                    continue  # binary or unreadable

                rel_path = str(full_path.relative_to(self.root))
                file_info = FileInfo(
                    path=str(full_path),
                    language=language,
                    content_hash=_compute_hash(full_path),
                    size_bytes=full_path.stat().st_size,
                )
                results.append((file_info, source))

        return results

    def discover_file(self, path: str | Path) -> tuple[FileInfo, str] | None:
        """Discover a single file. Returns None if unsupported or unreadable."""
        full_path = Path(path).resolve()
        language = self._detect_language(full_path)
        if language == Language.UNKNOWN:
            return None
        source = self._read_safe(full_path)
        if source is None:
            return None
        file_info = FileInfo(
            path=str(full_path),
            language=language,
            content_hash=_compute_hash(full_path),
            size_bytes=full_path.stat().st_size,
        )
        return file_info, source

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_language(path: Path) -> Language:
        suffix = path.suffix.lower()
        return _EXTENSION_MAP.get(suffix, Language.UNKNOWN)

    @staticmethod
    def _read_safe(path: Path) -> str | None:
        """Read a text file. Returns None if it appears binary or cannot be read."""
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
            return text
        except (UnicodeDecodeError, OSError):
            return None
