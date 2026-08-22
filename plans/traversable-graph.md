# Traversable Graph — Feature Plan

**Interactive browser-based code graph visualization for node-walk.**

Light-touch MVP first, iterate from user feedback.

---

## 1. Goal

Let a developer **see and click through** their code graph in a browser — starting from any symbol, expanding neighbors on click, reading source on hover — rather than only navigating via terminal commands.

The graph is **local-only**: a tiny embedded HTTP server reads the same `.node_walk/graph.db` that the CLI already produces. No cloud, no build step, no bundler.

---

## 2. What the MVP Does

| Capability | Detail |
|---|---|
| **Launch from CLI** | `node-walk serve` starts a local HTTP server (default `localhost:7777`) and opens the browser. |
| **Full graph overview** | On load, render all symbols + relationships from the indexed repo. Nodes are colored by `SymbolKind`, edges by `RelationshipType`. |
| **Click to focus** | Click a node → highlight it and its direct neighbors, dim everything else. |
| **Expand / collapse** | Double-click a node → run a 1-hop walk from that node and add newly discovered neighbors to the canvas (lazy expansion). |
| **Node detail panel** | Select a node → side panel shows: qualified name, kind, file, line range, signature, docstring preview. |
| **Search bar** | Type a symbol name → fuzzy match (reuses `find_symbol` logic server-side) → center the graph on the result. |
| **Layout** | Force-directed layout (Cytoscape.js `cose`) with manual drag. |
| **Edge labels** | Edges show relationship type (`CALLS`, `IMPORTS`, `EXTENDS`, etc.). |
| **Filter panel** | Checkboxes to show/hide by SymbolKind and RelationshipType. |

### What the MVP Does NOT Do

- No live re-indexing (user must `node-walk index` first).
- No edit-in-place or "go to file in editor" integration.
- No multi-repo or remote graphs.
- No persistence of layout state between sessions.

---

## 3. Architecture

```
┌────────────────────────────────┐
│           Browser              │
│  ┌──────────────────────────┐  │
│  │  Single-Page HTML/JS/CSS │  │
│  │  Cytoscape.js (CDN)      │  │
│  └──────────┬───────────────┘  │
│             │ fetch /api/*     │
└─────────────┼──────────────────┘
              │ HTTP (localhost)
┌─────────────┼──────────────────┐
│  node-walk serve               │
│  ┌──────────┴───────────────┐  │
│  │  Python HTTP server      │  │
│  │  (aiohttp or stdlib)     │  │
│  │  JSON API endpoints      │  │
│  └──────────┬───────────────┘  │
│             │                  │
│  ┌──────────┴───────────────┐  │
│  │  QueryEngine             │  │
│  │  SQLiteGraphStore        │  │
│  │  (.node_walk/graph.db)   │  │
│  └──────────────────────────┘  │
└────────────────────────────────┘
```

**Zero new Python dependencies for MVP.** The HTTP server uses Python's built-in `http.server` module. The frontend loads Cytoscape.js from CDN. All HTML/CSS/JS is served from a single bundled directory inside the package.

---

## 4. Backend — API Endpoints

All endpoints return JSON. The server is a thin wrapper around `QueryEngine`.

### `GET /api/graph`

Returns the full graph (all symbols + relationships) for initial render.

```json
{
  "nodes": [
    {
      "id": "uuid",
      "name": "chat",
      "qualified_name": "jarvis.model.base.ModelAdapter.chat",
      "kind": "METHOD",
      "file_path": "src/jarvis/model/base.py",
      "start_line": 42,
      "end_line": 58,
      "signature": "(self, messages: list[ChatMessage]) -> str",
      "docstring": "Send messages to the model and return response.",
      "parent_id": "uuid-of-ModelAdapter"
    }
  ],
  "edges": [
    {
      "id": "uuid",
      "source": "source-symbol-uuid",
      "target": "target-symbol-uuid",
      "type": "CALLS",
      "resolution": "resolved"
    }
  ],
  "stats": {
    "total_nodes": 127,
    "total_edges": 304
  }
}
```

**Query params:**
- `?kinds=CLASS,METHOD,FUNCTION` — filter node kinds (default: all)
- `?rels=CALLS,IMPORTS,EXTENDS` — filter edge types (default: all)

### `GET /api/symbol/{id}`

Returns full detail for a single symbol (definition, source snippet).

```json
{
  "symbol": { /* same shape as node above */ },
  "source_lines": ["def chat(self, ...):", "    ..."],
  "callers_count": 3,
  "callees_count": 5,
  "refs_count": 8
}
```

### `GET /api/neighbors/{id}`

Returns 1-hop neighbors of a symbol (for lazy expand on double-click).

