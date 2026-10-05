# Multi-Pass Indexing & Incremental Re-Indexing Plan

**A forward-pass / backward-pass architecture for building correct graphs iteratively, and for re-indexing only what changed.**

---

## 0. The Core Insight

Building a semantic graph from source code is not a single-shot operation. It has the same structure as training a neural network:

- **Forward pass**: Extract what you can see locally — symbols, raw facts, surface-level relationships.
- **Backward pass**: Propagate knowledge gained globally back to unresolved facts — imports inform bindings, bindings inform calls, calls inform receiver chains, inheritance informs member lookups.
- **Convergence**: Repeat until no new information flows. Each pass resolves more facts using knowledge materialized in the previous pass.

The current codebase already has the bones of this (facts → resolvers → materialization), but the indexer runs everything in a single rigid sequence with no iteration, no convergence detection, and no ability to re-index a single file without wiping the entire graph.

This plan redesigns the system around two complementary ideas:

1. **Multi-pass resolution** that loops until stable (like gradient descent converging).
2. **Incremental re-indexing** that detects changed files, surgically removes their stale data, re-extracts, and re-resolves only the affected subgraph.

---

## 1. What's Wrong With the Current Architecture

### 1.1 Single-Shot Pipeline

The current `Indexer.index()` method runs a fixed sequence:

```
discover → analyze → store → run_resolvers → legacy_cross_file
```

Each resolver runs once, in a hardcoded order. If a later resolver produces information that an earlier resolver needed (e.g., inheritance resolution produces EXTENDS edges that `CrossFileCallResolver` needs for member lookup), the earlier resolver never gets a second chance.

### 1.2 Mixed Concerns in the Indexer

`Indexer._run_resolvers()` currently does three different jobs in one method:

- Fetches facts by type and status
- Runs resolvers in a hardcoded sequence
- Materializes resolved facts into the relationships table

These should be separate, composable stages.

### 1.3 No Convergence Detection

There is no mechanism to detect whether a resolution pass actually made progress. The pipeline runs once and stops, even if running the binding resolver again (after calls resolved some new imports) would resolve more bindings.

### 1.4 No Incremental Path

The only option today is `clear=True` (wipe everything) or `clear=False` (which just appends, creating duplicates). There is no mechanism to:

- Detect which files changed
- Remove stale symbols and facts from changed files
- Re-extract only those files
- Invalidate and re-resolve facts that depended on changed symbols

### 1.5 Legacy Cross-File Resolution

`_resolve_cross_file()` is a separate resolution path that operates on `Relationship` objects directly, bypassing the fact pipeline entirely. It duplicates logic that should live in resolvers.

---

## 2. Target Architecture

### 2.1 The Pipeline as Passes

```
┌─────────────────────────────────────────────────────────────┐
│                        INDEXER                              │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ FORWARD PASS (per-file, embarrassingly parallel)     │   │
│  │                                                      │   │
│  │  1. Discover files (detect changed via content_hash) │   │
│  │  2. Parse AST with LanguageAnalyzer                  │   │
│  │  3. Extract symbols (FILE, CLASS, METHOD, …)         │   │
│  │  4. Extract raw facts (CALL, IMPORT, BINDING, …)     │   │
│  │  5. Extract structural relationships (CONTAINS)      │   │
│  │  6. Store everything                                 │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ BACKWARD PASS (global, multi-iteration)              │   │
│  │                                                      │   │
│  │  loop:                                               │   │
│  │    pass 1: ImportResolver      (IMPORT facts)        │   │
│  │    pass 2: InheritanceResolver (INHERITANCE facts)   │   │
│  │    pass 3: BindingResolver     (BINDING facts)       │   │
│  │    pass 4: Call resolvers      (CALL facts)          │   │
│  │                                                      │   │
│  │    if no new facts resolved this iteration → break   │   │
│  │    if max_iterations reached → break                 │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ MATERIALIZATION PASS (one-shot)                      │   │
│  │                                                      │   │
│  │  1. Delete all fact-derived relationships            │   │
│  │  2. Read all RESOLVED/PROBABLE facts                 │   │
│  │  3. Create Relationship edges from facts             │   │
│  │  4. Deduplicate                                      │   │
│  │  5. Bulk insert                                      │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Why Looping Works

Consider this real scenario:

```python
# file_a.py
from services import UserService

class Controller:
    def __init__(self, service: UserService):
        self.service = service

    def get(self, user_id):
        return self.service.get_by_id(user_id)
