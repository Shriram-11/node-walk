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

'use strict';

// ---------------------------------------------------------------------------
// Design tokens (keep in sync with style.css CSS variables)
// ---------------------------------------------------------------------------
const KIND_COLORS = {
  CLASS:     '#4FC3F7',
  INTERFACE: '#7E57C2',
  FUNCTION:  '#66BB6A',
  METHOD:    '#FFA726',
  MODULE:    '#AB47BC',
  FILE:      '#AB47BC',
  CONSTANT:  '#FFEE58',
  VARIABLE:  '#BDBDBD',
  FIELD:     '#BDBDBD',
};

const REL_COLORS = {
  CALLS:      '#42A5F5',
  IMPORTS:    '#AB47BC',
  EXTENDS:    '#66BB6A',
  IMPLEMENTS: '#26A69A',
  REFERENCES: '#9E9E9E',
  CONTAINS:   '#E0E0E0',
};

const KIND_SHAPES = {
  CLASS:     'round-rectangle',
  INTERFACE: 'diamond',
  FUNCTION:  'ellipse',
  METHOD:    'ellipse',
  MODULE:    'rectangle',
  FILE:      'rectangle',
  CONSTANT:  'rectangle',
  VARIABLE:  'ellipse',
  FIELD:     'ellipse',
};

const ALL_KINDS = Object.keys(KIND_COLORS);
const ALL_RELS  = Object.keys(REL_COLORS);

// Relationships hidden by default (can be toggled on via filter)
const HIDDEN_BY_DEFAULT_RELS = new Set(['CONTAINS']);

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let cy;                          // Cytoscape instance
let selectedNodeId = null;       // currently selected node id
let hiddenNodes = new Set();     // manually hidden node ids
let focusedNodeId = null;        // node being "focused" (subtree highlight)

const activeKinds = new Set(ALL_KINDS);
const activeRels  = new Set(ALL_RELS.filter(r => !HIDDEN_BY_DEFAULT_RELS.has(r)));

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const elLoading      = document.getElementById('cy-loading');
const elEmpty        = document.getElementById('cy-empty');
const elDetailPanel  = document.getElementById('detail-panel');
const elFilterPanel  = document.getElementById('filter-panel');
const elSearchInput  = document.getElementById('search-input');
const elSearchRes    = document.getElementById('search-results');
const elStatusNodes  = document.getElementById('status-nodes');
const elStatusEdges  = document.getElementById('status-edges');
const elStatusSel    = document.getElementById('status-selected');
const elTooltip      = document.getElementById('tooltip');
const elCtxMenu      = document.getElementById('ctx-menu');
const elKindFilters  = document.getElementById('kind-filters');
const elRelFilters   = document.getElementById('rel-filters');
const elDetailName   = document.getElementById('detail-name');
const elDetailKind   = document.getElementById('detail-kind');
const elDetailFile   = document.getElementById('detail-file');
const elDetailLines  = document.getElementById('detail-lines');
const elDetailSig    = document.getElementById('detail-sig');
const elDetailSigRow = document.getElementById('detail-sig-row');
const elDetailCallers = document.getElementById('detail-callers');
const elDetailCallees = document.getElementById('detail-callees');
const elDetailDoc    = document.getElementById('detail-docstring');
const elDetailDocWrap = document.getElementById('detail-docstring-wrap');
const elDetailSrcCode = document.getElementById('detail-source-code');
const elDetailSrcWrap = document.getElementById('detail-source-wrap');

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
function kindColor(kind) { return KIND_COLORS[kind] || '#6b7280'; }
function relColor(rel)   { return REL_COLORS[rel]   || '#9E9E9E'; }
function kindShape(kind) { return KIND_SHAPES[kind]  || 'ellipse'; }

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

async function apiFetch(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`API ${path} → ${r.status}`);
  return r.json();
}

function setLoading(on) {
  elLoading.classList.toggle('hidden', !on);
}

function updateStatusBar() {
  if (!cy) return;
  const visNodes = cy.nodes(':visible').length;
  const visEdges = cy.edges(':visible').length;
  elStatusNodes.textContent = `${visNodes} nodes`;
  elStatusEdges.textContent = `${visEdges} edges`;
  if (selectedNodeId) {
    const n = cy.getElementById(selectedNodeId);
    elStatusSel.textContent = n.length ? n.data('qualified_name') : '';
  } else {
    elStatusSel.textContent = '';
  }
}