```json
{
  "nodes": [ /* new nodes not already on canvas */ ],
  "edges": [ /* edges connecting to/from the expanded node */ ]
}
```

**Query params:**
- `?direction=out|in|both` (default: `both`)
- `?rels=CALLS,IMPORTS` (default: all)

### `GET /api/search?q=<query>`

Fuzzy symbol search. Returns top matches with scores.

```json
{
  "results": [
    {
      "id": "uuid",
      "qualified_name": "pkg.ModelAdapter.chat",
      "kind": "METHOD",
      "score": 0.95
    }
  ]
}
```

### `GET /api/stats`

Returns graph statistics (reuses `QueryEngine.stats()`).

---

## 5. Frontend — Single-Page App

### Technology

| Component | Choice | Rationale |
|---|---|---|
| Graph rendering | **Cytoscape.js** (CDN) | Mature, performant, supports cola/cose layouts, built-in selection/expansion APIs. |
| Styling | Vanilla CSS | Zero build step. Dark theme by default. |
| HTTP | `fetch()` | No framework needed. |

### Files

All frontend files live under `src/node_walk/web/`:

```
src/node_walk/web/
├── index.html       # Single-page shell
├── style.css        # Dark theme, layout, panels
└── app.js           # Cytoscape init, API calls, interaction handlers
```

### UI Layout

```
┌───────────────────────────────────────────────────────────────┐
│  ┌─────────────────────────────────────────┐  ┌───────────┐  │
│  │           Search bar                    │  │  Filters  │  │
│  └─────────────────────────────────────────┘  └───────────┘  │
│  ┌─────────────────────────────────────────┐  ┌───────────┐  │
│  │                                         │  │  Detail   │  │
│  │                                         │  │  Panel    │  │
│  │           Cytoscape Canvas              │  │           │  │
│  │           (interactive graph)           │  │  - Name   │  │
│  │                                         │  │  - Kind   │  │
│  │                                         │  │  - File   │  │
│  │                                         │  │  - Lines  │  │
│  │                                         │  │  - Source  │  │
│  └─────────────────────────────────────────┘  └───────────┘  │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Status bar: node count · edge count · index path       │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

### Interactions

| User Action | Behavior |
|---|---|
| **Click node** | Select → highlight it + direct neighbors. Show detail panel. |
| **Double-click node** | Lazy expand: fetch `/api/neighbors/{id}`, add new nodes/edges, re-layout locally. |
| **Right-click node** | Context menu: "Trace from here", "Blast radius", "Hide node", "Focus subtree". |
| **Hover node** | Tooltip with qualified name + kind. |
| **Hover edge** | Tooltip with relationship type. |
| **Search** | Debounced keystroke → `GET /api/search?q=...` → highlight + center on top match. |
| **Filter checkboxes** | Toggle visibility of SymbolKind / RelationshipType categories. |
| **Drag node** | Manual positioning (disables auto-layout for that node). |
| **Scroll** | Zoom in/out. |
| **Fit button** | Reset viewport to fit all visible nodes. |

### Node Styling (by SymbolKind)

| Kind | Shape | Color |
|---|---|---|
| CLASS | Round rectangle | `#4FC3F7` (blue) |
| INTERFACE | Diamond | `#7E57C2` (purple) |
| FUNCTION | Ellipse | `#66BB6A` (green) |
| METHOD | Ellipse | `#FFA726` (orange) |
| MODULE / FILE | Rectangle | `#AB47BC` (violet) |
| CONSTANT | Small rectangle | `#FFEE58` (yellow) |
| VARIABLE / FIELD | Small ellipse | `#BDBDBD` (grey) |

### Edge Styling (by RelationshipType)

| Type | Style | Color |
|---|---|---|
| CALLS | Solid arrow | `#42A5F5` (blue) |
| IMPORTS | Dashed arrow | `#AB47BC` (purple) |
| EXTENDS | Bold arrow | `#66BB6A` (green) |
| IMPLEMENTS | Dashed arrow | `#26A69A` (teal) |
| REFERENCES | Dotted arrow | `#9E9E9E` (grey) |
| CONTAINS | Dotted, thin | `#E0E0E0` (light grey) |

---

## 6. Implementation Plan

### Phase 1: Backend API (server.py)

**New file:** `src/node_walk/web/server.py`

1. Create a `http.server.HTTPServer` subclass that:
   - Serves static files from `src/node_walk/web/` for `/`, `/style.css`, `/app.js`.
   - Routes `/api/*` requests to handler functions.
   - Opens `SQLiteGraphStore` + `QueryEngine` on startup.