```

Iteration 1:
- `ImportResolver` resolves `UserService` → symbol in `services.py` ✓
- `BindingResolver` sees `service: UserService` → resolves binding to UserService class ✓
- `CrossFileCallResolver` sees `self.service.get_by_id()` → binding for `self.service` exists now, resolves! ✓

That's the easy case. But consider:

```python
# file_b.py
class App:
    def __init__(self, ctrl: Controller):
        self.ctrl = ctrl

    def run(self, user_id):
        return self.ctrl.get(user_id)  # needs Controller resolved first
```

Iteration 1:
- `BindingResolver` resolves `ctrl: Controller` ✓
- `CrossFileCallResolver` tries `self.ctrl.get()` → needs to look up `Controller.get`, but `Controller` might be in another file and the EXTENDS edges haven't materialized yet → might fail

Iteration 2:
- After materialization of EXTENDS/IMPLEMENTS from iteration 1, the member lookup through inheritance now works ✓

Each iteration propagates knowledge further through the graph, like backpropagation flowing gradients through layers.

### 2.3 Convergence Guarantee

The system converges because:

1. The set of facts is finite and fixed after the forward pass.
2. Each resolver can only transition a fact from PENDING → {RESOLVED, PROBABLE, UNRESOLVED, IGNORED}. It never moves a fact backward.
3. Each iteration can only resolve facts that were previously PENDING or UNRESOLVED.
4. Therefore the number of PENDING facts monotonically decreases.
5. When no facts change status in an iteration, the loop terminates.

A `max_iterations` safety cap (default: 5) prevents infinite loops in pathological cases.

---

## 3. Codebase Restructuring

### 3.1 Current Structure

```
src/node_walk/
├── analysis/           # Language analyzers (forward pass)
│   ├── base.py         # FileDiscovery, LanguageAnalyzer ABC
│   └── python.py       # PythonAnalyzer (tree-sitter)
├── ir/                 # Data models
│   ├── enums.py
│   └── models.py
├── resolution/         # Fact resolvers
│   ├── base.py         # FactResolver ABC
│   ├── bindings.py
│   ├── calls.py
│   ├── imports.py
│   ├── inheritance.py
│   └── receiver.py
├── storage/            # Persistence
│   ├── base.py         # GraphStore ABC
│   ├── schema.py
│   └── sqlite_store.py
├── query/              # Query engine
├── cli/                # CLI
├── web/                # Browser explorer
└── indexer.py          # Orchestrator (monolith — being redesigned)
```

### 3.2 Target Structure

```
src/node_walk/
├── analysis/               # UNCHANGED — forward pass extraction
│   ├── base.py
│   └── python.py
├── ir/                     # Data models (minor additions)
│   ├── enums.py            # Add PassPhase enum
│   └── models.py           # Add FileState model
├── resolution/             # Resolvers (mostly unchanged)
│   ├── base.py             # FactResolver ABC (updated RunResult)
│   ├── bindings.py
│   ├── calls.py
│   ├── imports.py
│   ├── inheritance.py
│   └── receiver.py
├── pipeline/               # NEW — the multi-pass orchestration layer
│   ├── __init__.py
│   ├── forward.py          # ForwardPass: discover → analyze → store
│   ├── backward.py         # BackwardPass: resolve facts in loop
│   ├── materializer.py     # Materializer: facts → relationships
│   ├── diff.py             # FileDiff: detect changed/added/removed files
│   └── invalidator.py      # Invalidator: cascade-remove stale data
├── storage/                # Persistence (additions for incremental)
│   ├── base.py             # GraphStore ABC (new methods)
│   ├── schema.py           # Schema v3 with content_hash index
│   └── sqlite_store.py     # Implement new methods
├── query/
├── cli/
├── web/
└── indexer.py              # Thin orchestrator — delegates to pipeline/*
```

### 3.3 What Changes and Why

| Current | Target | Rationale |
|---|---|---|
| `indexer.py` does discovery, analysis, storage, resolution, materialization, and legacy cross-file all inline | `indexer.py` becomes a thin entry point that calls `ForwardPass` → `BackwardPass` → `Materializer` | Each pass is testable, replaceable, and has a single job |
| Resolution order is hardcoded in `_run_resolvers()` | `BackwardPass` declares a resolver sequence and loops it | Looping enables knowledge propagation across resolver boundaries |
| Materialization is mixed into `_run_resolvers()` | `Materializer` is a standalone class that reads final fact states and creates relationships | Clean separation of "decide" from "write" |
| `_resolve_cross_file()` is a separate code path | Deleted. Its logic is already covered by `CrossFileCallResolver` and `ImportResolver` | Eliminates duplicate resolution and dual codepaths |
| No change detection | `FileDiff` computes hash diffs against stored `files.content_hash` | Enables incremental re-indexing |
| No invalidation | `Invalidator` removes symbols, facts, and relationships owned by changed files | Clean slate for re-extraction without wiping unrelated data |

---

## 4. Detailed Design: Each Component

### 4.1 `pipeline/diff.py` — FileDiff

```python
class FileDiff:
    """Compares discovered files against the stored file table."""

    def diff(self, discovered: list[FileInfo], store: GraphStore) -> DiffResult:
        """
        Returns:
            added:    files not in the store
            changed:  files whose content_hash differs
            removed:  files in the store but no longer on disk
            unchanged: files whose content_hash matches
        """
