# Binding and Receiver Resolution Plan

**A backend plan for resolving `service.method()`, `repo.method()`, `self.repository.method()`, and similar chains using explicit multi-pass binding analysis.**

This plan is for the next major semantic-analysis improvement after the current fact/resolver pipeline.

---

## 1. Problem Statement

The current graph is better than before, but it still relies on tactical heuristics for receiver-based calls.

Examples:

- `service.get_by_id(...)`
- `repo.find(...)`
- `adapter.chat(...)`
- `self.repository.get_by_id(...)`
- `self.client.send(...)`

These calls are common in real codebases, especially in:

- controller -> service -> repository architectures
- dependency-injected frameworks
- service classes composed from repositories or clients
- adapter/client wrappers

The root problem is not call extraction. The root problem is missing **receiver type knowledge**.

When the parser sees:

```python
service: TransactionService = Depends(get_transaction_service)
data = await service.get_by_id(session, txn_id)
```

it extracts the call site correctly, but unless we know that `service` is a `TransactionService`, the call target remains unresolved.

The same is true for:

```python
self.repository = repository
transaction = await self.repository.get_by_id(session, txn_id)
```

Without binding information, the receiver chain is opaque.

---

## 2. Core Decision

We do **not** need to resolve all relationships during one parse or one AST walk.

In fact, we should prefer:

- extraction pass
- binding analysis pass
- receiver resolution pass
- materialization pass

This is a better fit for Python’s semantics and for maintainability.

### Guiding Rule

**Parse once if practical, but perform as many semantic passes as needed.**

We should optimize for correctness and debuggability, not for “everything in one visitor.”

---

## 3. Goal

Build a general receiver-resolution framework that can resolve:

- typed parameter receivers
- constructor-bound locals
- instance-attribute receivers
- simple chained receivers

The result should be a reusable system, not a pile of special cases.

---

## 4. What We Need to Infer

The core missing concept is:

- **what symbol/type does this receiver name represent at this point in code?**

Examples:

| Receiver expression | Needed inference |
|---|---|
| `service` | `TransactionService` |
| `repo` | `TransactionRepository` |
| `adapter` | `OllamaAdapter` |
| `self.repository` | `TransactionRepository` |
| `self.client` | `ApiClient` |

If we can infer those bindings, then resolving:

- `service.get_by_id`
- `repo.find`
- `self.repository.get_by_id`

becomes a straightforward symbol lookup problem.

---

## 5. Architectural Direction

Add a new semantic layer:

- **Binding Facts**

This should sit beside:

- `CALL` facts
- `IMPORT` facts
- `INHERITANCE` facts

The future pipeline becomes:

1. extract symbols and raw facts
2. extract binding facts
3. resolve binding facts
4. use binding facts to resolve receiver-based calls
5. materialize final relationships

---

## 6. Proposed Fact Model

Introduce a new fact type:

- `BINDING`

### 6.1 Meaning of a Binding Fact

A binding fact says:

- a local name, parameter, or attribute likely refers to a symbol/type

Examples:

- `service -> TransactionService`
- `repo -> TransactionRepository`
- `adapter -> OllamaAdapter`
- `self.repository -> TransactionRepository`

### 6.2 Suggested Binding Fact Shape

The current `RelationshipFact` model may be enough if we use `fact_type = BINDING` plus metadata.

Suggested stored fields:

- `source_symbol_id`
  - the enclosing method/function/class scope where the binding is relevant
- `raw_text`
  - receiver path, e.g. `service`, `repo`, `self.repository`
- `simple_name`
  - last segment, e.g. `repository`
- `receiver_text`
  - prefix, e.g. `self`
- `qualified_hint`
  - annotation or constructor hint, e.g. `TransactionRepository`
- `metadata`
  - binding origin such as:
    - `parameter_annotation`
    - `constructor_assignment`
    - `self_attr_assignment`
    - `dependency_injection`

### 6.3 Optional Alternative

If the current `RelationshipFact` shape becomes awkward, create a dedicated `BindingFact` model and table.

For MVP, using `RelationshipFact(fact_type=BINDING)` is acceptable if the schema stays readable.

