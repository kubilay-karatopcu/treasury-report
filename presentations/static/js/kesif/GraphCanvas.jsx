/* Phase 9.b.1 — Cosmograph WebGL graph for Keşif (Atölye / Discover).
 *
 * Renders /presentations/catalog/graph as a force-directed network. No
 * semantic zoom in this sub-phase — every node renders at the same level.
 * Phase 9.b.2 adds macro/meso/micro layers on top of this same component.
 *
 * Cosmograph 2.x API notes:
 * - Data flows as columnar arrays (`points`, `links`) referenced by name
 *   via `pointIdBy` / `linkSourceBy` / `linkTargetBy` accessor strings.
 * - Event callbacks fire with INDEX (number), not the node object — we
 *   maintain a parallel id↔index map.
 * - Selection / focus is imperative via `useCosmograph()` hook or `ref`.
 * - WebGL line rendering doesn't support dashed strokes; we differentiate
 *   `shared_concept` edges via lower opacity + thinner width instead.
 *
 * License gate: the parent App only mounts this when DATA.flags.use_cosmograph
 * is true. Passing `licenseKey` unlocks commercial use once procured.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Cosmograph, CosmographProvider } from "@cosmograph/react";
import { Plus, X, MousePointer2 } from "lucide-react";


// Cluster-color palette — keyed by department so a "Treasury" node always
// renders in the same hue. Cycled stably so adding new departments doesn't
// reshuffle existing colors.
const DEPT_PALETTE = [
  "#2563eb", // blue
  "#0891b2", // cyan
  "#16a34a", // green
  "#3730a3", // indigo
  "#dc2626", // red
  "#9333ea", // purple
  "#475569", // slate (fallback)
];
// Concept hubs in dark red — bumped from yellow which was reading as
// pink/mauve through the user's colour-vision profile. Dark red has
// the highest contrast against any blue dept palette + against the
// off-white background, so the table↔hub distinction stays crisp.
const CONCEPT_COLOR = "#b91c1c";   // red-700 (dark red)
// User uploads in pink — won't collide with concept red or any dept.
const UPLOAD_COLOR = "#db2777";    // pink
const DEFAULT_COLOR = "#94a3b8";   // neutral grey

function colorForNode(node, deptIndex) {
  if (node.type === "concept") return CONCEPT_COLOR;
  if (node.source === "user_upload") return UPLOAD_COLOR;
  if (!node.department) return DEFAULT_COLOR;
  const idx = deptIndex.get(node.department) ?? 0;
  return DEPT_PALETTE[idx % DEPT_PALETTE.length];
}

// Per-node weight that Cosmograph linearly remaps into pointSizeRange.
//   - Tables: concept-binding count, capped at TABLE_WEIGHT_MAX.
//   - Concepts: usage_count + an offset so the *smallest* concept is
//     larger than the *biggest* table. The floor was bumped per user
//     feedback — even a one-table concept needs to read as a hub at a
//     glance, not as a sibling node.
//
// With pointSizeRange [8, 28] and the weight ranges below:
//   table   weights 0..6   → ~8..12 px
//   concept weights 14..22 → ~20..28 px
const TABLE_WEIGHT_MAX = 6;
const CONCEPT_WEIGHT_BASE = 22;    // floor — concepts always visibly dominant
const CONCEPT_WEIGHT_SPAN = 12;    // 22 + 0..12 → 22..34

function sizeWeightForNode(node) {
  if (node.type === "concept") {
    return CONCEPT_WEIGHT_BASE + Math.min(node.usage_count || 0, CONCEPT_WEIGHT_SPAN);
  }
  return Math.min((node.concepts || []).length, TABLE_WEIGHT_MAX);
}


// Default sim + visual params. SimPanel below seeds its initial state
// from here; user tweaks override (persisted via localStorage on the
// panel side). The same defaults bake back into the bundle once the
// user copy/pastes their preferred values to me.
export const GRAPH_DEFAULTS = {
  // Layout mode — "radial" pins concepts on a fixed orbit and places
  // tables at the centroid of their bound concepts; force sim is
  // disabled. "force" runs Cosmograph's normal force-directed sim.
  layoutMode:               "radial",
  // Radial-layout knobs (ignored when layoutMode === "force")
  radialConceptRadius:      380,
  radialTablePull:          0.55,    // 0 = at center, 1 = at concept ring, >1 = outside
  radialTableJitter:        45,      // px — random offset around the centroid
  // Force-sim params (ignored when layoutMode === "radial")
  simulationGravity:        0.25,
  simulationRepulsion:      2.5,
  simulationLinkDistance:   18,
  simulationLinkSpring:     1.2,
  simulationFriction:       0.88,
  simulationDecay:          1000,
  // Node sizing
  pointSizeMin:             6,
  pointSizeMax:             44,
  // Edge styling
  bindOpacity:              0.06,
  // Label
  pointLabelFontSize:       12,
};


// Deterministic positive hash for stable jitter seeding from a table id.
function _hashStr(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}


// Build a node-id → {x, y} map for the radial layout. Concepts on a
// circle of `radius`, evenly distributed; tables at the centroid of
// their bound concepts × `pull` plus stable per-table jitter so
// siblings don't stack.
function computeRadialPositions(nodes, edges, { radius, pull, jitter }) {
  const conceptNodes = nodes.filter((n) => n.type === "concept");
  const positions = new Map();
  if (conceptNodes.length === 0) {
    // No concepts → tables would all sit on top of each other. Spread
    // them in a circle as a fallback so the canvas isn't a single dot.
    const tableNodes = nodes.filter((n) => n.type !== "concept");
    tableNodes.forEach((t, i) => {
      const angle = (2 * Math.PI * i) / Math.max(1, tableNodes.length) - Math.PI / 2;
      positions.set(t.id, { x: radius * Math.cos(angle), y: radius * Math.sin(angle) });
    });
    return positions;
  }

  // 1. Concepts on a circle. Start from the top (-π/2) so the first
  //    concept lands at 12 o'clock; cosmetic only, helps readability.
  conceptNodes.forEach((c, i) => {
    const angle = (2 * Math.PI * i) / conceptNodes.length - Math.PI / 2;
    positions.set(c.id, {
      x: radius * Math.cos(angle),
      y: radius * Math.sin(angle),
    });
  });

  // 2. Tables → centroid of their bound concept hubs × pull factor.
  //    Build a table → [conceptIds] map from the bind edges first.
  const conceptsByTable = new Map();
  for (const e of edges || []) {
    if (e.kind !== "binds") continue;
    if (!conceptsByTable.has(e.source)) conceptsByTable.set(e.source, []);
    conceptsByTable.get(e.source).push(e.target);
  }

  const tableNodes = nodes.filter((n) => n.type !== "concept");
  tableNodes.forEach((t) => {
    const boundIds = conceptsByTable.get(t.id) || [];
    const conceptPositions = boundIds
      .map((cid) => positions.get(cid))
      .filter(Boolean);

    let x = 0, y = 0;
    if (conceptPositions.length > 0) {
      x = conceptPositions.reduce((s, p) => s + p.x, 0) / conceptPositions.length;
      y = conceptPositions.reduce((s, p) => s + p.y, 0) / conceptPositions.length;
    }
    // Pull factor — 0..1 keeps tables between centre and the ring; >1
    // pushes them outside the concept ring. Default 0.55 sits them
    // safely in the middle annulus.
    x *= pull;
    y *= pull;

    // Stable jitter from a hash of the table id so re-renders don't
    // teleport nodes. Polar offset so siblings spread along a small
    // ring around the centroid rather than stacking.
    const seed = _hashStr(t.id);
    const jitterAngle = ((seed % 360) * Math.PI) / 180;
    const jitterRadius = (seed % 100) / 100 * jitter;
    x += jitterRadius * Math.cos(jitterAngle);
    y += jitterRadius * Math.sin(jitterAngle);

    positions.set(t.id, { x, y });
  });

  return positions;
}

export default function GraphCanvas({
  catalogGraphUrl,
  licenseKey,
  selectedId,
  basketTableIds,
  highlightIds,           // 9.c — pulse these nodes ~3s when ChatDrawer fires
  filterMaskIds,          // Set<string> of table ids passing the filter
  filterConceptIds,       // Set<string> of concept ids the user explicitly
                          // picked. Only THESE concept hubs survive the
                          // filter dim — transitive (other concepts a
                          // filtered table also binds) stay greyed.
  simParams: initialSimParams = GRAPH_DEFAULTS,
  onSelect,
  onAddToBasket,
  onBulkAddToBasket,
}) {
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Multi-select state (Set of node ids). Cosmograph's own selection API
  // is imperative; we keep a React copy so the basket affordance can read
  // it without poking through the ref.
  const [multiSelectIds, setMultiSelectIds] = useState(() => new Set());

  // Click-sticky focus — when the user clicks a node, that node + its
  // direct neighbours stay lit until they click empty space. Same
  // dim pathway as the hover effect, just persistent. The filter dim
  // composes on top: a clicked node that's outside the filter still
  // wins the focus while filter is active.
  const [clickedFocusId, setClickedFocusId] = useState(null);

  // Live sim/visual params — persisted to localStorage so a tweak
  // survives a hard refresh. SimPanel below reads + writes through
  // setSimParams; everything else in this component reads simParams
  // as plain state.
  const [simParams, setSimParams] = useState(() => {
    try {
      const stored = window.localStorage.getItem("kesif.simParams");
      if (stored) return { ...initialSimParams, ...JSON.parse(stored) };
    } catch { /* localStorage may be blocked — fall back to defaults */ }
    return initialSimParams;
  });
  useEffect(() => {
    try {
      window.localStorage.setItem("kesif.simParams", JSON.stringify(simParams));
    } catch { /* noop */ }
  }, [simParams]);

  // Right-click menu state.
  const [menu, setMenu] = useState(null); // { x, y, nodeId } | null

  const cosmoRef = useRef(null);

  // Fetch graph payload.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(catalogGraphUrl, { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`graph: HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (cancelled) return;
        setGraph(data);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String(err.message || err));
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [catalogGraphUrl]);

  // Stable index → id mapping. Cosmograph's callbacks return point INDEX
  // (the position in our `points` array); we need a quick way to map back.
  //
  // Filter dim is *not* baked into the points array anymore. We let
  // Cosmograph's selectPoints() do the work — it's a constant-time
  // selection update that doesn't touch the DuckDB buffer, vs. swapping
  // colors on the data which forced a multi-second re-upload + force
  // restart every time the filter changed. The "visible set"
  // computation now lives in a separate effect (search below for
  // "filter dim effect").
  const { points, links, idToIndex, deptIndex, neighborMap } = useMemo(() => {
    if (!graph) {
      return { points: [], links: [], idToIndex: new Map(), deptIndex: new Map(), neighborMap: new Map() };
    }
    const deptIndex = new Map();
    const orderedDepts = [];
    for (const n of graph.nodes) {
      if (n.type === "concept") continue;
      const d = n.department;
      if (d && !deptIndex.has(d)) {
        deptIndex.set(d, orderedDepts.length);
        orderedDepts.push(d);
      }
    }

    // Cosmograph 2.x requires BOTH a string id (pointIdBy) AND a sequential
    // numeric index (pointIndexBy) on every point. The runtime errors with
    // "Missing required properties: pointIndexBy" if the index column is
    // absent. Links similarly need numeric source/target indices for the
    // GPU adjacency lookups.
    // When the layout mode is "radial", precompute fixed positions
    // and attach them to each point. Cosmograph reads them via
    // pointXBy/pointYBy props (configured below). In "force" mode
    // we leave x/y as 0 and the sim takes over.
    const radialPositions =
      simParams.layoutMode === "radial"
        ? computeRadialPositions(graph.nodes, graph.edges, {
            radius: simParams.radialConceptRadius,
            pull:   simParams.radialTablePull,
            jitter: simParams.radialTableJitter,
          })
        : null;

    const points = graph.nodes.map((n, i) => {
      const pos = radialPositions?.get(n.id);
      return {
        index: i,
        id: n.id,
        type: n.type || "table",   // 9.b.1 — bipartite: "table" | "concept"
        label: n.label,
        department: n.department,
        source: n.source,
        usage_count: n.usage_count || 0,
        conceptCount: (n.concepts || []).length,
        color: colorForNode(n, deptIndex),
        sizeWeight: sizeWeightForNode(n),
        x: pos ? pos.x : 0,
        y: pos ? pos.y : 0,
      };
    });
    const idToIndex = new Map(points.map((p) => [p.id, p.index]));

    // Edge styling per kind. Three kinds now:
    //   - lookup (table→table): solid, darkest. Real join semantics.
    //   - binds  (table→concept): hub spoke. Thin + soft so popular hubs
    //     don't drown in a sea of lines; the eye reads them as orbital
    //     attachments, not "this table is just like that table".
    //   - manual (table→table): catalog-declared related_tables. Slightly
    //     stronger than binds, weaker than lookup.
    // Bind edges intentionally near-invisible (opacity 0.06) — at 30+
    // tables × ~5 concept hubs the spider-web of bind lines drowns the
    // node layout. Force clustering already pulls bound tables toward
    // their concept hubs, so spatial proximity carries the bind signal;
    // the faint line is a hint, not the primary cue. Lookup / manual
    // edges stay full-opacity because they're rare and structural.
    const EDGE_STYLE = {
      lookup:  { width: 1.6, opacity: 0.85,                       color: "#334155" },
      binds:   { width: 0.4, opacity: simParams.bindOpacity ?? 0.06, color: "#e2e8f0" },
      manual:  { width: 1.0, opacity: 0.55,                       color: "#64748b" },
    };
    const defaultStyle = { width: 0.8, opacity: 0.4, color: "#94a3b8" };

    // Drop edges that reference nodes the catalog didn't return (defensive
    // — shouldn't happen post-bipartite emit but cheap insurance).
    // Edge dim under filter is handled by Cosmograph's link greyout
    // (driven by selectPoints), not by per-edge color mutation.
    const links = (graph.edges || []).reduce((acc, e) => {
      const sIdx = idToIndex.get(e.source);
      const tIdx = idToIndex.get(e.target);
      if (sIdx === undefined || tIdx === undefined) return acc;
      const style = EDGE_STYLE[e.kind] || defaultStyle;
      acc.push({
        source: e.source,
        target: e.target,
        sourceIndex: sIdx,
        targetIndex: tIdx,
        kind: e.kind,
        label: e.label,
        strength: e.strength,
        width: style.width,
        opacity: style.opacity,
        color: style.color,
      });
      return acc;
    }, []);

    // Build undirected neighbor map for hover-dim. We keep id-keyed sets
    // because callbacks deliver INDEX and we resolve via idToIndex/points.
    const neighborMap = new Map();
    const add = (a, b) => {
      if (!neighborMap.has(a)) neighborMap.set(a, new Set());
      neighborMap.get(a).add(b);
    };
    for (const e of links) {
      add(e.source, e.target);
      add(e.target, e.source);
    }

    return { points, links, idToIndex, deptIndex, neighborMap };
  }, [
    graph,
    simParams.bindOpacity,
    simParams.layoutMode,
    simParams.radialConceptRadius,
    simParams.radialTablePull,
    simParams.radialTableJitter,
  ]);

  // Filter dim — derived set of point indices that should remain bright
  // when the user has narrowed the graph via the left-rail filters.
  // Empty/null mask = no filter active (everything bright).
  //
  // No transitive concepts: the only concept hubs that survive are the
  // ones the user explicitly picked (filterConceptIds). The previously
  // automatic "include any hub a filtered table binds" pulled in every
  // sibling concept — visually noisy when the user just wants to see
  // the as_of_time cluster.
  const filterFocusIndices = useMemo(() => {
    const hasTableFilter = filterMaskIds && filterMaskIds.size > 0;
    const hasConceptFilter = filterConceptIds && filterConceptIds.size > 0;
    if (!graph || (!hasTableFilter && !hasConceptFilter)) return null;
    const visible = new Set(hasTableFilter ? filterMaskIds : []);
    if (hasConceptFilter) {
      for (const c of filterConceptIds) visible.add(`concept:${c}`);
    }
    return [...visible]
      .map((id) => idToIndex.get(id))
      .filter((i) => i !== undefined);
  }, [graph, filterMaskIds, filterConceptIds, idToIndex]);

  // Click-focus dim — when the user clicked a node, dim everyone except
  // it + its direct neighbours. Same shape as filterFocusIndices so the
  // selection effect below can treat both uniformly.
  const clickFocusIndices = useMemo(() => {
    if (!clickedFocusId) return null;
    const idx = idToIndex.get(clickedFocusId);
    if (idx === undefined) return null;
    const out = [idx];
    const neighbours = neighborMap.get(clickedFocusId);
    if (neighbours) {
      for (const nid of neighbours) {
        const ni = idToIndex.get(nid);
        if (ni !== undefined) out.push(ni);
      }
    }
    return out;
  }, [clickedFocusId, idToIndex, neighborMap]);

  // Composed dim — click wins over filter when set (so clicking a node
  // outside the current filter still focuses it). When click is null,
  // filter alone drives the dim.
  const activeFocusIndices = clickFocusIndices ?? filterFocusIndices;

  // ── Selection bridging ───────────────────────────────────────────────
  // When the parent's selectedId changes (e.g., from the tree), reflect
  // it in Cosmograph's focus AND pin the click-sticky so neighbours
  // light up too. When the parent clears (right rail closes), drop the
  // sticky so the graph returns to full brightness (or to the filter
  // selection if one's active).
  useEffect(() => {
    if (selectedId == null) {
      setClickedFocusId(null);
      return;
    }
    setClickedFocusId(selectedId);
    if (!cosmoRef.current) return;
    const idx = idToIndex.get(selectedId);
    if (idx === undefined) return;
    try {
      cosmoRef.current.focusPoint?.(idx);
    } catch { /* method varies by version — soft-fail */ }
  }, [selectedId, idToIndex]);

  // ── Sticky dim (filter and/or click focus) ───────────────────────────
  // Cosmograph's selectPoints greys out non-selected via
  // pointGreyoutOpacity. We point it at whatever focus is active —
  // click wins over filter when both are set. No setData, no force
  // restart, no DuckDB upload — constant-time selection update.
  useEffect(() => {
    const ref = cosmoRef.current;
    if (!ref) return;
    try {
      if (activeFocusIndices && activeFocusIndices.length > 0) {
        ref.selectPoints?.(activeFocusIndices);
      } else {
        ref.unselectPoints?.();
      }
    } catch { /* method varies by version — soft-fail */ }
  }, [activeFocusIndices]);

  // ── Highlight pulse from ChatDrawer (9.c) ────────────────────────────
  // When the LLM returns proposals, the parent passes their ids here.
  // We selectPoints (Cosmograph's selection greys out everyone else) for
  // ~3s, then unselect. Reuses the same dimming pathway the hover uses,
  // so the "lock onto these tables" cue is consistent across surfaces.
  useEffect(() => {
    if (!cosmoRef.current || !highlightIds || highlightIds.length === 0) return;
    const indices = highlightIds
      .map((id) => idToIndex.get(id))
      .filter((i) => i !== undefined);
    if (indices.length === 0) return;
    try {
      cosmoRef.current.selectPoints?.(indices);
    } catch { /* soft-fail */ }
    const t = setTimeout(() => {
      try { cosmoRef.current?.unselectPoints?.(); } catch { /* noop */ }
    }, 3000);
    return () => clearTimeout(t);
  }, [highlightIds, idToIndex]);

  // ── Event handlers ──────────────────────────────────────────────────

  const idForIndex = useCallback((index) => {
    if (index == null || index < 0 || index >= points.length) return null;
    return points[index].id;
  }, [points]);

  const handleClick = useCallback((index, _position, event) => {
    // Background click clears every sticky state.
    if (index == null) {
      setMultiSelectIds(new Set());
      setClickedFocusId(null);
      onSelect?.(null);
      setMenu(null);
      return;
    }
    const point = points[index];
    if (!point) return;

    // Concept hub click: route through onSelect (detail card opens),
    // and also pin the click-sticky focus so the orbital tables stand
    // out while the user reads the docs.
    if (point.type === "concept") {
      setMultiSelectIds(new Set());
      setClickedFocusId(point.id);
      onSelect?.(point.id);
      setMenu(null);
      return;
    }

    if (event?.shiftKey) {
      // Multi-select: toggle id membership; leave single-selection alone.
      setMultiSelectIds((prev) => {
        const next = new Set(prev);
        if (next.has(point.id)) next.delete(point.id); else next.add(point.id);
        return next;
      });
    } else {
      // Single selection clears multi, opens detail, and pins the
      // click-sticky focus on this node + its neighbours.
      setMultiSelectIds(new Set());
      setClickedFocusId(point.id);
      onSelect?.(point.id);
    }
    setMenu(null);
  }, [points, neighborMap, onSelect]);

  const handleContextMenu = useCallback((index, position, event) => {
    event?.preventDefault?.();
    const point = points[index];
    if (!point) {
      setMenu(null);
      return;
    }
    setMenu({
      x: event.clientX,
      y: event.clientY,
      nodeId: point.id,
      nodeType: point.type,
    });
  }, [points]);

  const handlePointMouseOver = useCallback((index) => {
    if (!cosmoRef.current) return;
    const id = idForIndex(index);
    if (!id) return;
    const neighbors = neighborMap.get(id);
    const focusIndices = [index];
    if (neighbors) {
      for (const nid of neighbors) {
        const ni = idToIndex.get(nid);
        if (ni !== undefined) focusIndices.push(ni);
      }
    }
    // Cosmograph dims un-selected points to `pointGreyoutOpacity`.
    try {
      cosmoRef.current.selectPoints?.(focusIndices);
    } catch { /* soft-fail across versions */ }
  }, [idForIndex, neighborMap, idToIndex]);

  const handlePointMouseOut = useCallback(() => {
    if (!cosmoRef.current) return;
    try {
      // Restore whichever sticky focus is active (click wins over
      // filter); otherwise clear and let every node return to full
      // brightness.
      if (activeFocusIndices && activeFocusIndices.length > 0) {
        cosmoRef.current.selectPoints?.(activeFocusIndices);
      } else {
        cosmoRef.current.unselectPoints?.();
      }
    } catch { /* soft-fail */ }
  }, [activeFocusIndices]);

  // Esc closes the context menu.
  useEffect(() => {
    if (!menu) return;
    const onKey = (e) => { if (e.key === "Escape") setMenu(null); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menu]);

  // Click-outside the menu also closes it.
  useEffect(() => {
    if (!menu) return;
    const onDown = (e) => {
      if (!e.target.closest?.(".kesif-graph__menu")) setMenu(null);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menu]);

  // ── Render ──────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="kesif-graph__loading">
        Graph yükleniyor… ({points.length} node)
      </div>
    );
  }
  if (error) {
    return (
      <div className="kesif-graph__error">
        Graph yüklenemedi: {error}
      </div>
    );
  }
  if (points.length === 0) {
    return (
      <div className="kesif-graph__empty">
        Henüz tablo yok. Sol taraftan tablo eklendikçe burada görünecek.
      </div>
    );
  }

  // Highlight selected single + multi-selected on top of cosmograph's
  // own selection. We pass `focusedPointIndex` for the single, and
  // imperatively call selectPoints for multi via the effect below.
  const focusedIndex = selectedId != null ? idToIndex.get(selectedId) : undefined;

  return (
    <div className="kesif-graph">
      <CosmographProvider>
        <Cosmograph
          ref={cosmoRef}
          points={points}
          links={links}
          pointIdBy="id"
          pointIndexBy="index"
          pointLabelBy="label"
          pointColorBy="color"
          pointSizeBy="sizeWeight"
          // pointSizeRange now driven by simParams so the live panel can
          // tune it without a rebuild. Defaults [6, 44]: tables collapse
          // to 6-12 px, concepts spread from ~28 → 44 px so the hub vs
          // satellite distinction is unmissable.
          pointSizeRange={[simParams.pointSizeMin, simParams.pointSizeMax]}
          pointLabelFontSize={simParams.pointLabelFontSize}
          // Radial layout: feed pre-computed positions + disable the
          // force sim. Cosmograph respects (x, y) verbatim per point.
          // In force mode the props are still passed (Cosmograph
          // ignores them when enableSimulation is true).
          {...(simParams.layoutMode === "radial"
            ? { pointXBy: "x", pointYBy: "y", enableSimulation: false }
            : { enableSimulation: true })}
          // Soft white labels stay legible against any node color — the
          // default near-black got eaten by the dark colored chips and
          // disappeared on the colored nodes. #f1f5f9 = slate-100, which
          // has just enough warmth to not look harsh on the f6f7f9
          // background.
          pointLabelColor="#f1f5f9"
          linkSourceBy="source"
          linkSourceIndexBy="sourceIndex"
          linkTargetBy="target"
          linkTargetIndexBy="targetIndex"
          linkColorBy="color"
          linkWidthBy="width"
          linkOpacityBy="opacity"
          // Force-sim params live-driven by the floating SimPanel — the
          // user tunes them in real time and copies the JSON back here
          // once happy. Defaults in GRAPH_DEFAULTS above.
          simulationGravity={simParams.simulationGravity}
          simulationRepulsion={simParams.simulationRepulsion}
          simulationLinkDistance={simParams.simulationLinkDistance}
          simulationLinkSpring={simParams.simulationLinkSpring}
          simulationFriction={simParams.simulationFriction}
          // simulationDecay default 5000 — sim runs for a long time and
          // keeps applying micro-velocities that look like jitter to the
          // user. 1000 = settles in ~2-3s on small graphs.
          simulationDecay={simParams.simulationDecay}
          // Filter changes re-emit the points array with new colors;
          // without this flag Cosmograph would re-run the force sim and
          // jitter every node. Holding positions keeps the dim
          // transition feel like a fade, not a teleport.
          preservePointPositionsOnDataUpdate
          // Fit the graph to the viewport once the simulation settles.
          // 0.1 padding leaves only a thin margin so we don't start zoomed
          // out from a great distance.
          fitViewOnInit
          fitViewDelay={1200}
          fitViewPadding={0.1}
          fitViewDuration={400}
          // Hover dim — Cosmograph greys out non-selected on selectPoints.
          pointGreyoutOpacity={0.15}
          linkGreyoutOpacity={0.05}
          backgroundColor="#f6f7f9"
          // Show labels for the strongest-bound nodes; the rest hide on
          // collision (Cosmograph picks by pointLabelWeightBy under the hood).
          pointLabelWeightBy="sizeWeight"
          renderLinks
          curvedLinks={false}
          focusedPointIndex={focusedIndex}
          licenseKey={licenseKey || undefined}
          onClick={handleClick}
          onPointMouseOver={handlePointMouseOver}
          onPointMouseOut={handlePointMouseOut}
          onPointContextMenu={handleContextMenu}
          onSimulationEnd={() => {
            const ref = cosmoRef.current;
            if (!ref) return;
            // (1) Refit — fitViewOnInit fires before convergence on small
            // catalogs, so we redo it once nodes have settled.
            try { ref.fitView?.(400); } catch { /* noop */ }
            // (2) Hard-pause the sim. Without this, Cosmograph keeps
            // ticking after "end" — invisible velocities re-render and
            // jiggle nodes pixel-by-pixel, which reads as "tık tık" jitter
            // to the user. pause() locks them in place.
            try { ref.pause?.(); } catch { /* noop */ }
          }}
          style={{ width: "100%", height: "100%" }}
        />
      </CosmographProvider>

      <Legend deptIndex={deptIndex} />

      <SimPanel
        params={simParams}
        onChange={setSimParams}
        onReset={() => setSimParams(initialSimParams)}
      />

      <MultiSelectBar
        ids={multiSelectIds}
        basketTableIds={basketTableIds}
        onClear={() => setMultiSelectIds(new Set())}
        onAdd={() => {
          // Bulk add: only add the ids not already in the basket.
          const toAdd = [];
          for (const id of multiSelectIds) {
            if (!basketTableIds.has(id)) toAdd.push(id);
          }
          if (toAdd.length) onBulkAddToBasket?.(toAdd);
          setMultiSelectIds(new Set());
        }}
      />

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          nodeId={menu.nodeId}
          nodeType={menu.nodeType}
          inBasket={basketTableIds.has(menu.nodeId)}
          onClose={() => setMenu(null)}
          onAdd={() => {
            onAddToBasket?.(menu.nodeId);
            setMenu(null);
          }}
          onOpenDetail={() => {
            onSelect?.(menu.nodeId);
            setMenu(null);
          }}
          onAddOrbitalTables={() => {
            const orbital = neighborMap.get(menu.nodeId);
            if (orbital) {
              const toAdd = [...orbital].filter((id) => !basketTableIds.has(id));
              if (toAdd.length) onBulkAddToBasket?.(toAdd);
            }
            setMenu(null);
          }}
        />
      )}
    </div>
  );
}