```

The diff uses `content_hash` (SHA-256), which `FileDiscovery` already computes. The `files` table already stores `content_hash`.

`DiffResult` is a simple dataclass:

```python
@dataclass
class DiffResult:
    added: list[FileInfo]
    changed: list[FileInfo]          # new FileInfo with updated hash
    removed: list[str]               # file_ids no longer on disk
    unchanged: list[str]             # file_ids with matching hash
```

### 4.2 `pipeline/invalidator.py` — Invalidator

```python
class Invalidator:
    """Surgically removes all data owned by a set of file IDs."""

    def invalidate_files(self, store: GraphStore, file_ids: list[str]) -> InvalidationStats:
        """
        For each file_id:
          1. Delete all relationship_facts where file_id matches
          2. Delete all relationships whose source_id belongs to a symbol in this file
          3. Delete all symbols where file_id matches
          4. Delete the file record itself
          5. Re-mark any facts in OTHER files that had resolved_target_id
             pointing to a now-deleted symbol as PENDING (cascade re-resolution)

        Returns counts of deleted entities.
        """
```

Step 5 is the "backward invalidation cascade": if file B has a resolved CALL fact pointing to a symbol in file A, and file A changed, file B's fact must go back to PENDING so the backward pass can re-resolve it against A's new symbols.

This requires new storage methods:

```python
# New methods on GraphStore
def get_symbols_by_file(self, file_id: str) -> list[Symbol]: ...
def delete_file_data(self, file_id: str) -> None: ...
def reset_facts_targeting(self, symbol_ids: set[str]) -> int: ...
```

### 4.3 `pipeline/forward.py` — ForwardPass

```python
class ForwardPass:
    """
    Discovers files, detects changes, invalidates stale data,
    and extracts fresh symbols + facts from changed/added files.
    """

    def run(
        self,
        root: Path,
        store: GraphStore,
        analyzers: list[LanguageAnalyzer],
        mode: Literal["full", "incremental"],
        progress: ProgressCallback | None = None,
    ) -> ForwardResult:
        """
        In 'full' mode: clear store, discover all, analyze all.
        In 'incremental' mode:
          1. Discover all files on disk
          2. Diff against stored file table
          3. Invalidate changed + removed files
          4. Analyze only added + changed files
          5. Store new results

        Returns:
            ForwardResult with counts and the set of affected file IDs
        """
```

`ForwardResult`:

```python
@dataclass
class ForwardResult:
    files_discovered: int
    files_analyzed: int          # only new/changed in incremental mode
    files_unchanged: int
    files_removed: int
    symbols_extracted: int
    facts_extracted: int
    errors: list[str]
    affected_file_ids: set[str]  # files that were re-analyzed
