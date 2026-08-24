/**
 * node-walk Graph Explorer — app.js
 *
 * Responsibilities:
 *  - Bootstrap Cytoscape.js with data from /api/graph
 *  - Node click → detail panel via /api/symbol/{id}
 *  - Node double-click → lazy neighbour expansion via /api/neighbors/{id}
 *  - Right-click → context menu (focus, hide, expand, reset)
 *  - Search bar → debounced /api/search → centre + highlight
 *  - Filter checkboxes → toggle node/edge visibility
 *  - Tooltip on hover
 */

"use strict";

// ---------------------------------------------------------------------------
// Design tokens (keep in sync with style.css CSS variables)
// ---------------------------------------------------------------------------
const KIND_COLORS = {
  CLASS: "#4FC3F7",
  INTERFACE: "#7E57C2",
  FUNCTION: "#66BB6A",
  METHOD: "#FFA726",
  MODULE: "#AB47BC",
  FILE: "#818CF8",
  CONSTANT: "#FFEE58",
  VARIABLE: "#BDBDBD",
  FIELD: "#BDBDBD",
};

const KIND_GLYPHS = {
  CLASS: "C",
  INTERFACE: "I",
  FUNCTION: "ƒ",
  METHOD: "m",
  MODULE: "mod",
  FILE: "f",
  CONSTANT: "c",
  VARIABLE: "v",
  FIELD: "·",
};

const REL_COLORS = {
  CALLS: "#42A5F5",
  IMPORTS: "#AB47BC",
  EXTENDS: "#66BB6A",
  IMPLEMENTS: "#26A69A",
  REFERENCES: "#9E9E9E",
  CONTAINS: "#6B7280",
};

const REL_LABELS = {
  CALLS: "Calls",
  IMPORTS: "Imports",
  EXTENDS: "Extends",
  IMPLEMENTS: "Implements",
  REFERENCES: "References",
  CONTAINS: "Contains",
};

const KIND_SHAPES = {
  CLASS: "round-rectangle",
  INTERFACE: "diamond",
  FUNCTION: "ellipse",
  METHOD: "ellipse",
  MODULE: "rectangle",
  FILE: "rectangle",
  CONSTANT: "rectangle",
  VARIABLE: "ellipse",
  FIELD: "ellipse",
};

const ALL_KINDS = Object.keys(KIND_COLORS);
const ALL_RELS = Object.keys(REL_COLORS);

// Hide structural/noise nodes & edges by default so __init__.py and raw files don't clutter the graph
const HIDDEN_BY_DEFAULT_KINDS = new Set([
  "FILE",
  "CONSTANT",
  "VARIABLE",
  "FIELD",
]);
const HIDDEN_BY_DEFAULT_RELS = new Set(["CONTAINS"]);

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let cy; // Cytoscape instance
let rawGraphData = { nodes: [], edges: [] }; // Full dataset from API
let selectedNodeId = null; // currently selected node id
let hiddenNodes = new Set(); // manually hidden node ids
let focusedNodeId = null; // node being "focused" (subtree highlight)
let isFullGraphMode = false; // whether all nodes are loaded onto canvas
let activeDirection = "out"; // visible relationship direction around selection

const activeKinds = new Set(
  ALL_KINDS.filter((k) => !HIDDEN_BY_DEFAULT_KINDS.has(k)),
);
const activeRels = new Set(
  ALL_RELS.filter((r) => !HIDDEN_BY_DEFAULT_RELS.has(r)),
);

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const elLoading = document.getElementById("cy-loading");
const elEmpty = document.getElementById("cy-empty");
const elDetailPanel = document.getElementById("detail-panel");
const elFilterPanel = document.getElementById("filter-panel");
const elSearchInput = document.getElementById("search-input");
const elSearchRes = document.getElementById("search-results");
const elStatusNodes = document.getElementById("status-nodes");
const elStatusEdges = document.getElementById("status-edges");
const elStatusSel = document.getElementById("status-selected");
const elTooltip = document.getElementById("tooltip");
const elCtxMenu = document.getElementById("ctx-menu");
const elKindFilters = document.getElementById("kind-filters");
const elRelFilters = document.getElementById("rel-filters");
const elDetailName = document.getElementById("detail-name");
const elDetailKind = document.getElementById("detail-kind");
const elDetailFile = document.getElementById("detail-file");
const elDetailLines = document.getElementById("detail-lines");
const elDetailSig = document.getElementById("detail-sig");
const elDetailSigRow = document.getElementById("detail-sig-row");
const elDetailInCount = document.getElementById("detail-in-count");
const elDetailOutCount = document.getElementById("detail-out-count");
const elDetailCallersList = document.getElementById("detail-callers-list");
const elDetailCalleesList = document.getElementById("detail-callees-list");
const elDetailUnresolvedWrap = document.getElementById(
  "detail-unresolved-wrap",
);
const elDetailUnresolvedList = document.getElementById(
  "detail-unresolved-list",
);
const elDetailDoc = document.getElementById("detail-docstring");
const elDetailDocWrap = document.getElementById("detail-docstring-wrap");
const elDetailSrcCode = document.getElementById("detail-source-code");
const elDetailSrcWrap = document.getElementById("detail-source-wrap");
const directionButtons = document.querySelectorAll("[data-direction]");

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Utility & Node Markings
// ---------------------------------------------------------------------------
function kindColor(kind) {
  return KIND_COLORS[kind] || "#6b7280";
}
function relColor(rel) {
  return REL_COLORS[rel] || "#9E9E9E";
}
function relLabel(rel) {
  return REL_LABELS[rel] || rel;
}
function kindShape(kind) {
  return KIND_SHAPES[kind] || "ellipse";
}
function kindGlyph(kind) {
  return KIND_GLYPHS[kind] || "•";
}