// ── Sub-components ────────────────────────────────────────────────────


function Legend({ deptIndex }) {
  const entries = deptIndex ? [...deptIndex.entries()].sort((a, b) => a[1] - b[1]) : [];
  return (
    <div className="kesif-graph__legend">
      <div className="kesif-graph__legend-item">
        <span className="kesif-graph__legend-swatch" style={{ background: CONCEPT_COLOR }} />
        Kavram
      </div>
      {entries.map(([dept, idx]) => (
        <div key={dept} className="kesif-graph__legend-item">
          <span
            className="kesif-graph__legend-swatch"
            style={{ background: DEPT_PALETTE[idx % DEPT_PALETTE.length] }}
          />
          {dept.charAt(0).toUpperCase() + dept.slice(1)}
        </div>
      ))}
      <div className="kesif-graph__legend-item">
        <span className="kesif-graph__legend-swatch" style={{ background: UPLOAD_COLOR }} />
        Yüklemelerim
      </div>
    </div>
  );
}


function MultiSelectBar({ ids, basketTableIds, onClear, onAdd }) {
  if (!ids || ids.size === 0) return null;
  const newCount = [...ids].filter((id) => !basketTableIds.has(id)).length;
  return (
    <div className="kesif-graph__multi">
      <MousePointer2 size={14} />
      <span>{ids.size} tablo seçili</span>
      <button
        type="button"
        className="kesif-btn kesif-btn--primary"
        onClick={onAdd}
        disabled={newCount === 0}
        title={newCount === 0 ? "Hepsi zaten sepette" : `${newCount} yeni tabloyu ekle`}
      >
        <Plus size={12} />
        Sepete ekle ({newCount})
      </button>
      <button type="button" className="kesif-btn" onClick={onClear} title="Seçimi temizle">
        <X size={12} />
      </button>
    </div>
  );
}