```

### 4.4 `pipeline/backward.py` — BackwardPass

This is the heart of the multi-pass design.

```python
class BackwardPass:
    """
    Runs resolvers in a loop until convergence or max_iterations.
    Each iteration is one full sweep of the resolver sequence.
    """

    DEFAULT_RESOLVER_SEQUENCE = [
        # Phase 1: Foundation — resolve what things are
        ImportResolver,
        InheritanceResolver,

        # Phase 2: Type knowledge — resolve what names refer to
        BindingResolver,

        # Phase 3: Behavior — resolve what calls what
        NoiseFilterCallResolver,
        ClassMemberCallResolver,
        InFileCallResolver,
        ConstructorCallResolver,
        CrossFileCallResolver,
    ]

    def __init__(
        self,
        resolvers: list[type[FactResolver]] | None = None,
        max_iterations: int = 5,
    ):
        self._resolver_types = resolvers or self.DEFAULT_RESOLVER_SEQUENCE
        self._max_iterations = max_iterations

    def run(self, store: GraphStore) -> BackwardResult:
        """
        Loop:
          1. For each resolver in sequence:
               a. Fetch its applicable PENDING facts
               b. Run it
               c. Track how many facts changed status
          2. If total_resolved_this_iteration == 0 → converged, break
          3. If iteration >= max_iterations → break
          4. Else → next iteration

        Returns:
            BackwardResult with per-iteration stats
        """
```

`BackwardResult`:

```python
@dataclass
class IterationStats:
    iteration: int
    facts_resolved: int        # moved to RESOLVED
    facts_probable: int        # moved to PROBABLE
    facts_ignored: int         # moved to IGNORED
    facts_unresolved: int      # explicitly marked UNRESOLVED
    resolver_stats: dict[str, int]  # resolver_name → count resolved

@dataclass
class BackwardResult:
    iterations_run: int
    converged: bool            # True if last iteration resolved 0 new facts
    iteration_stats: list[IterationStats]
    total_resolved: int
    total_pending: int         # facts still PENDING after all iterations
```

**Key design decision**: Between iterations, facts that a resolver previously marked `UNRESOLVED` should become eligible for re-resolution by other resolvers. The simplest approach: at the start of each iteration, reset all `UNRESOLVED` facts back to `PENDING`. Only facts that are `RESOLVED`, `PROBABLE`, or `IGNORED` are stable.

This is important because:
- Iteration 1: `CrossFileCallResolver` marks a fact `UNRESOLVED` because the binding wasn't available yet.
- Iteration 1: `BindingResolver` resolves the binding.
- Iteration 2: The call fact should be `PENDING` again so `CrossFileCallResolver` can try with the now-available binding.

### 4.5 `pipeline/materializer.py` — Materializer

```python
class Materializer:
    """
    Reads final fact states and creates corresponding Relationship edges.
    Replaces the inline materialization currently in Indexer._run_resolvers().
    """

    def run(self, store: GraphStore) -> MaterializationResult:
        """
        1. Delete all relationships that were fact-derived (have fact_id in metadata)
        2. Read all facts with status RESOLVED or PROBABLE
        3. Map each fact to the appropriate RelationshipType
        4. Deduplicate by (source_id, target_id, type)
        5. Bulk insert
        """
```

The mapping:

| FactType | Status | → RelationshipType |
|---|---|---|
| CALL | RESOLVED/PROBABLE | CALLS |
| IMPORT | RESOLVED/PROBABLE | IMPORTS |
| INHERITANCE | RESOLVED/PROBABLE | EXTENDS or IMPLEMENTS (from metadata) |
| BINDING | RESOLVED/PROBABLE | _(not materialized as a relationship — bindings are internal resolution state)_ |
| REFERENCE | RESOLVED/PROBABLE | REFERENCES |

### 4.6 Redesigned `indexer.py`

The indexer becomes a thin orchestrator:

```python
class Indexer:
    """
    Orchestrates the full indexing pipeline:
      ForwardPass → BackwardPass → Materializer
    """

    def index(
        self,
        root: Path,
        *,
        mode: Literal["full", "incremental"] = "full",
        max_iterations: int = 5,
    ) -> IndexStats:
        # 1. Forward pass
        forward = ForwardPass()
        fwd_result = forward.run(root, self._store, self._analyzers, mode, self._progress)

        # 2. Backward pass (multi-iteration resolution)
        backward = BackwardPass(max_iterations=max_iterations)
        bwd_result = backward.run(self._store)

        # 3. Materialization
        materializer = Materializer()
        mat_result = materializer.run(self._store)

        return IndexStats(
            forward=fwd_result,
            backward=bwd_result,
            materialization=mat_result,
        )
```

---

## 5. Storage Layer Changes

### 5.1 New Schema Additions (v3)

```sql
-- Index for fast hash comparison during diff
CREATE INDEX IF NOT EXISTS idx_files_content_hash ON files(content_hash);

