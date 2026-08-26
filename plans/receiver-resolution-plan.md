# Receiver and Dependency-Injection Resolution Plan

## Problem

`CALLS` resolution is now able to use simple bindings, but it still assumes
that the receiver's type can be found in one local fact. Real Python code often
passes an implementation through several symbols before a call is made:

```python
class Controller:
    def __init__(self, service: UserService):
        self.service = service

    def get(self, user_id):
        return self.service.get_by_id(user_id)

class App:
    def __init__(self, controller: Controller):
        self.controller = controller

    def run(self, user_id):
        return self.controller.get(user_id)
```

The resolver must answer one general question:

> What symbol does this receiver expression denote at this call site, and
> which member belongs to that symbol?

This applies equally to local variables, constructor parameters, instance
attributes, imported aliases, factories, and nested paths. It should not grow
as a collection of DI, repository, or adapter special cases.

## Goals

- Resolve `receiver.member(...)` as `receiver binding -> member lookup`.
- Support bindings that flow through multiple classes and methods.
- Preserve source facts and explain every resolution decision.
- Prefer a correct `PROBABLE` or `UNRESOLVED` result over an incorrect edge.
- Keep resolution bounded and deterministic for dynamic Python.
- Make re-resolution possible without reparsing source files.

## Non-goals

- Full Python type inference or runtime execution.
- Proving arbitrary dynamic attribute assignment, monkey patching, or
  metaclass behavior.
- Modeling every possible container/service-locator framework in the first
  implementation.

## Current Gaps

1. Binding facts currently store a type hint, but the resolver treats them as
   isolated facts rather than a graph of aliases and assignments.
2. `self.repository` may be established in `__init__` and used in another
   method, so method-local lookup is insufficient.
3. A binding such as `self.service = factory()` has no stable target unless the
   factory return type or returned symbol is known.
4. Nested receivers such as `self.repo.session.client.send()` require repeated
   member resolution, not one string concatenation.
5. Resolver order currently affects whether a binding is available when calls
   are resolved.
6. The visitor can emit duplicate or incomplete binding facts while walking
   nested assignment nodes.
7. FastAPI dependency declarations such as
   `service: UserService = Depends(get_service)` mix a parameter annotation,
   a provider reference, and framework metadata in one AST expression. Treating
   the whole expression as an ordinary call loses the injected type contract.

## Target Model

Represent receiver resolution as a chain of typed steps:

```text
call site: self.repo.session.find
  self.repo       -> Repository
  Repository.session -> Session
  Session.find    -> method symbol
```

Each step should carry:

- expression/path (`self.repo`)
- source scope and class owner
- candidate symbol id(s)
- evidence (`parameter_annotation`, `constructor_call`, `attribute_assignment`,
  `import_alias`, `return_annotation`, `fastapi_depends`, etc.)
- confidence (`RESOLVED`, `PROBABLE`, `UNRESOLVED`)
- diagnostic details

The existing `RelationshipFact` table remains the persisted observation layer.
The implementation may add metadata fields or a dedicated binding index, but
must not hide inferred bindings only in Python process state.

## Resolution Pipeline

### Phase 1: Normalize Extraction Facts

Make the visitor emit stable facts without deciding the final target:

- calls: `receiver_text`, `callee_name`, complete `raw_text`, call scope
- assignments: target path and RHS expression
- parameters: parameter path and annotation expression
- returns: return expression and optional annotation
- imports: local alias and imported qualified path

Normalize target paths structurally where possible. For example, represent
`self.repository` as path segments `self`, `repository`, while retaining the
original text for diagnostics.

Add tests for:

- `service.get_by_id()`
- `self.repository.get_by_id()`
- `self.repo.session.client.send()`
- alias assignments (`repo = self.repository`)
- tuple/destructured assignments where safely supported
- duplicate traversal not producing duplicate binding facts

### Phase 2: Build a Repository Binding Index

After all files are stored and imports/classes are known, build an index from
binding facts. The index should support:

- lookup by file and scope
- lookup by enclosing class
- lookup by exact path (`self.repository`)
- lookup by root alias (`repository`)
- lookup of the latest or applicable assignment before a call line

Do not rely on fact insertion order. Use source line and lexical ownership to
make the result deterministic.

Initial binding sources, in priority order:

1. explicit type annotation (`service: UserService`)
2. constructor call (`service = UserService()`)
3. imported symbol or module alias
4. assignment from an already-known binding (`self.repo = repository`)
5. annotated return value of a known factory
6. FastAPI dependency parameter annotation or provider return annotation
7. unique global fallback, marked `PROBABLE`

### FastAPI `Depends()`

Handle FastAPI dependency injection as a framework-specific binding pattern,
not as a special case in member lookup. The common forms are:

```python
from fastapi import Depends

def get_service() -> UserService:
   return UserService()

@router.get("/users/{user_id}")
def get_user(
   user_id: int,
   service: UserService = Depends(get_service),
):
   return service.get_by_id(user_id)
```

Extraction should record separate facts:

- parameter binding: `service -> UserService`
- provider relationship: `get_user depends_on get_service`
- optional provider return contract: `get_service -> UserService`

The `Depends` wrapper itself should not become a misleading `CALLS` edge from
the route handler to `Depends`. Instead:

1. recognize a default value whose call target is `Depends`
2. extract its first argument as the provider symbol/path
3. preserve the parameter annotation as the strongest receiver type evidence
4. resolve `service.get_by_id()` through `UserService`
5. optionally materialize a separate `DEPENDS_ON` relationship type later;
   until then, retain the provider fact for diagnostics without polluting
   runtime call traversal

Support both named and module-qualified wrappers:

- `Depends(get_service)`
- `fastapi.Depends(get_service)`
- imported aliases where the import resolver proves the alias refers to
  `fastapi.Depends`

Do not assume every function passed to `Depends` returns the annotated type.
When both exist, compare the provider return annotation and parameter
annotation. If they disagree, keep the parameter binding `PROBABLE` and record
the conflict rather than creating a resolved edge silently.

### Phase 3: Resolve Binding Chains

Introduce a resolver/service with an API equivalent to:

```python
resolve_receiver(call_fact) -> ReceiverResolution
resolve_member(receiver_resolution, member_name) -> SymbolResolution
```

For each receiver path:

1. Resolve the root (`self`, local name, imported alias, or known symbol).
2. Apply each remaining attribute segment through class/module member lookup.
3. Stop on ambiguity, missing member, or unsupported dynamic behavior.
4. Detect cycles and enforce a small maximum path depth.

`self` resolves to the enclosing class instance. A binding created in
`__init__` is available to other methods of the same class, but not to an
unrelated class merely because the attribute names match.

### Phase 4: Propagate Constructor and Method Contracts

Support the common multi-class DI flow without pretending to execute Python:

```python
class Controller:
    def __init__(self, service: UserService):
        self.service = service
```

Record that `Controller.service` has type `UserService`. Then:

- resolve `Controller.service.get_by_id` in any controller method
- resolve `Controller(...)` constructor calls to the class and initializer
- pass known argument bindings into typed parameters when a call target is
  known

For a call such as `Controller(service)`, bind the constructor parameter
`service` to the argument's known symbol. For unknown arguments, retain the
annotation as a probable contract rather than inventing a target.

Handle return annotations next. A known `make_service() -> UserService`
should allow `make_service().get_by_id()` only when the AST shape and symbol
model make that relationship unambiguous.

### Phase 5: Materialize Calls with Evidence

Update `CrossFileCallResolver` to consume the binding index and receiver
resolver. Its decision order should be:

1. same-class member resolution for `self` / `cls`
2. exact receiver-path binding and member lookup
3. imported module/class alias lookup
4. direct imported function lookup
5. narrowly scoped unique fallback
6. unresolved result with diagnostics

Diagnostics should include:

- receiver expression
- resolved binding path and target
- member lookup path
- evidence used
- ambiguity or missing-member reason
- resolution confidence