function ContextMenu({
  x, y, nodeId, nodeType, inBasket,
  onAdd, onOpenDetail, onAddOrbitalTables,
}) {
  const style = {
    left: Math.min(x, window.innerWidth - 220),
    top: Math.min(y, window.innerHeight - 140),
  };

  if (nodeType === "concept") {
    // Concept hub menu: no detail card, no direct sepete-ekle. The
    // primary verb is "add every table bound to this concept".
    const conceptLabel = nodeId.replace(/^concept:/, "");
    return (
      <div className="kesif-graph__menu" style={style}>
        <div className="kesif-graph__menu-header" title={nodeId}>
          <strong>{conceptLabel}</strong>
          <span>Kavram</span>
        </div>
        <button
          type="button"
          className="kesif-graph__menu-item"
          onClick={onAddOrbitalTables}
        >
          Tüm bağlı tabloları sepete ekle
        </button>
        <button type="button" className="kesif-graph__menu-item" disabled title="Yakında — 9.c">
          Kavram detayı
        </button>
      </div>
    );
  }

  const [schema, name] = nodeId.split(/\.(.+)/);
  return (
    <div className="kesif-graph__menu" style={style}>
      <div className="kesif-graph__menu-header" title={nodeId}>
        <strong>{name}</strong>
        <span>{schema}</span>
      </div>
      <button type="button" className="kesif-graph__menu-item" onClick={onOpenDetail}>
        Detayı aç
      </button>
      <button
        type="button"
        className="kesif-graph__menu-item"
        onClick={onAdd}
        disabled={inBasket}
      >
        {inBasket ? "Zaten sepette" : "Sepete ekle"}
      </button>
      <button type="button" className="kesif-graph__menu-item" disabled title="Yakında — 9.b.2">
        Cluster'a odaklan
      </button>
    </div>
  );
}