function getDisplayName(node) {
  const name = node.name || "";
  const qname = node.qualified_name || name;
  const parts = qname.split(".");
  if (node.kind === "METHOD" || node.kind === "FIELD") {
    if (parts.length >= 2) {
      return `${parts[parts.length - 2]}.${parts[parts.length - 1]}`;
    }
  } else if (node.kind === "FUNCTION") {
    if (
      parts.length >= 2 &&
      [
        "handler",
        "__init__",
        "main",
        "run",
        "execute",
        "tool",
        "TOOL",
      ].includes(name.toLowerCase())
    ) {
      return `${parts[parts.length - 2]}.${parts[parts.length - 1]}`;
    }
  }
  return name || qname;
}

function getNodeBadgeLabel(node) {
  const glyph = kindGlyph(node.kind);
  const name = getDisplayName(node);
  return `[${glyph}] ${name}`;
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function apiFetch(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`API ${path} → ${r.status}`);
  return r.json();
}

function setLoading(on) {
  elLoading.classList.toggle("hidden", !on);
}

function updateStatusBar() {
  if (!cy) return;
  const visNodes = cy.nodes(":visible").length;
  const visEdges = cy.edges(":visible").length;
  elStatusNodes.textContent = `${visNodes} nodes`;
  elStatusEdges.textContent = `${visEdges} edges`;
  if (selectedNodeId) {
    const n = cy.getElementById(selectedNodeId);
    elStatusSel.textContent = n.length ? n.data("qualified_name") : "";
  } else {
    elStatusSel.textContent = "";
  }
}