function hideTooltip() {
  elTooltip.hidden = true;
}

function showTooltip(text, x, y) {
  elTooltip.textContent = text;
  elTooltip.hidden = false;
  // Keep tooltip within viewport
  const tw = elTooltip.offsetWidth;
  const th = elTooltip.offsetHeight;
  elTooltip.style.left = Math.min(x + 12, window.innerWidth - tw - 8) + 'px';
  elTooltip.style.top  = Math.max(y - th - 8, 8) + 'px';
}

// ---------------------------------------------------------------------------
// Cytoscape stylesheet
// ---------------------------------------------------------------------------
function buildStylesheet() {
  const nodeStyles = ALL_KINDS.map(k => ({
    selector: `node[kind="${k}"]`,
    style: {
      'background-color': kindColor(k),
      'shape': kindShape(k),
    },
  }));

  const edgeStyles = ALL_RELS.map(r => ({
    selector: `edge[type="${r}"]`,
    style: {
      'line-color':   relColor(r),
      'target-arrow-color': relColor(r),
      'line-style':   ['IMPORTS','IMPLEMENTS','REFERENCES','CONTAINS'].includes(r) ? 'dashed' : 'solid',
      'width':        r === 'EXTENDS' ? 3 : r === 'CONTAINS' ? 1 : 2,
    },
  }));

  return [
    {
      selector: 'node',
      style: {
        'label': 'data(name)',
        'font-size': 11,
        'font-family': "'JetBrains Mono', monospace",
        'color': '#e8eaf0',
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 4,
        'text-outline-color': '#0d0f14',
        'text-outline-width': 2,
        'width': 36,
        'height': 36,
        'border-width': 2,
        'border-color': 'rgba(255,255,255,0.12)',
        'background-color': '#6b7280',
        'transition-property': 'opacity, border-color, border-width',
        'transition-duration': '150ms',
      },
    },
    {
      selector: 'edge',
      style: {
        'curve-style': 'bezier',
        'target-arrow-shape': 'triangle',
        'arrow-scale': 1,
        'line-color':   '#9E9E9E',
        'target-arrow-color': '#9E9E9E',
        'width': 1.5,
        'label': 'data(type)',
        'font-size': 9,
        'font-family': "'Inter', sans-serif",
        'color': 'rgba(255,255,255,0.35)',
        'text-rotation': 'autorotate',
        'text-margin-y': -6,
        'text-outline-color': '#0d0f14',
        'text-outline-width': 1.5,
        'transition-property': 'opacity',
        'transition-duration': '150ms',
      },
    },
    ...nodeStyles,
    ...edgeStyles,
    // Selected node
    {
      selector: 'node.selected',
      style: {
        'border-width': 3,
        'border-color': '#fff',
        'z-index': 999,
      },
    },
    // Neighbor highlight
    {
      selector: 'node.neighbor',
      style: {
        'border-width': 2,
        'border-color': 'rgba(255,255,255,0.5)',
      },
    },
    // Dimmed (focus mode)
    {
      selector: 'node.dimmed, edge.dimmed',
      style: { 'opacity': 0.12 },
    },
    {
      selector: 'node.highlighted, edge.highlighted',
      style: { 'opacity': 1 },
    },
    // New nodes added via expand
    {
      selector: 'node.new',
      style: {
        'border-color': '#4f9dff',
        'border-width': 3,
      },
    },
  ];
}

