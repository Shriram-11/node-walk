# Graph Explorer UX Plan

**A clear interaction and information design plan for the `node-walk` browser graph explorer.**

The goal is not just "prettier UI." The goal is a graph that helps a developer answer:

- what does this symbol depend on?
- what depends on this symbol?
- what is the blast radius if this changes?
- where does this method sit inside its class/module?
- what do the arrows mean without guessing?

---

## 1. Core Product Goal

The graph explorer should make repository structure and change impact understandable in seconds.

That means:

- arrow direction must have one stable meaning
- classes and methods must behave differently and intentionally
- callers vs callees must never be visually ambiguous
- structural edges and semantic edges must not be mixed in a confusing way
- the UI must support both local inspection and blast-radius tracing

---

## 2. Current Problems

### 2.1 Arrow Direction Is Mentally Expensive

Right now a user can see an arrow and still wonder:

- does this point to something the node calls?
- or to something that calls the node?
- or is it just containment?

That uncertainty makes the graph feel wrong even when the data is right.

### 2.2 Class Nodes and Method Nodes Are Mixed Without a Clear Model

Example:

- user clicks `OllamaAdapter`
- expects to understand who uses it and what it does
- but many meaningful relationships are actually attached to `OllamaAdapter.chat`

This makes the graph appear incomplete even when the method-level graph is correct.

### 2.3 Terminology Is Too Database-Shaped

Terms like:

- `INCOMING`
- `OUTGOING`
- `IMPORTS`
- `Resolved cross-file`

are implementation-oriented, not developer-oriented.

The graph should describe relationships in user language, not storage language.

### 2.4 Structural and Semantic Edges Are Not Separated Clearly Enough

`CONTAINS` is useful, but if it is visually mixed with `CALLS`, it can drown the real behavioral story.

The user often wants one of two different views:

- structure view
- behavior / dependency view

Right now those are not cleanly separated.

### 2.5 Blast Radius Is Possible But Not Obvious

The backend can already support impact analysis, but the graph does not make the workflow obvious:

- “show me what this method calls”
- “show me what calls this method”
- “keep expanding until I see the full downstream or upstream chain”

---

## 3. Design Principles

- One arrow meaning only
- Method-level behavior, class-level organization
- Structural edges are secondary unless explicitly requested
- Detail panel explains the selected node in plain language
- Every click should answer a question, not create a new puzzle
- Blast radius must be a first-class workflow
- Labels should match developer intent, not internal schema names

---

## 4. Primary Interaction Model

The explorer should have three explicit graph modes:

1. `Outgoing`
2. `Incoming`
3. `Both`

These modes should be visible and user-controlled at all times.

### 4.1 Meaning of Each Mode

#### Outgoing

Shows:

- what the selected node depends on
- what it calls
- what it imports
- what it contains, if structural edges are enabled

This is the default mode.

Why:

- it matches “what does this code do?”
- it is the best first view for understanding behavior

#### Incoming

Shows:

- what depends on the selected node
- what calls it
- what imports it
- what contains it, if the user is in structural mode

Why:

- this is the blast-radius mode
- it answers “what breaks if I change this?”

#### Both

Shows both directions, but should be treated as an advanced mode.

Why:

- useful for exploration
- more visually dense
- not ideal as the default because it reintroduces ambiguity

---

## 5. Arrow Semantics

This is the most important rule in the whole plan.

### 5.1 Single Rule

**An arrow always points from the source symbol to the thing it directly relates to.**

Examples:

- `chat -> _message_payload` means `chat` calls `_message_payload`
- `UserService -> Repository` means `UserService` extends or depends on `Repository`
- `module.py -> json` means `module.py` imports `json`
- `OllamaAdapter -> chat` means `OllamaAdapter` contains `chat`

### 5.2 Consequence

When the user switches to `Incoming` mode, the graph should still preserve arrow direction in the data model, but the UI should visually present the selected node as the target of those arrows.

This means:

- we do not reverse the meaning of edges
- we change which edges are shown around the selected node

### 5.3 Visual Direction Rules

- `Outgoing` mode: selected node appears as the left/center origin where possible
- `Incoming` mode: selected node appears as the right/center target where possible
- `Both` mode: standard force layout is acceptable

This helps the layout reinforce the meaning.

---

## 6. Node Semantics by Kind

Different symbol kinds should behave differently.

### 6.1 Method / Function Node

Method/function nodes are the primary behavioral graph unit.

Clicking a method should emphasize:

- its parent class or module
- what it calls
- what calls it
- its exact source
- its blast radius

### 6.2 Class Node

Class nodes are organizational and architectural units.

Clicking a class should emphasize:

- what methods it contains
- what it extends or implements
- which methods are high-traffic
- optionally an aggregate view of its methods’ callers/callees

Class nodes should not pretend to be equivalent to method call nodes.

### 6.3 Module / File Node

Module/file nodes should primarily support:

- import relationships
- structural organization
- jump-off points to contained classes/functions