-- Track which relationships came from fact materialization vs extraction
-- (already tracked via metadata_json → fact_id, but add an explicit column)
ALTER TABLE relationships ADD COLUMN fact_derived INTEGER NOT NULL DEFAULT 0;
```

### 5.2 New GraphStore Methods

```python
class GraphStore(ABC):
    # --- Existing methods (unchanged) ---
    ...

    # --- New methods for incremental indexing ---

    @abstractmethod
    def get_file_by_path(self, path: str) -> FileInfo | None:
        """Look up a file by its absolute path."""
        ...

    @abstractmethod
    def get_symbols_by_file(self, file_id: str) -> list[Symbol]:
        """Return all symbols belonging to a file."""
        ...

    @abstractmethod
    def delete_file_data(self, file_id: str) -> None:
        """
        Delete a file and all its owned data:
          - symbols with file_id
          - relationship_facts with file_id
          - relationships whose source_id is a symbol in this file
          - the file record itself
        Uses CASCADE where possible, explicit deletes otherwise.
        """
        ...

    @abstractmethod
    def reset_facts_targeting(self, symbol_ids: set[str]) -> int:
        """
        Find all facts in OTHER files whose resolved_target_id
        is in the given set, and reset them to PENDING.
        Returns count of reset facts.
        """
        ...

    @abstractmethod
    def delete_fact_derived_relationships(self) -> int:
        """Delete all relationships that were materialized from facts."""
        ...

    @abstractmethod
    def reset_unresolved_facts_to_pending(self) -> int:
        """
        Reset all facts with status=UNRESOLVED back to PENDING.
        Used between backward-pass iterations.
        """
        ...

    @abstractmethod
    def get_pending_fact_count(self) -> int:
        """Count of facts still in PENDING status."""
        ...
```

---

## 6. Incremental Re-Indexing: The Full Flow

### 6.1 User Experience

```bash
# First time: full index
node-walk index .

# After editing some files: incremental
node-walk index .

# Force full re-index
node-walk index . --full
```

The `index` command defaults to `incremental` if a graph already exists, and `full` if no graph exists. The `--full` flag forces a clean re-index.

### 6.2 Incremental Flow Detail

```
1. Discover all .py files on disk
2. Load all file records from the store
3. Compute diff:
     added:    files on disk but not in store
     changed:  files where content_hash differs
     removed:  files in store but not on disk
     unchanged: files where hash matches
4. If nothing changed → print "Already up to date" → exit
5. For each removed file_id:
     a. Collect symbol IDs owned by this file
     b. Delete file data (symbols, facts, relationships)
     c. Reset facts in other files that targeted deleted symbols → PENDING
6. For each changed file:
     a. Collect symbol IDs owned by the OLD version
     b. Delete old file data
     c. Reset facts in other files that targeted old symbols → PENDING
     d. Re-analyze the file (forward pass)
     e. Store new symbols, facts, structural relationships
7. For each added file:
     a. Analyze the file
     b. Store results
8. Run backward pass (multi-iteration) on ALL facts
     - Note: unchanged files' RESOLVED facts stay resolved
     - Only reset PENDING facts (from step 5c/6c) and new facts get resolved
9. Materialize all fact-derived relationships
```

### 6.3 Why Full Backward Pass is Needed

Even though only some files changed, the backward pass must consider all facts because:

- A new file might define a class that was previously unresolved as an import target in 10 other files.
- A deleted file removes symbols that other files' facts pointed to, cascading PENDING status.
- A changed file might rename a method, invalidating callers in unchanged files.

The cost is manageable because:
- Already-RESOLVED facts are skipped by resolvers (they check `if fact.status in (RESOLVED, IGNORED): continue`).
- Only PENDING facts get processed.
- The loop converges quickly (typically 2-3 iterations).

---

## 7. CLI Changes

### 7.1 Updated `index` Command

```python
@app.command()
def index(
    path: Path = Path("."),
    full: bool = typer.Option(False, "--full", help="Force full re-index."),
    max_passes: int = typer.Option(5, "--max-passes", help="Max resolution iterations."),
) -> None:
    """Index a repository and build the semantic graph."""
    # Determine mode
    db_exists = (root / _CG_DIR / _DB_FILENAME).exists()
    mode = "full" if full or not db_exists else "incremental"

    indexer = Indexer(store, ...)
    stats = indexer.index(root, mode=mode, max_iterations=max_passes)