// ---------------------------------------------------------------------------
// Cytoscape elements from API data
// ---------------------------------------------------------------------------
function buildElements(nodes, edges) {
  const cyNodes = nodes.map(n => ({
    group: 'nodes',
    data: { id: n.id, ...n },
  }));
  const cyEdges = edges.map(e => ({
    group: 'edges',
    data: { id: e.id, source: e.source, target: e.target, type: e.type, resolution: e.resolution },
  }));
  return [...cyNodes, ...cyEdges];
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
async function init() {
  setLoading(true);
  let graphData;
  try {
    graphData = await apiFetch('/api/graph');
  } catch (err) {
    setLoading(false);
    elEmpty.hidden = false;
    elLoading.classList.add('hidden');
    console.error('Failed to load graph:', err);
    return;
  }

  if (!graphData.nodes || graphData.nodes.length === 0) {
    setLoading(false);
    elEmpty.hidden = false;
    elLoading.classList.add('hidden');
    return;
  }

  const elements = buildElements(graphData.nodes, graphData.edges);

  cy = cytoscape({
    container: document.getElementById('cy'),
    elements,
    style: buildStylesheet(),
    layout: { name: 'cose', animate: true, randomize: true, nodeRepulsion: 8000, idealEdgeLength: 80, gravity: 0.4 },
    minZoom: 0.05,
    maxZoom: 4,
    wheelSensitivity: 0.3,
  });

  cy.ready(() => {
    setLoading(false);
    applyActiveFilters();
    updateStatusBar();
    buildFilterUI();
    bindCyEvents();
  });
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------
function buildFilterUI() {
  // Kind checkboxes
  elKindFilters.innerHTML = '';
  ALL_KINDS.forEach(k => {
    const li  = document.createElement('li');
    li.className = 'filter-item';
    li.innerHTML = `
      <input type="checkbox" id="kind-${k}" ${activeKinds.has(k) ? 'checked' : ''} />
      <span class="filter-swatch" style="background:${kindColor(k)}"></span>
      <label for="kind-${k}">${k}</label>`;
    li.querySelector('input').addEventListener('change', e => {
      if (e.target.checked) activeKinds.add(k); else activeKinds.delete(k);
      applyActiveFilters();
      updateStatusBar();
    });
    elKindFilters.appendChild(li);
  });

  // Relationship checkboxes
  elRelFilters.innerHTML = '';
  ALL_RELS.forEach(r => {
    const li  = document.createElement('li');
    li.className = 'filter-item';
    li.innerHTML = `
      <input type="checkbox" id="rel-${r}" ${activeRels.has(r) ? 'checked' : ''} />
      <span class="filter-swatch" style="background:${relColor(r)};border-radius:2px"></span>
      <label for="rel-${r}">${r}</label>`;
    li.querySelector('input').addEventListener('change', e => {
      if (e.target.checked) activeRels.add(r); else activeRels.delete(r);
      applyActiveFilters();
      updateStatusBar();
    });
    elRelFilters.appendChild(li);
  });
}

function applyActiveFilters() {
  if (!cy) return;
  cy.batch(() => {
    cy.nodes().forEach(n => {
      const k = n.data('kind');
      const manualHide = hiddenNodes.has(n.id());
      if (!activeKinds.has(k) || manualHide) {
        n.style('display', 'none');
      } else {
        n.style('display', 'element');
      }
    });
    cy.edges().forEach(e => {
      const r = e.data('type');
      const srcVis = e.source().style('display') !== 'none';
      const tgtVis = e.target().style('display') !== 'none';
      if (!activeRels.has(r) || !srcVis || !tgtVis) {
        e.style('display', 'none');
      } else {
        e.style('display', 'element');
      }
    });
  });
}

document.getElementById('btn-reset-filters').addEventListener('click', () => {
  ALL_KINDS.forEach(k => activeKinds.add(k));
  ALL_RELS.forEach(r => {
    if (HIDDEN_BY_DEFAULT_RELS.has(r)) activeRels.delete(r); else activeRels.add(r);
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
  cy.on('tap', 'node', e => {
    const node = e.target;
    selectNode(node.id());
    showDetailPanel(node.id());
  });

  // Click background → deselect
  cy.on('tap', e => {
    if (e.target === cy) {
      deselect();
    }
  });

  // ── Double-click → expand neighbours ───────────────────────────────────
  cy.on('dblclick', 'node', e => {
    expandNeighbors(e.target.id());
  });

  // ── Right-click → context menu ─────────────────────────────────────────
  cy.on('cxttap', 'node', e => {
    e.originalEvent.preventDefault();
    const node = e.target;
    selectNode(node.id());
    const { clientX: x, clientY: y } = e.originalEvent;
    showCtxMenu(x, y, node.id());
  });

  // ── Hover → tooltip ────────────────────────────────────────────────────
  cy.on('mouseover', 'node', e => {
    const n = e.target;
    const txt = `${n.data('kind')} · ${n.data('qualified_name')}`;
    const pos = e.originalEvent;
    showTooltip(txt, pos.clientX, pos.clientY);
  });
  cy.on('mouseout', 'node', hideTooltip);
  cy.on('mousemove', 'node', e => {
    const pos = e.originalEvent;
    showTooltip(elTooltip.textContent, pos.clientX, pos.clientY);
  });

  cy.on('mouseover', 'edge', e => {
    const ed = e.target;
    const txt = `${ed.data('type')} · ${ed.source().data('name')} → ${ed.target().data('name')}`;
    const pos = e.originalEvent;
    showTooltip(txt, pos.clientX, pos.clientY);
  });
  cy.on('mouseout', 'edge', hideTooltip);

  // Hide tooltip on pan/zoom
  cy.on('pan zoom', hideTooltip);
}

// ---------------------------------------------------------------------------
// Node selection / focus
// ---------------------------------------------------------------------------
function selectNode(id) {
  if (!cy) return;
  cy.nodes().removeClass('selected neighbor dimmed highlighted');
  cy.edges().removeClass('dimmed highlighted');

  selectedNodeId = id;
  const node = cy.getElementById(id);
  node.addClass('selected');

  // Highlight direct neighbours
  const connected = node.closedNeighborhood();
  connected.nodes().not(node).addClass('neighbor');
  connected.edges().addClass('highlighted');

  // Dim everything else
  cy.nodes().not(connected).addClass('dimmed');
  cy.edges().not(connected).addClass('dimmed');

  updateStatusBar();
}

function deselect() {
  if (!cy) return;
  cy.nodes().removeClass('selected neighbor dimmed highlighted');
  cy.edges().removeClass('dimmed highlighted');
  selectedNodeId = null;
  elDetailPanel.hidden = true;
  elStatusSel.textContent = '';
  updateStatusBar();
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------
async function showDetailPanel(id) {
  elDetailPanel.hidden = false;
  // Show a loading placeholder
  elDetailName.textContent = '…';
  elDetailKind.textContent = '';

  try {
    const data = await apiFetch(`/api/symbol/${encodeURIComponent(id)}`);
    const sym = data.symbol;

    elDetailName.textContent = sym.name;
    elDetailKind.textContent = sym.kind;
    elDetailKind.style.color = kindColor(sym.kind);
    elDetailFile.textContent = sym.file_path ? sym.file_path.split(/[/\\]/).slice(-2).join('/') : '—';
    elDetailLines.textContent = `${sym.start_line}–${sym.end_line}`;

    if (sym.signature) {
      elDetailSig.textContent = sym.signature;
      elDetailSigRow.hidden = false;
    } else {
      elDetailSigRow.hidden = true;
    }

    elDetailCallers.textContent = data.callers_count;
    elDetailCallees.textContent = data.callees_count;

    if (sym.docstring) {
      elDetailDoc.textContent = sym.docstring;
      elDetailDocWrap.hidden = false;
    } else {
      elDetailDocWrap.hidden = true;
    }

    if (data.source_lines && data.source_lines.length) {
      elDetailSrcCode.textContent = data.source_lines.join('\n');
      elDetailSrcWrap.hidden = false;
    } else {
      elDetailSrcWrap.hidden = true;
    }
  } catch (err) {
    elDetailName.textContent = 'Error loading symbol';
    console.error(err);
  }
}

// Button: expand from detail panel
document.getElementById('btn-expand-node').addEventListener('click', () => {
  if (selectedNodeId) expandNeighbors(selectedNodeId);
});

document.getElementById('btn-close-detail').addEventListener('click', deselect);

// ---------------------------------------------------------------------------
// Expand neighbours (lazy)
// ---------------------------------------------------------------------------
async function expandNeighbors(id) {
  try {
    const data = await apiFetch(`/api/neighbors/${encodeURIComponent(id)}?direction=both`);
    if (!data.nodes.length && !data.edges.length) return;

    const existingIds = new Set(cy.nodes().map(n => n.id()));
    const newEls = [];

    data.nodes.forEach(n => {
      if (!existingIds.has(n.id)) {
        newEls.push({ group: 'nodes', data: { id: n.id, ...n }, classes: 'new' });
      }
    });

    const existingEdgeIds = new Set(cy.edges().map(e => e.id()));
    data.edges.forEach(e => {
      if (!existingEdgeIds.has(e.id)) {
        newEls.push({ group: 'edges', data: { id: e.id, source: e.source, target: e.target, type: e.type, resolution: e.resolution } });
      }
    });

    if (!newEls.length) return;

    cy.add(newEls);
    // Run a local layout around the expanded node only
    cy.layout({ name: 'cose', animate: true, randomize: false, fit: false }).run();
    applyActiveFilters();
    updateStatusBar();

    // Remove 'new' class after animation
    setTimeout(() => cy.nodes('.new').removeClass('new'), 2000);
  } catch (err) {
    console.error('expandNeighbors error:', err);
  }
}

// ---------------------------------------------------------------------------
// Context menu
// ---------------------------------------------------------------------------
let ctxTargetId = null;

function showCtxMenu(x, y, nodeId) {
  ctxTargetId = nodeId;
  elCtxMenu.style.left = x + 'px';
  elCtxMenu.style.top  = y + 'px';
  elCtxMenu.hidden = false;
}

function hideCtxMenu() {
  elCtxMenu.hidden = true;
  ctxTargetId = null;
}

document.getElementById('ctx-expand').addEventListener('click', () => {
  if (ctxTargetId) expandNeighbors(ctxTargetId);
  hideCtxMenu();
});

document.getElementById('ctx-focus').addEventListener('click', () => {
  if (!ctxTargetId || !cy) { hideCtxMenu(); return; }
  focusedNodeId = ctxTargetId;
  const node = cy.getElementById(ctxTargetId);
  const subtree = node.closedNeighborhood();
  cy.nodes().not(subtree).addClass('dimmed');
  cy.edges().not(subtree).addClass('dimmed');
  subtree.removeClass('dimmed').addClass('highlighted');
  hideCtxMenu();
});

document.getElementById('ctx-hide').addEventListener('click', () => {
  if (!ctxTargetId || !cy) { hideCtxMenu(); return; }
  hiddenNodes.add(ctxTargetId);
  applyActiveFilters();
  updateStatusBar();
  hideCtxMenu();
});

document.getElementById('ctx-reset-focus').addEventListener('click', () => {
  if (!cy) { hideCtxMenu(); return; }
  focusedNodeId = null;
  cy.nodes().removeClass('dimmed highlighted selected neighbor');
  cy.edges().removeClass('dimmed highlighted');
  selectedNodeId = null;
  elDetailPanel.hidden = true;
  updateStatusBar();
  hideCtxMenu();
});

// Close context menu on outside click
document.addEventListener('click', e => {
  if (!elCtxMenu.contains(e.target)) hideCtxMenu();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { hideCtxMenu(); deselect(); closeSearch(); }
});

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------
const doSearch = debounce(async (q) => {
  if (!q.trim()) { closeSearch(); return; }
  try {
    const data = await apiFetch(`/api/search?q=${encodeURIComponent(q)}`);
    renderSearchResults(data.results || []);
  } catch { closeSearch(); }
}, 220);

elSearchInput.addEventListener('input', e => doSearch(e.target.value));
elSearchInput.addEventListener('focus', () => {
  if (elSearchInput.value.trim()) doSearch(elSearchInput.value);
});

function renderSearchResults(results) {
  elSearchRes.innerHTML = '';
  if (!results.length) { elSearchRes.hidden = true; return; }

  results.slice(0, 10).forEach((r, i) => {
    const div = document.createElement('div');
    div.className = 'search-result-item';
    div.setAttribute('role', 'option');
    div.setAttribute('id', `sr-${i}`);
    div.innerHTML = `
      <span class="search-result-kind" style="color:${kindColor(r.kind)}">${r.kind}</span>
      <span class="search-result-name">${r.qualified_name}</span>
      <span class="search-result-score">${(r.score * 100).toFixed(0)}%</span>`;
    div.addEventListener('click', () => {
      navigateToSymbol(r.id);
      closeSearch();
    });
    elSearchRes.appendChild(div);
  });

  elSearchRes.hidden = false;
}

function closeSearch() {
  elSearchRes.hidden = true;
}

function navigateToSymbol(id) {
  if (!cy) return;
  const node = cy.getElementById(id);
  if (!node.length) return;
  cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.5) }, { duration: 400 });
  selectNode(id);
  showDetailPanel(id);
}

// ---------------------------------------------------------------------------
// Top-bar buttons
// ---------------------------------------------------------------------------
document.getElementById('btn-fit').addEventListener('click', () => {
  cy && cy.fit(undefined, 40);
});

document.getElementById('btn-layout').addEventListener('click', () => {
  cy && cy.layout({ name: 'cose', animate: true }).run();
});

document.getElementById('btn-filter-toggle').addEventListener('click', e => {
  const btn = e.currentTarget;
  const isOpen = !elFilterPanel.hidden;
  elFilterPanel.hidden = isOpen;
  btn.setAttribute('aria-expanded', String(!isOpen));
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', init);
