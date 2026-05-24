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
  "#ca8a04", // amber
  "#dc2626", // red
  "#9333ea", // purple
  "#db2777", // pink
  "#475569", // slate (fallback)
];
const UPLOAD_COLOR = "#f59e0b";   // amber — user-uploaded tables stand out
const DEFAULT_COLOR = "#94a3b8";  // neutral grey

function colorForNode(node, deptIndex) {
  if (node.source === "user_upload") return UPLOAD_COLOR;
  if (!node.department) return DEFAULT_COLOR;
  const idx = deptIndex.get(node.department) ?? 0;
  return DEPT_PALETTE[idx % DEPT_PALETTE.length];
}

// Bucket node sizes by concept count — well-bound tables look heavier, so
// the eye is drawn to them as starting points. Range chosen so Cosmograph
// pointSizeRange:[3,12] doesn't bloom out at 60fps.
function sizeForNode(node) {
  const n = (node.concepts || []).length;
  return 3 + Math.min(n, 6) * 1.5;
}


export default function GraphCanvas({
  catalogGraphUrl,
  licenseKey,
  selectedId,
  basketTableIds,
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
      const d = n.department;
      if (d && !deptIndex.has(d)) {
        deptIndex.set(d, orderedDepts.length);
        orderedDepts.push(d);
      }
    }

    const points = graph.nodes.map((n) => ({
      id: n.id,
      label: n.label,
      department: n.department,
      source: n.source,
      conceptCount: (n.concepts || []).length,
      color: colorForNode(n, deptIndex),
      size: sizeForNode(n),
    }));
    const idToIndex = new Map(points.map((p, i) => [p.id, i]));

    // Cosmograph 2.x expects links with whatever source/target column names
    // we pass; we keep ours as `source`/`target` for clarity. Kind drives
    // styling (lookup = full opacity, shared_concept = dimmer + thinner).
    const links = (graph.edges || []).map((e) => ({
      source: e.source,
      target: e.target,
      kind: e.kind,
      label: e.label,
      strength: e.strength,
      width: e.kind === "lookup" ? 1.4 : 0.8,
      opacity: e.kind === "lookup" ? 0.85 : 0.35,
      color: e.kind === "lookup" ? "#475569" : "#94a3b8",
    }));

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
    const id = idForIndex(index);
    if (!id) return;

    if (event?.shiftKey) {
      // Multi-select: toggle id membership; leave single-selection alone.
      setMultiSelectIds((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
      });
    } else {
      // Single selection clears multi.
      setMultiSelectIds(new Set());
      onSelect?.(id);
    }
    setMenu(null);
  }, [idForIndex, onSelect]);

  const handleContextMenu = useCallback((index, position, event) => {
    event?.preventDefault?.();
    const id = idForIndex(index);
    if (!id) {
      setMenu(null);
      return;
    }
    setMenu({
      x: event.clientX,
      y: event.clientY,
      nodeId: id,
    });
  }, [idForIndex]);

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
          pointLabelBy="label"
          pointColorBy="color"
          pointSizeBy="size"
          linkSourceBy="source"
          linkTargetBy="target"
          linkColorBy="color"
          linkWidthBy="width"
          linkOpacityBy="opacity"
          // Force-sim params: tuned for ~200-1k node range. With 10k tables
          // we'll probably raise simulationFriction + drop simulationRepulsion
          // to converge faster — leave that tuning to live testing.
          simulationGravity={0.3}
          simulationRepulsion={0.6}
          simulationLinkDistance={6}
          simulationLinkSpring={1.2}
          simulationFriction={0.85}
          // Hover dim — Cosmograph greys out non-selected on selectPoints.
          pointGreyoutOpacity={0.15}
          linkGreyoutOpacity={0.05}
          backgroundColor="#f6f7f9"
          // Show labels for the strongest-bound nodes; the rest hide on
          // collision (Cosmograph picks by pointLabelWeightBy under the hood).
          pointLabelWeightBy="size"
          renderLinks
          curvedLinks={false}
          focusedPointIndex={focusedIndex}
          licenseKey={licenseKey || undefined}
          onClick={handleClick}
          onPointMouseOver={handlePointMouseOver}
          onPointMouseOut={handlePointMouseOut}
          onPointContextMenu={handleContextMenu}
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
        />
      )}
    </div>
  );
}


// ── Sub-components ────────────────────────────────────────────────────


function Legend({ deptIndex }) {
  if (!deptIndex || deptIndex.size === 0) return null;
  const entries = [...deptIndex.entries()].sort((a, b) => a[1] - b[1]);
  return (
    <div className="kesif-graph__legend">
      <div className="kesif-graph__legend-title">Renkler</div>
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
      <div className="kesif-graph__legend-divider" />
      <div className="kesif-graph__legend-item kesif-graph__legend-edge">
        <span className="kesif-graph__legend-line" style={{ background: "#475569", height: 2 }} />
        Lookup
      </div>
      <div className="kesif-graph__legend-item kesif-graph__legend-edge">
        <span className="kesif-graph__legend-line" style={{ background: "#94a3b8", opacity: 0.5 }} />
        Ortak kavram
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


function ContextMenu({ x, y, nodeId, inBasket, onClose, onAdd, onOpenDetail }) {
  // Position the menu so it doesn't run off the viewport edge.
  const style = {
    left: Math.min(x, window.innerWidth - 220),
    top: Math.min(y, window.innerHeight - 140),
  };
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