// ── SimPanel — live tuning overlay (top-left of the graph) ───────────
//
// Mutable controls for the force-sim + visual params. Used to let a
// human eyeball the best values; the chosen JSON gets copy/pasted back
// into GRAPH_DEFAULTS once happy.
//
// Persistence: parent (GraphCanvas) saves the current params to
// localStorage on every change, so a hard refresh keeps the tweak.
// Reset restores GRAPH_DEFAULTS.

const SIM_FIELDS_FORCE = [
  { key: "simulationGravity",      label: "Gravity",        min: 0,    max: 1,    step: 0.05 },
  { key: "simulationRepulsion",    label: "Repulsion",      min: 0,    max: 6,    step: 0.1  },
  { key: "simulationLinkDistance", label: "Link distance",  min: 1,    max: 50,   step: 1    },
  { key: "simulationLinkSpring",   label: "Link spring",    min: 0,    max: 3,    step: 0.1  },
  { key: "simulationFriction",     label: "Friction",       min: 0.5,  max: 1,    step: 0.01 },
  { key: "simulationDecay",        label: "Decay",          min: 100,  max: 10000, step: 100 },
];

const SIM_FIELDS_RADIAL = [
  { key: "radialConceptRadius",    label: "Çember yarıçap", min: 100,  max: 800,  step: 10   },
  { key: "radialTablePull",        label: "Tablo çekim",    min: 0,    max: 1.5,  step: 0.05 },
  { key: "radialTableJitter",      label: "Tablo dağılım",  min: 0,    max: 200,  step: 5    },
];

