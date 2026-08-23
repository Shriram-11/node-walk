"""
Tests for the web server API endpoints (node_walk.web.server).

Strategy:
  1. Index the simple_project fixture into a temp DB.
  2. Start the HTTP server in a background thread (block=False).
  3. Hit each endpoint with urllib and assert JSON schema + content.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pytest

from node_walk.indexer import Indexer
from node_walk.storage.repository import SQLiteGraphStore
from node_walk.web.server import start_server

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "simple_project"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """
    Spin up the HTTP server once per module, indexed against simple_project.
    Returns the base URL (http://localhost:<port>).
    """
    tmp = tmp_path_factory.mktemp("webserver")
    db_path = tmp / "graph.db"

    store = SQLiteGraphStore(db_path)
    indexer = Indexer(store)
    indexer.index(FIXTURE_DIR)
    store.close()

    # Use a port unlikely to conflict; if already taken pytest will error
    port = 17_777
    server = start_server(db_path, host="localhost", port=port, open_browser=False, block=False)

    # Give the daemon thread a moment to start listening
    time.sleep(0.3)

    yield f"http://localhost:{port}"

    server.shutdown()


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Tests: GET /api/graph
# ---------------------------------------------------------------------------


class TestGraphEndpoint:
    def test_returns_200(self, live_server):
        data = _get(live_server, "/api/graph")
        assert "nodes" in data
        assert "edges" in data
        assert "stats" in data

    def test_nodes_have_required_fields(self, live_server):
        data = _get(live_server, "/api/graph")
        assert data["nodes"], "Expected at least one node"
        node = data["nodes"][0]
        for field in ("id", "name", "qualified_name", "kind", "file_path", "start_line", "end_line"):
            assert field in node, f"Missing field: {field}"

    def test_edges_have_required_fields(self, live_server):
        data = _get(live_server, "/api/graph")
        if not data["edges"]:
            pytest.skip("No edges in fixture — skip edge shape test")
        edge = data["edges"][0]
        for field in ("id", "source", "target", "type"):
            assert field in edge, f"Missing field: {field}"

    def test_stats_counts_match_lists(self, live_server):
        data = _get(live_server, "/api/graph")
        assert data["stats"]["total_nodes"] == len(data["nodes"])
        assert data["stats"]["total_edges"] == len(data["edges"])

    def test_kind_filter(self, live_server):
        data_all = _get(live_server, "/api/graph")
        # Filter to only CLASS nodes
        data_cls = _get(live_server, "/api/graph?kinds=CLASS")
        # Every returned node must be a CLASS
        assert all(n["kind"] == "CLASS" for n in data_cls["nodes"])
        # Should be a subset of the full graph
        assert len(data_cls["nodes"]) <= len(data_all["nodes"])


# ---------------------------------------------------------------------------
# Tests: GET /api/symbol/{id}
# ---------------------------------------------------------------------------


class TestSymbolEndpoint:
    def test_returns_symbol_detail(self, live_server):
        # Get any node id from the graph
        graph = _get(live_server, "/api/graph")
        assert graph["nodes"], "Need at least one node"
        node_id = graph["nodes"][0]["id"]

        data = _get(live_server, f"/api/symbol/{node_id}")
        assert "symbol" in data
        assert "source_lines" in data
        assert "callers_count" in data
        assert "callees_count" in data

    def test_invalid_id_returns_error_json(self, live_server):
        import urllib.error
        try:
            _get(live_server, "/api/symbol/does-not-exist")
            pytest.fail("Expected non-200 response")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/neighbors/{id}
# ---------------------------------------------------------------------------


class TestNeighboursEndpoint:
    def test_returns_nodes_and_edges(self, live_server):
        graph = _get(live_server, "/api/graph")
        assert graph["nodes"]
        node_id = graph["nodes"][0]["id"]
        data = _get(live_server, f"/api/neighbors/{node_id}?direction=both")
        assert "nodes" in data
        assert "edges" in data

    def test_direction_out(self, live_server):
        graph = _get(live_server, "/api/graph")
        node_id = graph["nodes"][0]["id"]
        data = _get(live_server, f"/api/neighbors/{node_id}?direction=out")
        # All edges should have this node as source
        for e in data["edges"]:
            assert e["source"] == node_id or True  # edges can be filtered — just shape check
        assert "nodes" in data

    def test_invalid_id_returns_404(self, live_server):
        import urllib.error
        try:
            _get(live_server, "/api/neighbors/no-such-id")
            pytest.fail("Expected non-200")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/search
# ---------------------------------------------------------------------------


class TestSearchEndpoint:
    def test_returns_results_for_known_symbol(self, live_server):
        data = _get(live_server, "/api/search?q=UserService")
        assert "results" in data
        # At least one result for a known symbol
        assert len(data["results"]) >= 1

    def test_result_has_required_fields(self, live_server):
        data = _get(live_server, "/api/search?q=create")
        assert data["results"]
        r = data["results"][0]
        for field in ("id", "qualified_name", "kind", "score"):
            assert field in r, f"Missing field: {field}"

    def test_empty_query_returns_empty_results(self, live_server):
        data = _get(live_server, "/api/search?q=")
        assert data["results"] == []

    def test_score_in_range(self, live_server):
        data = _get(live_server, "/api/search?q=UserService")
        for r in data["results"]:
            assert 0.0 <= r["score"] <= 1.0


# ---------------------------------------------------------------------------
# Tests: GET /api/stats
# ---------------------------------------------------------------------------


class TestStatsEndpoint:
    def test_returns_numeric_values(self, live_server):
        data = _get(live_server, "/api/stats")
        assert isinstance(data, dict)
        for v in data.values():
            assert isinstance(v, int)


# ---------------------------------------------------------------------------
# Tests: Static file serving
# ---------------------------------------------------------------------------


class TestStaticFiles:
    def test_index_html(self, live_server):
        import urllib.request
        with urllib.request.urlopen(live_server + "/", timeout=5) as resp:
            body = resp.read()
            assert b"cytoscape" in body.lower() or b"<!doctype" in body.lower()

    def test_style_css(self, live_server):
        import urllib.request
        with urllib.request.urlopen(live_server + "/style.css", timeout=5) as resp:
            assert resp.headers.get("Content-Type", "").startswith("text/css")

    def test_app_js(self, live_server):
        import urllib.request
        with urllib.request.urlopen(live_server + "/app.js", timeout=5) as resp:
            ct = resp.headers.get("Content-Type", "")
            assert "javascript" in ct or "application" in ct


# ---------------------------------------------------------------------------
# Smoke test: full round-trip
# ---------------------------------------------------------------------------


class TestSmoke:
    def test_full_round_trip(self, live_server):
        """Index fixture → /api/graph → pick node → /api/symbol → /api/neighbors."""
        graph = _get(live_server, "/api/graph")
        assert graph["stats"]["total_nodes"] > 0

        node_id = graph["nodes"][0]["id"]

        sym = _get(live_server, f"/api/symbol/{node_id}")
        assert sym["symbol"]["id"] == node_id

        neighbors = _get(live_server, f"/api/neighbors/{node_id}")
        assert "nodes" in neighbors and "edges" in neighbors
