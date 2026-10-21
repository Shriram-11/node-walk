"""
Tests for CLI --json machine-readable output mode and error handling.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from node_walk.cli.main import app
from node_walk.indexer import Indexer
from node_walk.storage.sqlite_store import SQLiteGraphStore

runner = CliRunner()


@pytest.fixture(scope="module")
def indexed_repo(tmp_path_factory) -> Path:
    """Create a mini Python repository with classes, functions, calls, and index it."""
    repo_dir = tmp_path_factory.mktemp("test_cli_repo")
    src_dir = repo_dir / "pkg"
    src_dir.mkdir(parents=True)

    math_mod = src_dir / "math_utils.py"
    math_mod.write_text(
        'def add(a: int, b: int) -> int:\n'
        '    """Add two numbers together."""\n'
        '    return a + b\n\n'
        'def double_add(x: int) -> int:\n'
        '    return add(x, x)\n',
        encoding="utf-8",
    )

    app_mod = src_dir / "app.py"
    app_mod.write_text(
        'from pkg.math_utils import add, double_add\n\n'
        'class Calculator:\n'
        '    """Simple calculator."""\n'
        '    def calculate(self, val: int) -> int:\n'
        '        return double_add(val)\n\n'
        'class AdvancedCalculator(Calculator):\n'
        '    """Advanced subclass."""\n'
        '    pass\n',
        encoding="utf-8",
    )

    db_path = repo_dir / ".node_walk" / "graph.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteGraphStore(db_path)
    indexer = Indexer(store)
    indexer.index(repo_dir, mode="full")

    return repo_dir


def _check_envelope(data: dict, expected_command: str) -> None:
    """Assert standard envelope properties."""
    assert "ok" in data
    assert "command" in data
    assert "data" in data
    assert "error" in data
    assert data["command"] == expected_command


def test_index_json(tmp_path: Path):
    """Test `node-walk --json index <path>`."""
    proj = tmp_path / "mini_proj"
    proj.mkdir()
    (proj / "main.py").write_text("def hello(): pass\n", encoding="utf-8")

    result = runner.invoke(app, ["--json", "index", str(proj)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "index")
    assert data["ok"] is True
    assert data["data"]["forward"]["symbols_extracted"] >= 1
    assert data["data"]["forward"]["files_discovered"] >= 1


def test_find_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "find", "Calculator"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "find")
    assert data["ok"] is True
    assert data["data"]["count"] >= 1
    sym = data["data"]["results"][0]["symbol"]
    assert sym["name"] == "Calculator"
    assert sym["kind"] == "CLASS"
    assert "pkg/app.py" in sym["file"].replace("\\", "/")


def test_definition_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "definition", "add"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "definition")
    assert data["ok"] is True
    sym = data["data"]["symbol"]
    assert sym["name"] == "add"
    assert sym["kind"] == "FUNCTION"
    assert sym["docstring"] == "Add two numbers together."


def test_callers_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "callers", "add"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "callers")
    assert data["ok"] is True
    # double_add calls add
    names = [r["symbol"]["name"] for r in data["data"]["results"]]
    assert "double_add" in names


def test_callees_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "callees", "double_add"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "callees")
    assert data["ok"] is True
    names = [r["symbol"]["name"] for r in data["data"]["results"]]
    assert "add" in names


def test_refs_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "refs", "add"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "refs")
    assert data["ok"] is True
    assert isinstance(data["data"]["results"], list)


def test_implementations_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "implementations", "Calculator"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "implementations")
    assert data["ok"] is True
    names = [r["symbol"]["name"] for r in data["data"]["results"]]
    assert "AdvancedCalculator" in names


def test_imports_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "imports", "app.py"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "imports")
    assert data["ok"] is True
    assert isinstance(data["data"]["results"], list)


def test_source_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "source", "add"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "source")
    assert data["ok"] is True
    assert "def add(a: int, b: int) -> int:" in data["data"]["source"]
    assert data["data"]["start_line"] == 1


def test_stats_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "stats"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "stats")
    assert data["ok"] is True
    assert data["data"]["files"] >= 2
    assert data["data"]["symbols"] >= 4
    assert "CLASS" in data["data"]["symbols_by_kind"]


def test_trace_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "trace", "Calculator.calculate"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "trace")
    assert data["ok"] is True
    assert data["data"]["root"]["name"] == "calculate"
    assert isinstance(data["data"]["nodes"], list)
    assert isinstance(data["data"]["edges"], list)


def test_blast_radius_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "blast-radius", "add"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "blast-radius")
    assert data["ok"] is True
    assert data["data"]["direction"] == "in"
    assert isinstance(data["data"]["nodes"], list)


def test_graph_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "graph", "Calculator"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "graph")
    assert data["ok"] is True
    assert isinstance(data["data"]["nodes"], list)


def test_export_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "export"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    _check_envelope(data, "export")
    assert data["ok"] is True
    assert "files" in data["data"]
    assert "symbols" in data["data"]
    assert "relationships" in data["data"]


def test_no_index_error_json(tmp_path: Path, monkeypatch):
    """When no .node_walk/graph.db exists, return structured NO_INDEX error."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)

    result = runner.invoke(app, ["--json", "find", "anything"])
    assert result.exit_code != 0
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["command"] == "find"
    assert data["error"]["code"] == "NO_INDEX"
    assert "No .node_walk/graph.db found" in data["error"]["message"]


def test_symbol_not_found_error_json(indexed_repo: Path, monkeypatch):
    monkeypatch.chdir(indexed_repo)
    result = runner.invoke(app, ["--json", "definition", "NonExistentSymbol12345"])
    assert result.exit_code != 0
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["command"] == "definition"
    assert data["error"]["code"] == "SYMBOL_NOT_FOUND"