```

### 7.2 Updated Output

Replace the current output:

```
[OK] Files analyzed:       47 / 47
[OK] Symbols extracted:    312
[OK] Relationships:        189
[OK] Resolved cross-file:  1316
```

With structured per-phase output:

```
node-walk — indexing ./my_repo (incremental)

  Forward pass
    Files discovered:    47
    Files changed:       3
    Files added:         1
    Files removed:       0
    Files unchanged:     43
    Symbols extracted:   28  (from 4 files)
    Facts extracted:     64

  Backward pass
    Iteration 1:  resolved 412, probable 23, ignored 89
    Iteration 2:  resolved 18, probable 2
    Iteration 3:  converged (0 new)
    Total:        453 resolved, 25 probable, 89 ignored, 12 pending

  Materialization
    Relationships created:  478
    Deduplicated:           12

Index complete ✓
```

---

## 8. Revised `FactResolver` Base

The base `FactResolver.run()` method needs a small change to support convergence tracking:

```python
class FactResolver(ABC):

    def run(self, store: GraphStore, facts: list[RelationshipFact]) -> RunResult:
        """
        Returns a RunResult with counts instead of a bare int.
        """
        result = RunResult()
        for fact in facts:
            if fact.status in (FactStatus.RESOLVED, FactStatus.IGNORED):
                continue
            resolution = self.resolve(store, fact)
            if resolution is not None:
                store.update_relationship_fact(
                    fact.id,
                    status=resolution.status,
                    resolved_target_id=resolution.resolved_target_id,
                    resolver_name=self.name,
                    diagnostics=resolution.diagnostics,
                )
                result.record(resolution.status)
        return result

@dataclass
class RunResult:
    resolved: int = 0
    probable: int = 0
    ignored: int = 0
    unresolved: int = 0

    @property
    def total_decided(self) -> int:
        return self.resolved + self.probable + self.ignored + self.unresolved

    @property
    def progress_made(self) -> int:
        """Facts that moved to a terminal positive state."""
        return self.resolved + self.probable

    def record(self, status: FactStatus) -> None:
        match status:
            case FactStatus.RESOLVED: self.resolved += 1
            case FactStatus.PROBABLE: self.probable += 1
            case FactStatus.IGNORED: self.ignored += 1
            case FactStatus.UNRESOLVED: self.unresolved += 1
