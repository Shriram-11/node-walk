"""
node_walk.web.server — Embedded HTTP server for the traversable graph explorer.

Serves static files from this package directory and exposes JSON API
endpoints backed by QueryEngine + SQLiteGraphStore.

No external dependencies — uses Python's stdlib http.server only.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from node_walk.query.engine import QueryEngine
from node_walk.storage.sqlite_store import SQLiteGraphStore
from node_walk.ir.enums import FactStatus

# Directory where index.html / style.css / app.js live (same dir as this file)
_WEB_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

def _sym_to_dict(sym: Any, file_path: str = "") -> dict:
    """Convert a Symbol model to a plain dict suitable for JSON."""
    return {
        "id": sym.id,
        "name": sym.name,
        "qualified_name": sym.qualified_name,
        "kind": sym.kind.value,
        "file_path": file_path,
        "start_line": sym.start_line,
        "end_line": sym.end_line,
        "signature": sym.signature or "",
        "docstring": sym.docstring or "",
        "parent_id": sym.parent_id or "",
    }


def _rel_to_dict(rel: Any) -> dict:
    """Convert a Relationship model to a plain dict suitable for JSON."""
    return {
        "id": rel.id,
        "source": rel.source_id,
        "target": rel.target_id,
        "type": rel.type.value,
        "resolution": rel.resolution.value,
    }


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _GraphHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler.

    Attributes injected by the server after construction:
        engine   — QueryEngine
        store    — SQLiteGraphStore
    """

    # Suppress default request logging; use our own.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        try:
            if path == "/" or path == "/index.html":
                self._serve_static("index.html")
            elif path == "/style.css":
                self._serve_static("style.css")
            elif path == "/app.js":
                self._serve_static("app.js")
            elif path == "/api/graph":
                self._handle_graph(qs)
            elif path == "/api/stats":
                self._handle_stats()
            elif path == "/api/search":
                self._handle_search(qs)
            elif path.startswith("/api/symbol/"):
                sym_id = path.removeprefix("/api/symbol/")
                self._handle_symbol(sym_id)
            elif path.startswith("/api/neighbors/"):
                sym_id = path.removeprefix("/api/neighbors/")
                self._handle_neighbors(sym_id, qs)
            else:
                self._send_json({"error": "Not found"}, status=404)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, status=500)

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    def _serve_static(self, filename: str) -> None:
        fpath = _WEB_DIR / filename
        if not fpath.exists():
            self._send_json({"error": f"{filename} not found"}, status=404)
            return
        mime, _ = mimetypes.guess_type(filename)
        mime = mime or "application/octet-stream"
        data = fpath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------
    # API handlers
    # ------------------------------------------------------------------

    def _handle_graph(self, qs: dict) -> None:
        """GET /api/graph — full graph for initial render."""
        kinds_filter = set(qs.get("kinds", [""])[0].split(",")) if qs.get("kinds") else None
        rels_filter = set(qs.get("rels", [""])[0].split(",")) if qs.get("rels") else None

        # Build file_id → path cache
        file_cache: dict[str, str] = {f.id: f.path for f in self.server.store.get_all_files()}  # type: ignore[attr-defined]

        symbols = self.server.store.get_all_symbols()  # type: ignore[attr-defined]
        nodes = []
        for sym in symbols:
            if kinds_filter and sym.kind.value not in kinds_filter:
                continue
            nodes.append(_sym_to_dict(sym, file_cache.get(sym.file_id, "")))

        node_ids = {n["id"] for n in nodes}

        # Collect relationships
        seen_rel_ids: set[str] = set()
        edges = []
        for sym in symbols:
            if sym.id not in node_ids:
                continue
            rels = self.server.store.get_relationships_from(sym.id)  # type: ignore[attr-defined]
            for rel in rels:
                if rel.id in seen_rel_ids:
                    continue
                if not rel.target_id:
                    continue
                if rel.target_id not in node_ids:
                    continue
                if rels_filter and rel.type.value not in rels_filter:
                    continue
                seen_rel_ids.add(rel.id)
                edges.append(_rel_to_dict(rel))

        self._send_json({
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
            },
        })

    def _handle_symbol(self, sym_id: str) -> None:
        """GET /api/symbol/{id} — full detail for a single symbol."""
        sym = self.server.store.get_symbol(sym_id)  # type: ignore[attr-defined]
        if not sym:
            self._send_json({"error": "Symbol not found"}, status=404)
            return

        file_info = self.server.store.get_file(sym.file_id)  # type: ignore[attr-defined]
        file_path = file_info.path if file_info else ""

        # Source snippet
        source_lines: list[str] = []
        src = self.server.engine.get_source(sym_id)  # type: ignore[attr-defined]
        if src:
            source_lines = src.lines

        # Relationship metrics and lists
        inbound_rels = self.server.store.get_relationships_to(sym_id)  # type: ignore[attr-defined]
        outbound_rels = self.server.store.get_relationships_from(sym_id)  # type: ignore[attr-defined]

        # Gather file-specific unresolved facts
        unresolved_facts = [
            {
                "fact_type": f.fact_type.value,
                "raw_text": f.raw_text,
                "line": f.source_location.line if f.source_location else None
            }
            for f in self.server.store.get_relationship_facts(status=FactStatus.UNRESOLVED)  # type: ignore[attr-defined]
            if f.source_symbol_id == sym.id
        ]

        def format_rel(rel, is_inbound):
            other_id = rel.source_id if is_inbound else rel.target_id
            other_sym = self.server.store.get_symbol(other_id)  # type: ignore[attr-defined]
            return {
                "id": other_id,
                "name": other_sym.name if other_sym else "Unknown",
                "kind": other_sym.kind.value if other_sym else "Unknown",
                "rel_type": rel.type.value,
                "resolution": rel.resolution.value,
            }

        callers = [format_rel(r, True) for r in inbound_rels]
        callees = [format_rel(r, False) for r in outbound_rels]

        # Breakdowns
        in_counts: dict[str, int] = {}
        for r in inbound_rels:
            in_counts[r.type.value] = in_counts.get(r.type.value, 0) + 1
            
        out_counts: dict[str, int] = {}
        for r in outbound_rels:
            out_counts[r.type.value] = out_counts.get(r.type.value, 0) + 1

        self._send_json({
            "symbol": _sym_to_dict(sym, file_path),
            "source_lines": source_lines,
            "counts": {
                "inbound": in_counts,
                "outbound": out_counts,
            },
            "callers": callers,
            "callees": callees,
            "unresolved_facts": unresolved_facts,
        })

    def _handle_neighbors(self, sym_id: str, qs: dict) -> None:
        """GET /api/neighbors/{id} — 1-hop neighbors for lazy expansion."""
        sym = self.server.store.get_symbol(sym_id)  # type: ignore[attr-defined]
        if not sym:
            self._send_json({"error": "Symbol not found"}, status=404)
            return

        direction = (qs.get("direction", ["both"])[0] or "both").lower()
        if direction not in ("out", "in", "both"):
            direction = "both"

        rels_filter_set = set(qs.get("rels", [""])[0].split(",")) if qs.get("rels") else None

        file_cache: dict[str, str] = {f.id: f.path for f in self.server.store.get_all_files()}  # type: ignore[attr-defined]

        neighbor_ids: set[str] = set()
        edges: list[dict] = []

        def process_rels(rels: list, is_outgoing: bool) -> None:
            for rel in rels:
                if rels_filter_set and rel.type.value not in rels_filter_set:
                    continue
                other_id = rel.target_id if is_outgoing else rel.source_id
                if not other_id:
                    continue
                neighbor_ids.add(other_id)
                edges.append(_rel_to_dict(rel))

        if direction in ("out", "both"):
            process_rels(self.server.store.get_relationships_from(sym_id), True)  # type: ignore[attr-defined]
        if direction in ("in", "both"):
            process_rels(self.server.store.get_relationships_to(sym_id), False)  # type: ignore[attr-defined]

        nodes = []
        for nid in neighbor_ids:
            n = self.server.store.get_symbol(nid)  # type: ignore[attr-defined]
            if n:
                nodes.append(_sym_to_dict(n, file_cache.get(n.file_id, "")))

        self._send_json({"nodes": nodes, "edges": edges})

    def _handle_search(self, qs: dict) -> None:
        """GET /api/search?q=<query> — fuzzy symbol search."""
        q = (qs.get("q", [""])[0] or "").strip()
        if not q:
            self._send_json({"results": []})
            return

        matches = self.server.engine.find_symbol(q, limit=20)  # type: ignore[attr-defined]
        results = [
            {
                "id": m.symbol.id,
                "qualified_name": m.symbol.qualified_name,
                "kind": m.symbol.kind.value,
                "score": m.score,
            }
            for m in matches
        ]
        self._send_json({"results": results})

    def _handle_stats(self) -> None:
        """GET /api/stats — graph statistics."""
        data = self.server.engine.stats()  # type: ignore[attr-defined]
        self._send_json(data)

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

class GraphHTTPServer(HTTPServer):
    """HTTPServer subclass that carries shared state (engine + store)."""

    def __init__(self, store: SQLiteGraphStore, engine: QueryEngine, host: str, port: int) -> None:
        self.store = store
        self.engine = engine
        super().__init__((host, port), _GraphHandler)


def start_server(
    db_path: Path,
    *,
    host: str = "localhost",
    port: int = 7777,
    open_browser: bool = True,
    block: bool = True,
) -> GraphHTTPServer:
    """
    Open the graph store at *db_path*, start the HTTP server on *host:port*.

    If *open_browser* is True, opens ``http://{host}:{port}`` in the default
    browser after a brief delay so the server has time to bind.

    If *block* is True (default), this call never returns (use Ctrl-C to stop).
    If *block* is False, the server runs in a daemon thread and the function
    returns the server instance (useful for tests).
    """
    store = SQLiteGraphStore(db_path)
    engine = QueryEngine(store)
    server = GraphHTTPServer(store, engine, host, port)

    if open_browser:
        import webbrowser
        url = f"http://{host}:{port}"

        def _open() -> None:
            import time
            time.sleep(0.8)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    if block:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            store.close()
    else:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

    return server