2. Implement API handlers:
   - `handle_graph()` → serialize all symbols + relationships to JSON.
   - `handle_symbol(id)` → serialize symbol detail + source snippet.
   - `handle_neighbors(id)` → 1-hop walk, serialize incremental nodes/edges.
   - `handle_search(q)` → `find_symbol()`, serialize matches.
   - `handle_stats()` → `stats()`, serialize.
3. Add `serve` command to CLI (`main.py`):
   ```python
   @app.command()
   def serve(
       port: int = 7777,
       no_open: bool = False,
   ):
       """Launch the interactive graph explorer in your browser."""
   ```
4. Auto-open `http://localhost:{port}` in the default browser via `webbrowser.open()`.

### Phase 2: Frontend (index.html + style.css + app.js)

**New files:** `src/node_walk/web/index.html`, `style.css`, `app.js`

1. **index.html**: Minimal shell — imports Cytoscape.js from CDN, links `style.css` and `app.js`.
2. **style.css**: Dark theme, layout grid (graph canvas + side panel), search bar, filter panel, status bar.
3. **app.js**:
   - On load: `fetch('/api/graph')` → feed nodes/edges to Cytoscape.
   - Node click → fetch `/api/symbol/{id}` → populate detail panel.
   - Node double-click → fetch `/api/neighbors/{id}` → add to graph, run local layout.
   - Search input → debounced fetch `/api/search` → center + highlight.
   - Filter checkboxes → toggle element visibility via `cy.elements().filter(...)`.

### Phase 3: CLI Integration

1. Add `serve` command to `main.py`.
2. Include `src/node_walk/web/` in the package build (`pyproject.toml` package data).
3. Update `README.md` and `CHANGELOG.md`.

### Phase 4: Tests

1. **Backend API tests** (`tests/test_web_server.py`):
   - Spin up server in a thread, hit each endpoint, assert JSON schema.
   - Test search endpoint with fuzzy queries.
   - Test neighbors endpoint returns incremental data.
2. **Smoke test**: Index a fixture repo, start server, verify `/api/graph` returns valid JSON with expected node/edge counts.

---

## 7. File Manifest

| File | Status | Purpose |
|---|---|---|
| `src/node_walk/web/__init__.py` | NEW | Package marker |
| `src/node_walk/web/server.py` | NEW | HTTP server + API handlers |
| `src/node_walk/web/index.html` | NEW | Single-page HTML shell |
| `src/node_walk/web/style.css` | NEW | Dark theme + layout |
| `src/node_walk/web/app.js` | NEW | Cytoscape.js graph logic |
| `src/node_walk/cli/main.py` | MODIFY | Add `serve` command |
| `pyproject.toml` | MODIFY | Include `web/` in package data |
| `tests/test_web_server.py` | NEW | API endpoint tests |
| `README.md` | MODIFY | Document `serve` command |
| `CHANGELOG.md` | MODIFY | Add traversable graph entry |

---

## 8. Post-MVP Improvements (Later)

These are explicitly **not** in the MVP scope, but worth tracking:

| Improvement | Description |
|---|---|
| **Compound nodes** | Nest methods inside class nodes (Cytoscape.js compound node support). |
| **Trace overlay** | Click "Trace" → highlight the full call chain path with animated edges. |
| **Blast radius heatmap** | Color nodes by blast-radius depth (red = close, blue = far). |
| **"Open in editor" link** | `vscode://file/{path}:{line}` deep-links from the detail panel. |
| **Layout persistence** | Save/restore node positions to `.node_walk/layout.json`. |
| **Incremental re-index** | Watch file changes, re-index modified files, push updates to connected browser via WebSocket. |
| **Performance: virtual rendering** | For repos with >5k symbols, use Cytoscape.js `webgl` renderer or cluster nodes by module. |
| **Minimap** | Small overview panel for orientation on large graphs. |
| **Dark/light toggle** | Theme switcher. |

---

## 9. Constraints & Decisions

| Decision | Rationale |
|---|---|
| **stdlib `http.server`** over Flask/FastAPI | Zero new dependencies, consistent with "lightweight" philosophy. MVP doesn't need async, middleware, or routing frameworks. |
| **Cytoscape.js from CDN** | No npm/bundler build step. Single HTML file can load it. Cytoscape is the most mature JS graph library with built-in layouts, selection, and expansion. |
| **All frontend in 3 files** | Keeps it dead simple. No React, no Vite, no TypeScript. If the MVP proves valuable, we can graduate to a proper SPA later. |
| **Full graph on initial load** | For repos up to ~2-3k symbols this is fine (Cytoscape handles 5k+ nodes). For larger repos, Phase 2 can add pagination or module-level clustering. |
| **CONTAINS edges hidden by default** | They dominate the graph and add visual clutter. Users can toggle them on via the filter panel. |