---

## 7. Binding Sources to Extract

We should support these in order of value.

### 7.1 Typed Parameters

Examples:

```python
service: TransactionService
repo: TransactionRepository
session: AsyncSession
```

This is especially important for:

- FastAPI `Depends(...)`
- injected services/repositories
- explicit dependency passing

Extraction rule:

- if a parameter has an annotation, emit a binding fact from parameter name to annotation text

### 7.2 Local Constructor Assignments

Examples:

```python
adapter = OllamaAdapter(...)
svc = UserService(...)
repo = TransactionRepository()
```

Extraction rule:

- if the right-hand side is a call to a symbol-like name, emit a binding fact from the local variable to that constructor/class target

### 7.3 Instance Attribute Assignments in `__init__`

Examples:

```python
self.repository = repository
self.client = client
```

If `repository` or `client` already has a binding, we should propagate it.

This is the key to service -> repo and class composition.

Extraction rule:

- if we see `self.attr = name`
- and `name` is a known parameter/local binding
- emit `self.attr -> bound type of name`

### 7.4 Direct Self Constructor Assignments

Examples:

```python
self.client = ApiClient(...)
```

Extraction rule:

- emit `self.client -> ApiClient`

### 7.5 Optional Future Sources

Not required yet, but compatible with this plan:

- provider function return typing
- factory functions
- dataclass field types
- Pydantic model fields
- assignment from imported aliases

---

## 8. Receiver Resolution Model

Once binding facts exist, call resolution should stop guessing blindly from receiver text.

Instead:

1. parse the receiver chain
2. resolve the base receiver via binding facts
3. walk the chain across class members if needed
4. resolve the final method name

### 8.1 Receiver Examples

#### Case A: Single local receiver

```python
service.get_by_id(...)
```

Flow:

- resolve `service -> TransactionService`
- resolve `TransactionService.get_by_id`

#### Case B: Constructor-bound local

```python
adapter.chat(...)
```

Flow:

- resolve `adapter -> OllamaAdapter`
- resolve `OllamaAdapter.chat`

#### Case C: Self attribute chain

```python
self.repository.get_by_id(...)
```

Flow:

- resolve `self -> current class`
- resolve `self.repository -> TransactionRepository`
- resolve `TransactionRepository.get_by_id`

#### Case D: Nested chain

```python
self.client.session.execute(...)
```

This may require chained binding/member lookup support later.

For MVP:

- support one attribute hop beyond `self`
- then expand later if needed

---

## 9. Proposed Multi-Pass Pipeline

### Pass 1: Symbol and Raw Fact Extraction

Extract:

- symbols
- `CONTAINS`
- raw `CALL` facts
- raw `IMPORT` facts
- raw `INHERITANCE` facts
- raw `BINDING` facts where trivial

### Pass 2: Binding Fact Enrichment

Resolve and propagate bindings:

- parameter annotation bindings
- local constructor bindings
- `self.attr = param`
- `self.attr = ClassName(...)`

Output:

- resolved binding facts
- binding environment per function/class

### Pass 3: Call Resolution with Binding Support

Resolve `CALL` facts using:

- class-member resolution
- import-aware resolution
- binding-aware receiver resolution
- constructor resolution

### Pass 4: Materialization

Materialize final `CALLS`, `IMPORTS`, `EXTENDS`, `IMPLEMENTS`

---

## 10. Storage Strategy

### Option A: Reuse `relationship_facts`

Add:

- `FactType.BINDING`

Pros:

- fastest path
- less schema churn
- aligns with current architecture

Cons:

- fact semantics become broader than “relationship”

### Option B: Add `binding_facts` table

Pros:

- cleaner conceptual separation

Cons:

- more migration work

### Recommendation

Use **Option A** for now:

- `FactType.BINDING`
- `status`
- `resolved_target_id`
- binding-specific metadata

This keeps momentum high.

---

## 11. Resolver Design

### 11.1 New Resolver: `BindingResolver`

Responsibilities:

