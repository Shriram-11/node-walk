"""
SQLite schema for CodeGraph.

Tables:
  files         — one row per discovered source file
  symbols       — one row per code symbol (class, function, …)
  relationships — one row per semantic edge in the graph

The schema is created idempotently using CREATE TABLE IF NOT EXISTS,
so it is safe to call initialize() multiple times.
"""

from __future__ import annotations

import sqlite3

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_FILES = """
CREATE TABLE IF NOT EXISTS files (
    id           TEXT PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    language     TEXT NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    indexed_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_SYMBOLS = """
CREATE TABLE IF NOT EXISTS symbols (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind           TEXT NOT NULL,
    language       TEXT NOT NULL,
    file_id        TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    signature      TEXT NOT NULL DEFAULT '',
    parent_id      TEXT REFERENCES symbols(id) ON DELETE SET NULL,
    docstring      TEXT NOT NULL DEFAULT '',
    is_async       INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_RELATIONSHIPS = """
CREATE TABLE IF NOT EXISTS relationships (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    target_id       TEXT NOT NULL DEFAULT '',
    type            TEXT NOT NULL,
    source_file_id  TEXT,
    source_line     INTEGER,
    source_col      INTEGER,
    resolution      TEXT NOT NULL DEFAULT 'resolved',
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);
"""

# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_symbols_name          ON symbols(name);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_qname         ON symbols(qualified_name);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_file_id       ON symbols(file_id);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_kind          ON symbols(kind);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_parent_id     ON symbols(parent_id);",
    "CREATE INDEX IF NOT EXISTS idx_rels_source_id        ON relationships(source_id);",
    "CREATE INDEX IF NOT EXISTS idx_rels_target_id        ON relationships(target_id);",
    "CREATE INDEX IF NOT EXISTS idx_rels_type             ON relationships(type);",
    "CREATE INDEX IF NOT EXISTS idx_rels_source_type      ON relationships(source_id, type);",
    "CREATE INDEX IF NOT EXISTS idx_rels_target_type      ON relationships(target_id, type);",
]

# ---------------------------------------------------------------------------
# Schema metadata
# ---------------------------------------------------------------------------

_CREATE_META = """
CREATE TABLE IF NOT EXISTS node_walk_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SCHEMA_VERSION = "1"


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def initialize(conn: sqlite3.Connection) -> None:
    """
    Create all tables and indexes in *conn* if they do not already exist.
    Safe to call repeatedly; never drops data.
    """
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    conn.execute(_CREATE_FILES)
    conn.execute(_CREATE_SYMBOLS)
    conn.execute(_CREATE_RELATIONSHIPS)
    conn.execute(_CREATE_META)

    for idx_sql in _CREATE_INDEXES:
        conn.execute(idx_sql)

    conn.execute(
        "INSERT OR IGNORE INTO node_walk_meta(key, value) VALUES (?, ?)",
        ("schema_version", _SCHEMA_VERSION),
    )
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> str | None:
    """Return the stored schema version, or None if the meta table is missing/empty."""
    try:
        row = conn.execute(
            "SELECT value FROM node_walk_meta WHERE key = 'schema_version'"
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
