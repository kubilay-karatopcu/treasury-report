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
// Concept hubs in saturated yellow — distinct *kind* of thing, not just
// another department. (Was amber; user wanted a brighter yellow so hubs
// pop against the table palette.)
const CONCEPT_COLOR = "#facc15";   // yellow-400
// User uploads in pink — won't collide with concept yellow or any dept.
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
//     larger than the *biggest* table. This makes the hub-and-spoke
//     hierarchy immediately legible — concepts are always visually
//     dominant.
//
// With pointSizeRange [8, 22] and the weight ranges below:
//   table  weights 0..6  → ~8..12 px
//   concept weights 8..16 → ~15..22 px
const TABLE_WEIGHT_MAX = 6;
const CONCEPT_WEIGHT_BASE = 8;     // floor — keeps small hubs bigger than tables
const CONCEPT_WEIGHT_SPAN = 8;     // 8 + 0..8 → 8..16

function sizeWeightForNode(node) {
  if (node.type === "concept") {
    return CONCEPT_WEIGHT_BASE + Math.min(node.usage_count || 0, CONCEPT_WEIGHT_SPAN);
  }
  return Math.min((node.concepts || []).length, TABLE_WEIGHT_MAX);
}


export default function GraphCanvas({
  catalogGraphUrl,
  licenseKey,
  selectedId,
  basketTableIds,
  highlightIds,        // 9.c — pulse these nodes ~3s when ChatDrawer fires
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
    const points = graph.nodes.map((n, i) => ({
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
    }));
    const idToIndex = new Map(points.map((p) => [p.id, p.index]));

    // Edge styling per kind. Three kinds now:
    //   - lookup (table→table): solid, darkest. Real join semantics.
    //   - binds  (table→concept): hub spoke. Thin + soft so popular hubs
    //     don't drown in a sea of lines; the eye reads them as orbital
    //     attachments, not "this table is just like that table".
    //   - manual (table→table): catalog-declared related_tables. Slightly
    //     stronger than binds, weaker than lookup.
    const EDGE_STYLE = {
      lookup:  { width: 1.6, opacity: 0.85, color: "#334155" },
      binds:   { width: 0.6, opacity: 0.22, color: "#cbd5e1" },
      manual:  { width: 1.0, opacity: 0.55, color: "#64748b" },
    };
    const defaultStyle = { width: 0.8, opacity: 0.4, color: "#94a3b8" };

    // Drop edges that reference nodes the catalog didn't return (defensive
    // — shouldn't happen post-bipartite emit but cheap insurance).
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
  }, [graph]);

  // ── Selection bridging ───────────────────────────────────────────────
  // When the parent's selectedId changes (e.g., from the tree), reflect
  // it in Cosmograph's focus so the graph and the tree stay in sync.
  useEffect(() => {
    if (!cosmoRef.current || selectedId == null) return;
    const idx = idToIndex.get(selectedId);
    if (idx === undefined) return;
    try {
      cosmoRef.current.focusPoint?.(idx);
    } catch { /* method varies by version — soft-fail */ }
  }, [selectedId, idToIndex]);

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
    // Background click clears selection state.
    if (index == null) {
      setMultiSelectIds(new Set());
      setMenu(null);
      return;
    }
    const point = points[index];
    if (!point) return;

    // Concept hub click: select all orbital tables in one go. The detail
    // card stays untouched (concepts aren't in /catalog/<schema>/<table>).
    if (point.type === "concept") {
      const orbital = neighborMap.get(point.id);
      if (orbital && orbital.size) {
        setMultiSelectIds(new Set(orbital));
      }
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
      // Single selection clears multi.
      setMultiSelectIds(new Set());
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
      cosmoRef.current.unselectPoints?.();
    } catch { /* soft-fail */ }
  }, []);

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
          // Cosmograph linearly remaps the weight range into this px range.
          // Combined with sizeWeightForNode's asymmetric weights (tables
          // 0..6, concepts 8..16), the floor on concepts is ~15 px and
          // tables max ~12 px — so a concept hub is always visibly
          // dominant over its orbital tables.
          pointSizeRange={[8, 22]}
          pointLabelFontSize={10}
          linkSourceBy="source"
          linkSourceIndexBy="sourceIndex"
          linkTargetBy="target"
          linkTargetIndexBy="targetIndex"
          linkColorBy="color"
          linkWidthBy="width"
          linkOpacityBy="opacity"
          // Force-sim params: bipartite topology needs slightly tighter
          // gravity so orbital tables stay close to their concept hub.
          simulationGravity={0.4}
          simulationRepulsion={0.5}
          simulationLinkDistance={6}
          simulationLinkSpring={1.4}
          simulationFriction={0.88}
          // simulationDecay default 5000 — sim runs for a long time and
          // keeps applying micro-velocities that look like jitter to the
          // user. 1000 = settles in ~2-3s on small graphs.
          simulationDecay={1000}
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