function setDirection(direction) {
  activeDirection = direction;
  directionButtons.forEach((button) => {
    const isActive = button.dataset.direction === direction;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
  if (selectedNodeId) selectNode(selectedNodeId);
}

function hideTooltip() {
  elTooltip.hidden = true;
}

function showTooltip(text, x, y) {
  elTooltip.textContent = text;
  elTooltip.hidden = false;
  const tw = elTooltip.offsetWidth;
  const th = elTooltip.offsetHeight;
  elTooltip.style.left = Math.min(x + 12, window.innerWidth - tw - 8) + "px";
  elTooltip.style.top = Math.max(y - th - 8, 8) + "px";
}

// ---------------------------------------------------------------------------
// Cytoscape stylesheet
// ---------------------------------------------------------------------------
function buildStylesheet() {
  const nodeStyles = ALL_KINDS.map((k) => ({
    selector: `node[kind="${k}"]`,
    style: {
      "background-color": kindColor(k),
      shape: kindShape(k),
    },
  }));

  const edgeStyles = ALL_RELS.map((r) => ({
    selector: `edge[type="${r}"]`,
    style: {
      "line-color": relColor(r),
      "target-arrow-color": relColor(r),
      "line-style": [
        "IMPORTS",
        "IMPLEMENTS",
        "REFERENCES",
        "CONTAINS",
      ].includes(r)
        ? "dashed"
        : "solid",
      width: r === "EXTENDS" ? 3 : r === "CONTAINS" ? 1 : 2,
    },
  }));

  edgeStyles.push({
    selector: `edge[resolution="PROBABLE"]`,
    style: {
      "line-style": "dashed",
      "line-dash-pattern": [4, 4],
    },
  });

  return [
    {
      selector: "node",
      style: {
        label: "data(badgeLabel)",
        "font-size": 11,
        "font-weight": 600,
        "font-family": "'JetBrains Mono', monospace",
        color: "#f1f5f9",
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 5,
        "text-outline-color": "#0d0f14",
        "text-outline-width": 3,
        "text-outline-opacity": 0.95,
        width: "data(sz)",
        height: "data(sz)",
        "border-width": 2,
        "border-color": "rgba(255,255,255,0.25)",
        "background-color": "#6b7280",
        "transition-property": "opacity, border-color, border-width",
        "transition-duration": "150ms",
      },
    },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier",
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.85,
        "line-color": "#9E9E9E",
        "target-arrow-color": "#9E9E9E",
        width: 1.4,
        label: "",
        "font-size": 9,
        "font-family": "'Inter', sans-serif",
        color: "rgba(255,255,255,0.65)",
        "text-rotation": "autorotate",
        "text-margin-y": -6,
        "text-outline-color": "#0d0f14",
        "text-outline-width": 1.5,
        "transition-property": "opacity",
        "transition-duration": "150ms",
      },
    },
    {
      selector: "edge.edge-hovered",
      style: { label: "data(label)", width: 2.5, "z-index": 50 },
    },
    ...nodeStyles,
    ...edgeStyles,
    // Selected node
    {
      selector: "node.selected",
      style: {
        "border-width": 3,
        "border-color": "#fff",
        "z-index": 999,
        label: "data(badgeLabel)",
      },
    },
    // Neighbor highlight
    {
      selector: "node.neighbor",
      style: {
        "border-width": 2,
        "border-color": "rgba(255,255,255,0.7)",
        label: "data(badgeLabel)",
      },
    },
    // Dimmed (focus mode)
    {
      selector: "node.dimmed, edge.dimmed",
      style: { opacity: 0.12 },
    },
    {
      selector: "node.highlighted, edge.highlighted",
      style: { opacity: 1 },
    },
    // New nodes added via expand
    {
      selector: "node.new",
      style: {
        "border-color": "#4f9dff",
        "border-width": 3,
      },
    },
  ];
}

// ---------------------------------------------------------------------------
// Cytoscape elements from API data
// ---------------------------------------------------------------------------
function buildElements(nodes, edges) {
  const cyNodes = nodes.map((n) => ({
    group: "nodes",
    data: {
      id: n.id,
      displayName: getDisplayName(n),
      badgeLabel: getNodeBadgeLabel(n),
      glyph: kindGlyph(n.kind),
      ...n,
    },
  }));
  const cyEdges = edges.map((e) => ({
    group: "edges",
    data: {
      id: e.id,
      source: e.source,
      target: e.target,
      type: e.type,
      label: relLabel(e.type),
      resolution: e.resolution,
    },
  }));
  return [...cyNodes, ...cyEdges];
}