const SIM_FIELDS_VISUAL = [
  { key: "pointSizeMin",           label: "Node size min",  min: 1,    max: 30,   step: 1    },
  { key: "pointSizeMax",           label: "Node size max",  min: 10,   max: 80,   step: 1    },
  { key: "pointLabelFontSize",     label: "Label font",     min: 8,    max: 18,   step: 1    },
  { key: "bindOpacity",            label: "Bind opacity",   min: 0,    max: 1,    step: 0.02 },
];

function SimPanel({ params, onChange, onReset }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const setField = useCallback((key, value) => {
    onChange({ ...params, [key]: value });
  }, [params, onChange]);

  const copyJson = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(params, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch { /* clipboard may be denied — silent */ }
  }, [params]);

  if (!open) {
    return (
      <button
        type="button"
        className="kesif-sim__toggle"
        onClick={() => setOpen(true)}
        title="Sim ayarları"
      >
        ⚙ Sim
      </button>
    );
  }

  return (
    <div className="kesif-sim" role="dialog" aria-label="Sim ayarları">
      <header className="kesif-sim__header">
        <span className="kesif-sim__title">Sim ayarları</span>
        <button
          type="button"
          className="kesif-sim__close"
          onClick={() => setOpen(false)}
          title="Kapat"
        >
          ×
        </button>
      </header>
      <div className="kesif-sim__body">
        <div className="kesif-sim__layout-toggle">
          <button
            type="button"
            className={`kesif-sim__layout-btn${params.layoutMode === "radial" ? " is-active" : ""}`}
            onClick={() => setField("layoutMode", "radial")}
          >
            Radyal
          </button>
          <button
            type="button"
            className={`kesif-sim__layout-btn${params.layoutMode === "force" ? " is-active" : ""}`}
            onClick={() => setField("layoutMode", "force")}
          >
            Force
          </button>
        </div>

        <div className="kesif-sim__group-title">
          {params.layoutMode === "radial" ? "Radyal düzen" : "Force sim"}
        </div>
        {(params.layoutMode === "radial" ? SIM_FIELDS_RADIAL : SIM_FIELDS_FORCE).map((f) => (
          <SimField
            key={f.key}
            label={f.label}
            value={params[f.key]}
            min={f.min}
            max={f.max}
            step={f.step}
            onChange={(v) => setField(f.key, v)}
          />
        ))}

        <div className="kesif-sim__group-title">Görsel</div>
        {SIM_FIELDS_VISUAL.map((f) => (
          <SimField
            key={f.key}
            label={f.label}
            value={params[f.key]}
            min={f.min}
            max={f.max}
            step={f.step}
            onChange={(v) => setField(f.key, v)}
          />
        ))}
      </div>
      <footer className="kesif-sim__footer">
        <button type="button" className="kesif-btn" onClick={onReset} title="Varsayılana dön">
          Reset
        </button>
        <button type="button" className="kesif-btn kesif-btn--primary" onClick={copyJson} title="JSON'u panoya kopyala">
          {copied ? "Kopyalandı ✓" : "Kopyala (JSON)"}
        </button>
      </footer>
    </div>
  );
}


function SimField({ label, value, min, max, step, onChange }) {
  const num = Number(value);
  return (
    <label className="kesif-sim__field">
      <span className="kesif-sim__field-label">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={num}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="kesif-sim__field-slider"
      />
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={num}
        onChange={(e) => {
          const v = parseFloat(e.target.value);
          if (!Number.isNaN(v)) onChange(v);
        }}
        className="kesif-sim__field-number"
      />
    </label>
  );
}