These should be hidden by default in behavior mode unless explicitly enabled.

---

## 7. What Happens When You Click a Method

Clicking a method should trigger a focused behavior view.

### 7.1 Visual Behavior

- select the method node
- center it
- highlight its direct neighbors based on current direction mode
- dim unrelated nodes
- pin the method’s parent class node nearby if not already visible

### 7.2 Detail Panel

The detail panel should show:

- symbol name
- qualified name
- kind
- file path
- line range
- signature
- parent class or module
- direct `Calls`
- direct `Called by`
- `Belongs to`
- unresolved or ignored call facts, if debug mode is enabled

### 7.3 Primary Actions

Buttons or controls:

- `Expand Calls`
- `Expand Callers`
- `Show Blast Radius`
- `Show Downstream Trace`
- `Show Parent Class`
- `Focus Only This Chain`

### 7.4 Expected Questions It Should Answer

- what does this method call?
- what methods call this?
- what class is it part of?
- what would break if I changed it?

---

## 8. What Happens When You Click a Class

Clicking a class should trigger an architectural view first, with an optional aggregate behavior layer.

### 8.1 Visual Behavior

- select the class node
- show containment edges to its methods and fields
- show inheritance edges
- optionally show top method activity summary

### 8.2 Detail Panel

The panel should show:

- class name
- file path
- line range
- base class / interface info
- methods contained
- direct subclasses / implementers if any
- aggregate call summary

### 8.3 Aggregate Behavior Summary

This is important because users naturally expect class-level behavior understanding.

When a class is selected, show:

- `Methods in class`
- `External callers of methods in this class`
- `External callees used by methods in this class`
- `Most connected methods`

This can be a summary list, not necessarily all edges at once.

### 8.4 Primary Actions

- `Expand Methods`
- `Show Inheritance`
- `Show Method Call Summary`
- `Focus Class Subgraph`
- `Show Class Blast Radius`

### 8.5 Expected Questions It Should Answer

- what methods are inside this class?
- who uses this class or its methods?
- what does this class depend on architecturally?
- where should I click next to understand behavior?

---

## 9. Blast Radius Workflow

Blast radius must be a first-class interaction, not a hidden interpretation.

### 9.1 Definition

Blast radius means:

- all upstream dependents of a symbol
- optionally across multiple hops

For a method:

- all callers
- callers of callers
- optionally imports and references if enabled

For a class:

- callers of its methods
- constructors or direct class usages
- subclass relationships if relevant

### 9.2 UI Behavior

Add a dedicated control:

- `Blast Radius`

Clicking it should:

- switch to `Incoming` mode
- expand recursively to configurable depth
- keep the selected symbol visually anchored
- show depth labels or edge layers if possible

### 9.3 Settings

- depth selector: `1`, `2`, `3`, `4+`
- relationship filters:
  - `Calls`
  - `Imports`
  - `References`
  - `Extends / Implements`

### 9.4 Result Presentation

Show a plain-language summary:

- `3 direct callers`
- `11 upstream dependents within 3 hops`

That is much clearer than a raw graph count.

---

## 10. Trace Workflow

Trace is the opposite of blast radius.

### 10.1 Definition

Trace means:

- all downstream dependencies of a symbol
- what it directly and indirectly reaches

### 10.2 UI Behavior

Add a dedicated control:

- `Trace`

Clicking it should:

- switch to `Outgoing` mode
- expand recursively to configurable depth
- show the selected symbol as the root

### 10.3 Result Presentation

Show a summary:

- `6 direct callees`
- `19 downstream symbols within 4 hops`

---

## 11. Terminology Plan

All labels should be rewritten in user language.

### 11.1 Replace These Terms

Replace:

- `Incoming` with `Called By` / `Used By` depending on context
- `Outgoing` with `Calls` / `Uses` depending on context
- `INCOMING (4)` with grouped labels
- `OUTGOING (5)` with grouped labels
- `Resolved cross-file` in indexing output

### 11.2 Recommended Graph Labels

For relationship types:

- `CALLS` -> `Calls`
- `IMPORTS` -> `Imports`
- `CONTAINS` -> `Contains`
- `EXTENDS` -> `Extends`
- `IMPLEMENTS` -> `Implements`

For reverse display in the detail panel:

- `Called by`
- `Imported by`
- `Contained in`
- `Extended by`

### 11.3 Recommended Index Output Labels

Replace:

- `Resolved cross-file: 1316`

With something like:

- `Facts resolved: 1316`
- `Materialized relationships: 617`

Or, even better:

- `Raw facts: 1316`
- `Resolved facts: X`
- `Ignored facts: Y`
- `Unresolved facts: Z`
- `Graph relationships: 617`

This makes it clear that facts and graph edges are not the same thing.

---

## 12. Relationship Presentation in the Detail Panel

The panel should group relationships by meaning, not by raw direction.

### 12.1 For a Method

Show:

- `Belongs to`
- `Calls`
- `Called by`
- `Imports`
- `Imported by` if relevant