// ---------------------------------------------------------------------------
// Subgraph & Seed Selection (Progressive Discovery)
// ---------------------------------------------------------------------------
function getCoreSeeds(nodes, edges) {
  // Degree count per node
  const degMap = new Map();
  edges.forEach((e) => {
    degMap.set(e.source, (degMap.get(e.source) || 0) + 1);
    degMap.set(e.target, (degMap.get(e.target) || 0) + 1);
  });

  // Filter out noise nodes (files, constants, fields)
  const candidateNodes = nodes.filter(
    (n) => !HIDDEN_BY_DEFAULT_KINDS.has(n.kind),
  );

  // Score candidate nodes
  const scored = candidateNodes.map((n) => {
    let score = degMap.get(n.id) || 0;
    if (n.kind === "CLASS") score += 15;
    if (n.kind === "INTERFACE") score += 12;
    if (n.kind === "FUNCTION") score += 4;
    return { node: n, score };
  });

  scored.sort((a, b) => b.score - a.score);

  // Take top core hubs (up to 12)
  const topSeeds = scored.slice(0, 12).map((s) => s.node);
  const seedIds = new Set(topSeeds.map((s) => s.id));

  // Include edges connecting these seeds
  const seedEdges = edges.filter(
    (e) =>
      seedIds.has(e.source) &&
      seedIds.has(e.target) &&
      !HIDDEN_BY_DEFAULT_RELS.has(e.type),
  );

  return {
    seedNodes: topSeeds,
    seedEdges,
    allCandidates: scored.map((s) => s.node),
  };
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
async function init() {
  setLoading(true);
  try {
    rawGraphData = await apiFetch("/api/graph");
  } catch (err) {
    setLoading(false);
    elEmpty.hidden = false;
    elLoading.classList.add("hidden");
    console.error("Failed to load graph:", err);
    return;
  }

  if (!rawGraphData.nodes || rawGraphData.nodes.length === 0) {
    setLoading(false);
    elEmpty.hidden = false;
    elLoading.classList.add("hidden");
    return;
  }

  elEmpty.hidden = true;

  // Progressive discovery: seed canvas with top core hubs
  const { seedNodes, seedEdges, allCandidates } = getCoreSeeds(
    rawGraphData.nodes,
    rawGraphData.edges,
  );
  renderQuickSeeds(allCandidates.slice(0, 10));

  const elements = buildElements(seedNodes, seedEdges);

  cy = cytoscape({
    container: document.getElementById("cy"),
    elements,
    style: buildStylesheet(),
    layout: {
      name: "cose",
      animate: true,
      animationDuration: 600,
      randomize: true,
      nodeRepulsion: () => 14000,
      idealEdgeLength: () => 110,
      nodeOverlap: 20,
      gravity: 0.5,
      numIter: 2000,
      coolingFactor: 0.95,
      fit: true,
      padding: 50,
    },
    minZoom: 0.05,
    maxZoom: 4,
    wheelSensitivity: 0.3,
  });

  cy.ready(() => {
    setLoading(false);
    applyDegreeSizing();
    applyActiveFilters();
    buildFilterUI();
    bindCyEvents();
    cy.one("layoutstop", () => updateStatusBar());
    applyZoomLabels();
  });
}

// ---------------------------------------------------------------------------
// Degree-based node sizing
// ---------------------------------------------------------------------------
function applyDegreeSizing() {
  if (!cy) return;
  cy.batch(() => {
    cy.nodes().forEach((n) => {
      const deg = n.degree(false);
      const sz = Math.min(58, Math.max(26, 26 + Math.sqrt(deg) * 6));
      n.data("sz", sz);
    });
  });
}

// ---------------------------------------------------------------------------
// Adaptive node label visibility based on zoom
// ---------------------------------------------------------------------------
const LABEL_ZOOM_THRESHOLD = 0.35;

function applyZoomLabels() {
  if (!cy) return;
  const zoom = cy.zoom();
  if (zoom >= LABEL_ZOOM_THRESHOLD) {
    cy.nodes().not(".selected, .neighbor").style("label", "data(badgeLabel)");
  } else {
    cy.nodes().not(".selected, .neighbor").style("label", "");
  }
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------
function buildFilterUI() {
  // Kind checkboxes
  elKindFilters.innerHTML = "";
  ALL_KINDS.forEach((k) => {
    const li = document.createElement("li");
    li.className = "filter-item";
    li.innerHTML = `
      <input type="checkbox" id="kind-${k}" ${activeKinds.has(k) ? "checked" : ""} />
      <span class="filter-swatch" style="background:${kindColor(k)}"></span>
      <label for="kind-${k}">${k}</label>`;
    li.querySelector("input").addEventListener("change", (e) => {
      if (e.target.checked) activeKinds.add(k);
      else activeKinds.delete(k);
      applyActiveFilters();
      updateStatusBar();
    });
    elKindFilters.appendChild(li);
  });

  // Relationship checkboxes
  elRelFilters.innerHTML = "";
  ALL_RELS.forEach((r) => {
    const li = document.createElement("li");
    li.className = "filter-item";
    li.innerHTML = `
      <input type="checkbox" id="rel-${r}" ${activeRels.has(r) ? "checked" : ""} />
      <span class="filter-swatch" style="background:${relColor(r)};border-radius:2px"></span>
      <label for="rel-${r}">${relLabel(r)}</label>`;
    li.querySelector("input").addEventListener("change", (e) => {
      if (e.target.checked) activeRels.add(r);
      else activeRels.delete(r);
      applyActiveFilters();
      updateStatusBar();
    });
    elRelFilters.appendChild(li);
  });
}

function applyActiveFilters() {
  if (!cy) return;
  cy.batch(() => {
    cy.nodes().forEach((n) => {
      const k = n.data("kind");
      const manualHide = hiddenNodes.has(n.id());
      if (!activeKinds.has(k) || manualHide) {
        n.style("display", "none");
      } else {
        n.style("display", "element");
      }
    });
    cy.edges().forEach((e) => {
      const r = e.data("type");
      const srcVis = e.source().style("display") !== "none";
      const tgtVis = e.target().style("display") !== "none";
      if (!activeRels.has(r) || !srcVis || !tgtVis) {
        e.style("display", "none");
      } else {
        e.style("display", "element");
      }
    });
  });
}

document.getElementById("btn-reset-filters").addEventListener("click", () => {
  ALL_KINDS.forEach((k) => activeKinds.add(k));
  ALL_RELS.forEach((r) => {
    if (HIDDEN_BY_DEFAULT_RELS.has(r)) activeRels.delete(r);
    else activeRels.add(r);
  });
  hiddenNodes.clear();
  buildFilterUI();
  applyActiveFilters();
  updateStatusBar();
});

// ---------------------------------------------------------------------------
// Cytoscape event bindings
// ---------------------------------------------------------------------------
function bindCyEvents() {
  // ── Click node → select + show detail ──────────────────────────────────
  cy.on("tap", "node", (e) => {
    const node = e.target;
    selectNode(node.id());
    showDetailPanel(node.id());
  });

  // Click background → deselect
  cy.on("tap", (e) => {
    if (e.target === cy) {
      deselect();
    }
  });

  // ── Double-click → expand neighbours ───────────────────────────────────
  cy.on("dblclick", "node", (e) => {
    expandNeighbors(e.target.id());
  });

  // ── Right-click → context menu ─────────────────────────────────────────
  cy.on("cxttap", "node", (e) => {
    e.originalEvent.preventDefault();
    const node = e.target;
    selectNode(node.id());
    const { clientX: x, clientY: y } = e.originalEvent;
    showCtxMenu(x, y, node.id());
  });

  // ── Hover on node → tooltip (also force-show label at any zoom) ─────────
  cy.on("mouseover", "node", (e) => {
    const n = e.target;
    const dependents =
      n.data("callers_count") != null
        ? ` · ${n.data("callers_count")} dependents`
        : "";
    const txt = `${n.data("kind")} · ${n.data("qualified_name")}${dependents}`;
    const pos = e.originalEvent;
    showTooltip(txt, pos.clientX, pos.clientY);
    // Force label visible on hover regardless of zoom
    if (!n.hasClass("selected") && !n.hasClass("neighbor")) {
      n.style("label", "data(name)");
    }
  });
  cy.on("mouseout", "node", (e) => {
    hideTooltip();
    // Restore zoom-based label policy
    const n = e.target;
    if (!n.hasClass("selected") && !n.hasClass("neighbor")) {
      if (cy.zoom() < LABEL_ZOOM_THRESHOLD) n.style("label", "");
    }
  });
  cy.on("mousemove", "node", (e) => {
    const pos = e.originalEvent;
    showTooltip(elTooltip.textContent, pos.clientX, pos.clientY);
  });

  // ── Hover on edge → show label class + tooltip ──────────────────────────
  cy.on("mouseover", "edge", (e) => {
    const ed = e.target;
    ed.addClass("edge-hovered");
    const txt = `${relLabel(ed.data("type"))}  ${ed.source().data("name")} → ${ed.target().data("name")}`;
    const pos = e.originalEvent;
    showTooltip(txt, pos.clientX, pos.clientY);
  });
  cy.on("mouseout", "edge", (e) => {
    e.target.removeClass("edge-hovered");
    hideTooltip();
  });

  // ── Zoom → adaptive labels ───────────────────────────────────────────────
  cy.on("zoom", () => applyZoomLabels());

  // Hide tooltip on pan/zoom
  cy.on("pan zoom", hideTooltip);
}

// ---------------------------------------------------------------------------
// Quick Seeds bar
// ---------------------------------------------------------------------------
function renderQuickSeeds(seeds) {
  const elSeeds = document.getElementById("quick-seeds");
  if (!elSeeds) return;
  elSeeds.innerHTML = '<span class="seed-label">Explore seeds:</span>';

  seeds.forEach((s) => {
    const chip = document.createElement("button");
    chip.className = "seed-chip";
    chip.title = `Add ${s.qualified_name} to canvas`;
    const glyph = kindGlyph(s.kind);
    const dname = getDisplayName(s);
    chip.innerHTML = `<span class="seed-glyph" style="background:${kindColor(s.kind)}">${glyph}</span> <span class="seed-text">${dname}</span>`;
    chip.addEventListener("click", () => {
      addSymbolSeed(s.id);
    });
    elSeeds.appendChild(chip);
  });
}

async function addSymbolSeed(id) {
  if (!cy) return;
  let node = cy.getElementById(id);
  if (!node.length) {
    // Look up in rawGraphData
    const sym = rawGraphData.nodes.find((n) => n.id === id);
    if (sym) {
      cy.add(buildElements([sym], []));
      applyDegreeSizing();
      applyActiveFilters();
      updateStatusBar();
    }
  }
  // Automatically expand 1-hop neighbours for this seed
  await expandNeighbors(id, "both");
  node = cy.getElementById(id);
  if (node.length) {
    cy.animate(
      { center: { eles: node }, zoom: Math.max(cy.zoom(), 1.2) },
      { duration: 400 },
    );
    selectNode(id);
    showDetailPanel(id);
  }
}

// ---------------------------------------------------------------------------
// Node selection / focus
// ---------------------------------------------------------------------------
function selectNode(id) {
  if (!cy) return;
  cy.nodes().removeClass("selected neighbor dimmed highlighted");
  cy.edges().removeClass("dimmed highlighted");

  selectedNodeId = id;
  const node = cy.getElementById(id);
  if (!node.length) return;
  node.addClass("selected");

  // Highlight direct neighbours in the selected direction while preserving edge arrows.
  const outgoing = node.outgoers("edge").union(node.outgoers("node"));
  const incoming = node.incomers("edge").union(node.incomers("node"));
  const connected =
    activeDirection === "out"
      ? node.union(outgoing)
      : activeDirection === "in"
        ? node.union(incoming)
        : node.closedNeighborhood();
  connected.nodes().not(node).addClass("neighbor");
  (activeDirection === "out"
    ? outgoing
    : activeDirection === "in"
      ? incoming
      : node.closedNeighborhood()
  )
    .edges()
    .addClass("highlighted");

  // Dim everything else
  cy.nodes().not(connected).addClass("dimmed");
  cy.edges().not(connected).addClass("dimmed");

  updateStatusBar();
}

function deselect() {
  if (!cy) return;
  cy.nodes().removeClass("selected neighbor dimmed highlighted");
  cy.edges().removeClass("dimmed highlighted");
  selectedNodeId = null;
  elDetailPanel.hidden = true;
  elStatusSel.textContent = "";
  updateStatusBar();
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------
async function showDetailPanel(id) {
  elDetailPanel.hidden = false;
  elDetailName.textContent = "…";
  elDetailKind.textContent = "";

  try {
    const data = await apiFetch(`/api/symbol/${encodeURIComponent(id)}`);
    const sym = data.symbol;

    elDetailName.textContent = getDisplayName(sym);
    elDetailKind.textContent = `${kindGlyph(sym.kind)} ${sym.kind}`;
    elDetailKind.style.color = kindColor(sym.kind);
    elDetailFile.textContent = sym.file_path
      ? sym.file_path.split(/[/\\]/).slice(-2).join("/")
      : "—";
    elDetailLines.textContent = `${sym.start_line}–${sym.end_line}`;

    if (sym.signature) {
      elDetailSig.textContent = sym.signature;
      elDetailSigRow.hidden = false;
    } else {
      elDetailSigRow.hidden = true;
    }

    const inTotal = Object.values(data.counts.inbound || {}).reduce(
      (a, b) => a + b,
      0,
    );
    const outTotal = Object.values(data.counts.outbound || {}).reduce(
      (a, b) => a + b,
      0,
    );
    elDetailInCount.textContent = inTotal;
    elDetailOutCount.textContent = outTotal;

    function renderRelList(list, el) {
      el.innerHTML = "";
      if (!list || list.length === 0) {
        el.innerHTML = '<li class="detail-rel-item empty">None</li>';
        return;
      }
      list.forEach((r) => {
        const li = document.createElement("li");
        li.className = "detail-rel-item";
        const isProb = r.resolution === "PROBABLE";
        const glyph = kindGlyph(r.kind);
        li.innerHTML = `
          <span class="rel-badge" style="background:${relColor(r.rel_type)}; opacity: ${isProb ? "0.7" : "1"}">${relLabel(r.rel_type)}</span>
          <span class="rel-target-glyph" style="color:${kindColor(r.kind)}">${glyph}</span>
          <span class="rel-target-name ${isProb ? "probable" : ""}" title="${isProb ? "Probable match" : ""}">${r.name}</span>
        `;
        li.addEventListener("click", () => {
          addSymbolSeed(r.id);
        });
        el.appendChild(li);
      });
    }

    renderRelList(data.callers, elDetailCallersList);
    renderRelList(data.callees, elDetailCalleesList);

    if (data.unresolved_facts && data.unresolved_facts.length > 0) {
      elDetailUnresolvedWrap.hidden = false;
      elDetailUnresolvedList.innerHTML = "";
      data.unresolved_facts.forEach((f) => {
        const li = document.createElement("li");
        li.className = "detail-unresolved-item";
        li.innerHTML = `<span class="unresolved-type">${f.fact_type}</span> <span class="unresolved-text">${f.raw_text}</span>`;
        if (f.line) li.title = `Line ${f.line}`;
        elDetailUnresolvedList.appendChild(li);
      });
    } else {
      elDetailUnresolvedWrap.hidden = true;
    }

    if (sym.docstring) {
      elDetailDoc.textContent = sym.docstring;
      elDetailDocWrap.hidden = false;
    } else {
      elDetailDocWrap.hidden = true;
    }

    if (data.source_lines && data.source_lines.length) {
      elDetailSrcCode.textContent = data.source_lines.join("\n");
      elDetailSrcWrap.hidden = false;
    } else {
      elDetailSrcWrap.hidden = true;
    }
  } catch (err) {
    elDetailName.textContent = "Error loading symbol";
    console.error(err);
  }
}

// Buttons in detail panel
document.getElementById("btn-expand-node").addEventListener("click", () => {
  if (selectedNodeId) expandNeighbors(selectedNodeId, activeDirection);
});

const btnExpandCallers = document.getElementById("btn-expand-callers");
if (btnExpandCallers) {
  btnExpandCallers.addEventListener("click", () => {
    if (selectedNodeId) expandNeighbors(selectedNodeId, "in");
  });
}

const btnExpandCallees = document.getElementById("btn-expand-callees");
if (btnExpandCallees) {
  btnExpandCallees.addEventListener("click", () => {
    if (selectedNodeId) expandNeighbors(selectedNodeId, "out");
  });
}

document.getElementById("btn-close-detail").addEventListener("click", deselect);

directionButtons.forEach((button) => {
  button.addEventListener("click", () =>
    setDirection(button.dataset.direction),
  );
});

// ---------------------------------------------------------------------------
// Expand neighbours (lazy / progressive)
// ---------------------------------------------------------------------------
async function expandNeighbors(id, direction = "both") {
  try {
    const data = await apiFetch(
      `/api/neighbors/${encodeURIComponent(id)}?direction=${direction}`,
    );
    if (!data.nodes.length && !data.edges.length) return;

    const existingIds = new Set(cy.nodes().map((n) => n.id()));
    const newNodes = data.nodes.filter(
      (n) => !existingIds.has(n.id) && !HIDDEN_BY_DEFAULT_KINDS.has(n.kind),
    );

    const existingEdgeIds = new Set(cy.edges().map((e) => e.id()));
    const newEdges = data.edges.filter(
      (e) => !existingEdgeIds.has(e.id) && !HIDDEN_BY_DEFAULT_RELS.has(e.type),
    );

    if (!newNodes.length && !newEdges.length) return;

    const newEls = buildElements(newNodes, newEdges);
    newEls.forEach((el) => {
      if (el.group === "nodes") el.classes = "new";
    });

    cy.add(newEls);
    applyDegreeSizing();
    applyActiveFilters();

    // Run localized physics layout to fit new nodes smoothly
    cy.layout({
      name: "cose",
      animate: true,
      animationDuration: 500,
      randomize: false,
      fit: false,
      nodeRepulsion: () => 12000,
      idealEdgeLength: () => 90,
      gravity: 0.5,
    }).run();

    updateStatusBar();
    setTimeout(() => cy.nodes(".new").removeClass("new"), 1800);
  } catch (err) {
    console.error("expandNeighbors error:", err);
  }
}

// ---------------------------------------------------------------------------
// Context menu
// ---------------------------------------------------------------------------
let ctxTargetId = null;

function showCtxMenu(x, y, nodeId) {
  ctxTargetId = nodeId;
  elCtxMenu.style.left = x + "px";
  elCtxMenu.style.top = y + "px";
  elCtxMenu.hidden = false;
}

function hideCtxMenu() {
  elCtxMenu.hidden = true;
  ctxTargetId = null;
}

document.getElementById("ctx-expand").addEventListener("click", () => {
  if (ctxTargetId) expandNeighbors(ctxTargetId, "both");
  hideCtxMenu();
});

document.getElementById("ctx-focus").addEventListener("click", () => {
  if (!ctxTargetId || !cy) {
    hideCtxMenu();
    return;
  }
  focusedNodeId = ctxTargetId;
  const node = cy.getElementById(ctxTargetId);
  const subtree = node.closedNeighborhood();
  cy.nodes().not(subtree).addClass("dimmed");
  cy.edges().not(subtree).addClass("dimmed");
  subtree.removeClass("dimmed").addClass("highlighted");
  hideCtxMenu();
});

document.getElementById("ctx-hide").addEventListener("click", () => {
  if (!ctxTargetId || !cy) {
    hideCtxMenu();
    return;
  }
  hiddenNodes.add(ctxTargetId);
  applyActiveFilters();
  updateStatusBar();
  hideCtxMenu();
});

document.getElementById("ctx-reset-focus").addEventListener("click", () => {
  if (!cy) {
    hideCtxMenu();
    return;
  }
  focusedNodeId = null;
  cy.nodes().removeClass("dimmed highlighted selected neighbor");
  cy.edges().removeClass("dimmed highlighted");
  selectedNodeId = null;
  elDetailPanel.hidden = true;
  updateStatusBar();
  hideCtxMenu();
});

// Close context menu on outside click
document.addEventListener("click", (e) => {
  if (!elCtxMenu.contains(e.target)) hideCtxMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    hideCtxMenu();
    deselect();
    closeSearch();
  }
});

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------
const doSearch = debounce(async (q) => {
  if (!q.trim()) {
    closeSearch();
    return;
  }
  try {
    const data = await apiFetch(`/api/search?q=${encodeURIComponent(q)}`);
    renderSearchResults(data.results || []);
  } catch {
    closeSearch();
  }
}, 220);

elSearchInput.addEventListener("input", (e) => doSearch(e.target.value));
elSearchInput.addEventListener("focus", () => {
  if (elSearchInput.value.trim()) doSearch(elSearchInput.value);
});

function renderSearchResults(results) {
  elSearchRes.innerHTML = "";
  if (!results.length) {
    elSearchRes.hidden = true;
    return;
  }

  // Exclude raw file symbols from instant search suggestions unless explicitly requested
  const filtered = results.filter((r) => !HIDDEN_BY_DEFAULT_KINDS.has(r.kind));
  const listToRender = filtered.length ? filtered : results;

  listToRender.slice(0, 10).forEach((r, i) => {
    const div = document.createElement("div");
    div.className = "search-result-item";
    div.setAttribute("role", "option");
    div.setAttribute("id", `sr-${i}`);
    const glyph = kindGlyph(r.kind);
    div.innerHTML = `
      <span class="search-result-kind" style="background:${kindColor(r.kind)};color:#0d0f14;font-weight:bold">${glyph}</span>
      <span class="search-result-name">${r.qualified_name}</span>
      <span class="search-result-score">${(r.score * 100).toFixed(0)}%</span>`;
    div.addEventListener("click", () => {
      addSymbolSeed(r.id);
      closeSearch();
    });
    elSearchRes.appendChild(div);
  });

  elSearchRes.hidden = false;
}

function closeSearch() {
  elSearchRes.hidden = true;
}

// ---------------------------------------------------------------------------
// Canvas management: Clear & Full Graph toggles
// ---------------------------------------------------------------------------
function clearCanvas() {
  if (!cy) return;
  deselect();
  cy.elements().remove();
  updateStatusBar();
}

function loadFullGraph() {
  if (!cy || !rawGraphData.nodes.length) return;
  deselect();
  const elements = buildElements(rawGraphData.nodes, rawGraphData.edges);
  cy.elements().remove();
  cy.add(elements);
  applyDegreeSizing();
  applyActiveFilters();
  cy.layout({
    name: "cose",
    animate: true,
    animationDuration: 800,
    randomize: true,
    nodeRepulsion: () => 14000,
    idealEdgeLength: () => 110,
    nodeOverlap: 20,
    gravity: 0.5,
  }).run();
  updateStatusBar();
}

// ---------------------------------------------------------------------------
// Top-bar buttons
// ---------------------------------------------------------------------------
document.getElementById("btn-fit").addEventListener("click", () => {
  cy && cy.fit(undefined, 40);
});

document.getElementById("btn-layout").addEventListener("click", () => {
  if (!cy) return;
  cy.layout({
    name: "cose",
    animate: true,
    randomize: false,
    nodeRepulsion: () => 14000,
    idealEdgeLength: () => 110,
    nodeOverlap: 20,
    gravity: 0.5,
  }).run();
});

const btnClear = document.getElementById("btn-clear");
if (btnClear) {
  btnClear.addEventListener("click", clearCanvas);
}

const btnFullGraph = document.getElementById("btn-full-graph");
if (btnFullGraph) {
  btnFullGraph.addEventListener("click", loadFullGraph);
}

document.getElementById("btn-filter-toggle").addEventListener("click", (e) => {
  const btn = e.currentTarget;
  const isOpen = !elFilterPanel.hidden;
  elFilterPanel.hidden = isOpen;
  btn.setAttribute("aria-expanded", String(!isOpen));
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
window.addEventListener("DOMContentLoaded", init);