Remove the old one-off class-name receiver heuristic once equivalent coverage
exists. Keep arrow direction and final graph materialization unchanged.

## Multiple-Class Scenarios to Cover

These should become fixtures and integration tests:

1. **Constructor injection**
   `Controller.__init__(service: Service)` followed by
   `self.service.method()`.
2. **Injection across an application root**
   `App(controller: Controller)` followed by `self.controller.get()`.
3. **Local aliasing**
   `repository = self.repository` followed by `repository.find()`.
4. **Factory return**
   `make_repository() -> Repository` followed by
   `make_repository().find()` where the AST supports a safe representation.
5. **Imported implementation**
   `from package.repo import Repository` followed by typed injection.
6. **FastAPI typed dependency**
   `service: UserService = Depends(get_service)` followed by
   `service.get_by_id()`. The graph should resolve the method call to
   `UserService.get_by_id` and retain the provider fact separately.
7. **FastAPI untyped dependency**
   `service = Depends(get_service)`. Resolve through the provider return
   annotation only when it is unambiguous; otherwise mark the binding
   `PROBABLE` or `UNRESOLVED` with a diagnostic.
8. **FastAPI qualified dependency**
   `service: UserService = fastapi.Depends(get_service)`.
9. **Module receiver**
   `package.service.create()` resolved through the imported module symbol.
10. **Nested attributes**
    `self.repository.session.execute()` with each intermediate member known.
11. **Ambiguous implementations**
    Two classes share a member name; result must not become a false resolved
    edge without binding evidence.
12. **Inheritance**
    A method is declared on a base class and called through an injected
    subclass; member lookup may walk known base classes.
13. **Unsupported dynamic code**
    `setattr(self, name, value)` remains unresolved with a useful diagnostic.

## Testing Strategy

### Unit tests

- path parsing and normalization
- binding index precedence
- lexical/class ownership lookup
- member lookup across one and multiple segments
- cycle and depth-limit behavior
- FastAPI `Depends` extraction without a false `CALLS` edge to `Depends`
- provider/parameter annotation agreement and conflict diagnostics
- confidence and diagnostic payloads

### Integration tests

Index temporary multi-file projects and assert the final `CALLS` edge target.
For every fixture also inspect the original call fact and assert its resolver
diagnostics, so a passing edge cannot conceal an accidental fallback.

### Regression matrix

Run the existing analyzer, resolution, indexer, query, storage, and web tests.
Add explicit assertions that unresolved or ambiguous calls do not create a
resolved relationship to a same-named method in an unrelated class.

## Migration Order

1. Add normalized binding/call fact fields without changing graph output.
2. Add binding-index data structures and unit tests.
3. Add receiver-chain and member lookup service.
4. Route `CrossFileCallResolver` through that service.
5. Add constructor argument propagation and class attribute contracts.
6. Add FastAPI `Depends` extraction and provider contracts.
7. Add return annotations and bounded factory support.
8. Remove obsolete resolver heuristics and duplicate extraction paths.
9. Re-index representative repositories and compare resolved/probable/
   unresolved counts before and after.

## Acceptance Criteria

- `self.repository.get_by_id()` resolves to the repository method.
- The same repository binding works from any method of its owning class.
- A controller injected into another class resolves through the second class.
- A FastAPI parameter using `Depends(provider)` resolves calls through its
  annotation or unambiguous provider return type, without a false call to
  `Depends`.
- Nested known receiver paths resolve segment by segment.
- Ambiguous and dynamic cases remain non-resolved and explain why.
- Existing 80-test baseline remains green, with new multi-class fixtures green.
- Re-running resolution produces the same results independent of file traversal
  order.

## Risks and Guardrails

- **False positives:** require binding evidence before marking a call resolved.
- **Stale assignments:** use source location and lexical scope when selecting
  an applicable binding.
- **Dynamic Python:** cap path depth and return unresolved diagnostics.
- **Performance:** build indexes once per indexing pass, not with full-repository
  scans for every call fact.
- **Migration churn:** keep the existing graph edge contract while replacing
  only the resolution internals.