### 12.2 For a Class

Show:

- `Contained in module`
- `Methods`
- `Extends`
- `Implemented by` or `Extends from`
- `External callers of methods`
- `External dependencies of methods`

### 12.3 For a Module

Show:

- `Contains`
- `Imports`
- `Imported by`

---

## 13. Structural vs Behavioral Views

The explorer should support two top-level lenses:

1. `Behavior`
2. `Structure`

### 13.1 Behavior View

Default view.

Prioritizes:

- `CALLS`
- `EXTENDS`
- `IMPLEMENTS`
- optionally `IMPORTS`

Hides or downplays:

- `CONTAINS`
- low-value file nodes

### 13.2 Structure View

Prioritizes:

- `CONTAINS`
- module/class/method nesting
- inheritance

Downplays:

- runtime call edges

This split removes a lot of current visual confusion.

---

## 14. Layout Strategy

The graph should use different layout logic depending on the mode.

### 14.1 Focused Symbol View

When a symbol is selected:

- center the selected node
- arrange immediate neighbors around it
- put outgoing neighbors to the right or lower-right
- put incoming neighbors to the left or upper-left

### 14.2 Blast Radius View

Use a directional layout if possible:

- root at center/right
- callers upstream to the left

### 14.3 Trace View

Use a directional layout:

- root at center/left
- callees downstream to the right

### 14.4 Structure View

Use a hierarchical layout:

- module
- class
- method / field

This is much better than force layout for containment.

---

## 15. Debug Mode

The explorer should have a debug mode for graph quality inspection.

### 15.1 What Debug Mode Shows

- unresolved facts
- ignored facts
- resolver name
- relationship resolution status
- raw call/import text

### 15.2 Why

This helps improve backend quality without exposing internal complexity in normal usage.

Default should be off.

---

## 16. Suggested API Changes

The backend should support the UI model directly.

### 16.1 `/api/symbol/{id}`

Should return:

- grouped relationships
- counts by meaning
- parent class/module
- methods if class
- aggregate method summary if class
- unresolved facts in debug mode

### 16.2 `/api/neighbors/{id}`

Should support:

- `direction=out|in|both`
- `mode=behavior|structure`
- `depth=n`
- relation filters

### 16.3 New Helpful Endpoints

Potential additions:

- `/api/blast-radius/{id}`
- `/api/trace/{id}`
- `/api/class-summary/{id}`

These can be thin wrappers over existing query engine capabilities.

---

## 17. Suggested Frontend Changes

### 17.1 Top Bar

Add:

- mode toggle: `Behavior` / `Structure`
- direction toggle: `Outgoing` / `Incoming` / `Both`
- action buttons:
  - `Trace`
  - `Blast Radius`
  - `Fit`
  - `Reset`

### 17.2 Detail Panel

Replace raw `INCOMING` / `OUTGOING` sections with:

- `Belongs to`
- `Calls`
- `Called by`
- `Contains`
- `Methods`
- `Extends`
- `Imports`

### 17.3 Canvas Behavior

- selected node gets a strong visual anchor
- parent class pinned into the local subgraph for methods
- relation labels appear only on hover or selected paths
- edge styles should differ by semantic category

---

## 18. Recommended Build Order

### Phase 1: Terminology Cleanup

- rename confusing counters and labels
- separate fact stats from relationship stats
- make edge labels plain English

### Phase 2: Direction Semantics

- implement explicit `Outgoing / Incoming / Both`
- make arrow meaning stable
- adjust panel grouping accordingly

### Phase 3: Method Click Behavior

- show parent class
- show grouped callers/callees
- improve selection/focus behavior

### Phase 4: Class Click Behavior

- show contained methods
- add aggregate behavior summary
- expose easy jump from class to interesting methods

### Phase 5: Blast Radius and Trace Flows

- add dedicated controls
- recursive expansion by direction
- clear summaries

### Phase 6: Structure vs Behavior Views

- split the two lenses cleanly
- use different layout strategies

### Phase 7: Visual Polish

- improve styles
- better layout transitions
- spacing, readability, emphasis

---

## 19. MVP Definition for the Graph Explorer

The graph explorer can be considered “good enough MVP” when:

- a user always knows what arrow direction means
- clicking a method clearly shows `Calls` and `Called by`
- clicking a class clearly shows `Methods` and inheritance
- blast radius is a clear first-class interaction
- structural and behavioral views are separated enough to avoid confusion
- graph stats use honest, understandable terminology

---

## 20. Final Recommendation

The graph should not try to be one generic “everything at once” canvas.

It should behave like a developer tool with intentional modes:

- `Behavior` for understanding execution and dependencies
- `Structure` for understanding organization
- `Trace` for downstream exploration
- `Blast Radius` for upstream impact

And the single most important invariant should be:

**An arrow always points from a symbol to the thing it directly relates to.**

If that stays true everywhere, the rest of the graph becomes much easier to trust.