```

---

## 9. Handling Edge Cases

### 9.1 Renamed Files

A renamed file appears as one removal + one addition. The old file's symbols are deleted, facts in other files that pointed to them reset to PENDING. The new file is analyzed fresh. The backward pass re-resolves everything, and since the symbols have the same names and structures, the same edges get recreated.

### 9.2 Moved Symbols

If a class moves from `services/old.py` to `services/new.py`, the old file change deletes the old symbol, and the new file change creates a new symbol with a different ID but potentially the same qualified name. Imports in other files that used `from services.old import MyClass` won't resolve (correctly — the import path changed). Imports using `from services.new import MyClass` will resolve.

### 9.3 Circular Dependencies

Python allows circular imports. The multi-pass approach handles this naturally: iteration 1 resolves imports in file A, iteration 2 uses those resolved imports to resolve bindings that depend on symbols in file A, etc.

### 9.4 Large Repositories

For very large repos (10k+ files), the backward pass must not load all facts into memory at once. The resolvers should query facts by type and status, which is already indexed:

```sql
CREATE INDEX idx_fact_type_status ON relationship_facts(fact_type, status);
```

The backward pass fetches only PENDING facts per resolver per iteration.

---

## 10. Migration Strategy

### Phase 1: Extract Pipeline Components (No Behavior Change)

1. Create `pipeline/` package.
2. Move discovery + analysis logic from `Indexer.index()` into `ForwardPass`.
3. Move resolver orchestration from `Indexer._run_resolvers()` into `BackwardPass`.
4. Move materialization from `Indexer._run_resolvers()` into `Materializer`.
5. Delete `Indexer._resolve_cross_file()` (legacy path).
6. `Indexer.index()` becomes a thin delegation to the three passes.
7. All existing tests must still pass — behavior is identical, just reorganized.

### Phase 2: Add Multi-Pass Looping

1. Add `reset_unresolved_facts_to_pending()` to store.
2. Modify `BackwardPass.run()` to loop with convergence detection.
3. Update `FactResolver.run()` to return `RunResult`.
4. Update CLI output to show per-iteration stats.
5. Add tests: create fixtures where iteration 1 cannot resolve everything but iteration 2 can.

### Phase 3: Add Incremental Re-Indexing

1. Add `FileDiff` with hash comparison.
2. Add `Invalidator` with cascade reset.
3. Add new store methods (`delete_file_data`, `reset_facts_targeting`, etc.).
4. Update schema to v3 (add `fact_derived` column, content_hash index).
5. Update `ForwardPass` to support `mode="incremental"`.
6. Update CLI to auto-detect mode and add `--full` flag.
7. Add tests: modify fixture files, run incremental, assert correct graph.

### Phase 4: Cleanup and Polish

1. Remove the `clear` parameter from the old API.
2. Add progress callbacks for each pass.
3. Add `--verbose` flag to show per-resolver stats.
4. Clean up debug prints (e.g., the `print(f"DEBUG: ...")` in `receiver.py` line 132).
5. Update CHANGELOG and README.

---

## 11. Test Plan

### 11.1 Unit Tests

| Test | What it verifies |
|---|---|
| `test_file_diff_detects_changes` | Hash comparison correctly buckets files |
| `test_file_diff_detects_removals` | Files in store but not on disk show as removed |
| `test_invalidator_deletes_owned_data` | Symbols, facts, relationships for a file are deleted |
| `test_invalidator_cascades_pending` | Facts in other files that targeted deleted symbols reset to PENDING |
| `test_backward_pass_converges` | Loop stops when no new facts resolve |
| `test_backward_pass_max_iterations` | Loop stops at max_iterations even if not converged |
| `test_backward_pass_iteration_2_resolves_more` | A fact that fails in iteration 1 succeeds in iteration 2 |
| `test_materializer_creates_correct_edges` | CALL facts → CALLS relationships, INHERITANCE → EXTENDS/IMPLEMENTS |
| `test_materializer_deduplicates` | Same source+target+type doesn't create duplicate edges |
| `test_run_result_tracking` | RunResult correctly counts each status |

### 11.2 Integration Tests

| Test | What it verifies |
|---|---|
| `test_full_index_then_incremental_no_changes` | Incremental with no changes is a no-op |
| `test_full_index_then_add_file` | New file's symbols and relationships appear |
| `test_full_index_then_modify_file` | Changed file's symbols are refreshed, callers in other files re-resolve |
| `test_full_index_then_delete_file` | Deleted file's data removed, dependents' facts reset and re-resolve |
| `test_rename_file` | Old data gone, new data present, cross-file references updated |
| `test_incremental_is_idempotent` | Running incremental twice with no changes produces identical graph |
| `test_full_and_incremental_produce_same_graph` | Full re-index and incremental after changes produce identical relationships |

### 11.3 Performance Benchmarks

| Benchmark | What it measures |
|---|---|
| `bench_full_index_medium_repo` | Full index time on ~100 files |
| `bench_incremental_1_file_changed` | Time for incremental after 1 file edit |
| `bench_backward_pass_iterations` | How many iterations typically needed (expect 2-3) |
| `bench_materialization` | Time to materialize ~1000 facts into relationships |

---

## 12. Success Criteria

The work is done when:

1. **`node-walk index .`** auto-detects whether to run full or incremental.
2. **Incremental re-index after editing 1 file** completes in under 2 seconds on a ~100 file repo.
3. **The backward pass converges** in ≤ 3 iterations for typical Python codebases.
4. **Full index and incremental index produce identical graphs** (verified by test).
5. **The legacy `_resolve_cross_file()` method is deleted.**
6. **The debug `print()` statements in `receiver.py` are removed.**
7. **CLI output shows per-phase statistics** including iteration counts.
8. **All existing tests pass** without modification (or with intentional updates).
9. **New tests cover** the diff, invalidation, multi-pass, and materialization flows.

---

## 13. Risk Analysis

| Risk | Impact | Mitigation |
|---|---|---|
| Cascade invalidation is too aggressive (resets too many facts) | Incremental is slower than expected | Only reset facts whose `resolved_target_id` matched a deleted symbol, not all facts in dependent files |
| Backward pass doesn't converge for pathological code | Infinite loop | Hard cap at `max_iterations` with warning |
| Schema migration breaks existing .node_walk databases | Users must re-index | Detect schema version on open, auto-migrate or prompt for `--full` |
| Materialization creates duplicate edges | Inflated graph | Deduplicate by (source_id, target_id, type) before insert |
| Removing legacy cross-file path causes regressions | Some edges stop appearing | Run both paths in parallel during Phase 1, assert identical output, then remove legacy |