- resolve parameter annotation bindings to actual symbols
- resolve local constructor bindings to classes
- propagate bindings into `self.attr`
- mark unresolved/ambiguous bindings clearly

### 11.2 New Helper: `ReceiverBindingResolver`

This can be:

- a helper class
- or a utility used inside `CrossFileCallResolver`

Responsibilities:

- given a call fact receiver, return the best bound symbol/type
- support:
  - local names
  - `self.attr`
  - constructor locals
  - typed parameters

### 11.3 Update `CrossFileCallResolver`

It should stop relying mostly on:

- import-map matching
- global simple-name fallback

And instead use:

1. import-aware receiver lookup
2. binding-aware receiver lookup
3. local member lookup
4. global fallback only as last resort

---

## 12. Suggested Extraction Changes

### `visitor.py`

Add extraction for:

- typed parameter bindings
- assignment bindings
- self-attribute propagation candidates

Examples:

- `service: TransactionService = Depends(...)`
- `repo: Repo`
- `adapter = OllamaAdapter(...)`
- `self.repository = repository`

Do not try to fully resolve them there.

The visitor should emit the facts and lightweight hints only.

---

## 13. Suggested Resolver Changes

### New file candidates

- `src/node_walk/resolution/bindings.py`
- optional helper in `src/node_walk/resolution/receiver.py`

### `indexer.py`

Resolver order should become:

1. `ImportResolver`
2. `BindingResolver`
3. `NoiseFilterCallResolver`
4. `InFileCallResolver`
5. `ClassMemberCallResolver`
6. `BindingAwareCallResolver` or upgraded `CrossFileCallResolver`
7. `ConstructorCallResolver`
8. `InheritanceResolver`

Reason:

- bindings should be resolved before receiver-based call resolution

---

## 14. What Counts as MVP for This Layer

This binding system is “good enough MVP” when it resolves:

- DI-style typed parameters
- local constructor-bound receivers
- `self.attr = param` in `__init__`
- service -> repo chains
- controller -> service chains

Specifically, these should work reliably:

- `service.get_by_id(...)`
- `repo.get_by_id(...)`
- `adapter.chat(...)`
- `self.repository.get_by_id(...)`

---

## 15. Testing Plan

### 15.1 Unit Fixtures

Add fixtures for:

- controller -> service typed parameter
- service -> repository via constructor injection
- local constructor assignment
- `self.attr = param` propagation
- ambiguous typed parameter names

### 15.2 Integration Targets

Real-world validation on:

- `expense-tracker`
- `jarvis`
- other layered apps with DI or repository patterns

### 15.3 Assertions

Verify:

- facts exist
- binding facts resolve
- call facts resolve
- materialized `CALLS` edges point to the right method

---

## 16. Risks

### Risk: Overfitting to Python Service/Repo Patterns

Mitigation:

- keep the model generic: receiver binding, not “service logic”

### Risk: Type Annotation Text Does Not Match Symbol Name Directly

Mitigation:

- use import-aware resolution for annotations too
- support suffix matching conservatively

### Risk: Chained Receivers Become Complex

Mitigation:

- support one-hop chains first
- expand incrementally

### Risk: Temporary Duplication with Existing Heuristics

Mitigation:

- prefer moving logic into binding-aware resolution instead of stacking more ad hoc rules

---

## 17. Recommended Build Order

### Phase 1

- add `FactType.BINDING`
- emit typed parameter binding facts
- emit constructor assignment binding facts

### Phase 2

- add `BindingResolver`
- resolve binding facts to symbols/classes

### Phase 3

- add `self.attr = param` propagation
- store class-level attribute bindings

### Phase 4

- upgrade call resolution to consume binding facts
- resolve controller -> service and service -> repo chains

### Phase 5

- support simple chained receivers like `self.repository.get_by_id`
- add more regression fixtures

---

## 18. Final Recommendation

The current resolver pipeline should evolve from:

- “guess target from receiver text and imports”

to:

- “resolve receiver binding first, then resolve the called member”

That is the clean general solution.

It will handle:

- dependency injection
- service/repository architecture
- constructor-bound locals
- composed classes

without turning the codebase into a growing pile of special cases.
