/* Phase 9.e — Atölye / Bloklar (Hybrid C).
 *
 * Layout mirrors Keşif:
 *   left rail  — search + filters (team / viz_type / tag) + tree of saved
 *                blocks grouped by team + chat panel at the bottom
 *   center     — responsive card grid of block summaries; when a block
 *                is selected, a bottom panel slides up *inside* the
 *                center column (does NOT extend under the left rail)
 *   right rail — collapsed (the bottom panel replaces it)
 *
 * Backend: reuses Phase 6.5.a's existing /presentations/library JSON.
 * No new server-side schema introduced here — just a new way to browse
 * what's already there. The detail iframe points to
 * /presentations/library/<bid>/preview which renders the block in a
 * read-only Sunum shell.
 *
 * Chat is wired but the real "propose_blocks" LLM contract lands in
 * Phase 10 with the marketplace MVP. For 9.e the panel surfaces a
 * "Yakında" hint instead of a live send button — uses the same
 * Sunum-style .chat-box markup so the visual reads identically to
 * Keşif's chat.
 */
import { createRoot } from "react-dom/client";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown, ChevronRight, Loader2,
  PlusCircle, Eye, Tag, Users,
} from "lucide-react";
import ChatDrawer from "../kesif/ChatDrawer.jsx";


const DATA = JSON.parse(document.getElementById("bloklar-data").textContent);
const ENDPOINTS = DATA.endpoints || {};
const USER = DATA.user || {};

const detailUrl = (bid) => ENDPOINTS.library_detail_template.replace("{bid}", bid);
const previewUrl = (bid) => ENDPOINTS.library_preview_template.replace("{bid}", bid);

// Viz-type label map — server returns the underlying enum, we surface
// human-friendly Turkish labels on the cards + filter chips.
const VIZ_LABELS = {
  kpi: "KPI",
  kpi_grid: "KPI Grid",
  bar_chart: "Çubuk grafik",
  line_chart: "Çizgi grafik",
  area_chart: "Alan grafiği",
  pie_chart: "Pasta",
  radial_bar: "Radyal gösterge",
  heatmap: "Isı haritası",
  data_table: "Tablo",
  narrative: "Anlatım",
};
const vizLabel = (k) => VIZ_LABELS[k] || k || "—";


function App() {
  const [blocks, setBlocks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Filters: search (debounced), team set, viz_type set, tag set.
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [selectedTeams, setSelectedTeams] = useState(new Set());
  const [selectedVizTypes, setSelectedVizTypes] = useState(new Set());
  const [selectedTags, setSelectedTags] = useState(new Set());
  // Team groups start fully collapsed (mirror Keşif's Şemalar rework).
  // expandedTeams holds the open ones; absent = closed.
  const [expandedTeams, setExpandedTeams] = useState(new Set());

  const [selectedBid, setSelectedBid] = useState(null);

  // 200ms debounce — matches Keşif.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim().toLowerCase()), 200);
    return () => clearTimeout(t);
  }, [search]);

  // Fetch the library list once on mount. We don't paginate — even at
  // 500+ blocks the list is small enough to ship as one JSON.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(ENDPOINTS.library_list, { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`library HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (cancelled) return;
        // /presentations/library returns an array directly.
        const items = Array.isArray(data) ? data : (data.items || data.blocks || []);
        setBlocks(items);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String(err.message || err));
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  // ── Facets ───────────────────────────────────────────────────────────
  const facets = useMemo(() => {
    const teams = new Map();
    const viz = new Map();
    const tags = new Map();
    for (const b of blocks) {
      const team = b.owner_department || b.team || "—";
      teams.set(team, (teams.get(team) || 0) + 1);
      const v = b.block_type || b.viz_type;
      if (v) viz.set(v, (viz.get(v) || 0) + 1);
      for (const tg of b.tags || []) tags.set(tg, (tags.get(tg) || 0) + 1);
    }
    const sortMap = (m) => Object.fromEntries(
      [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
    );
    return { teams: sortMap(teams), viz: sortMap(viz), tags: sortMap(tags) };
  }, [blocks]);

  // ── Filtered set ─────────────────────────────────────────────────────
  const filteredBlocks = useMemo(() => {
    return blocks.filter((b) => {
      if (selectedTeams.size) {
        const team = b.owner_department || b.team || "—";
        if (!selectedTeams.has(team)) return false;
      }
      if (selectedVizTypes.size) {
        const v = b.block_type || b.viz_type;
        if (!selectedVizTypes.has(v)) return false;
      }
      if (selectedTags.size) {
        const hasAny = (b.tags || []).some((t) => selectedTags.has(t));
        if (!hasAny) return false;
      }
      if (debouncedSearch) {
        const hay = [
          b.name, b.description, (b.tags || []).join(" "),
          b.owner_id, b.owner_department,
        ].filter(Boolean).join(" ").toLowerCase();
        if (!hay.includes(debouncedSearch)) return false;
      }
      return true;
    });
  }, [blocks, selectedTeams, selectedVizTypes, selectedTags, debouncedSearch]);

  // Group by team for the tree.
  const treeGroups = useMemo(() => {
    const byTeam = new Map();
    for (const b of filteredBlocks) {
      const k = b.owner_department || b.team || "—";
      if (!byTeam.has(k)) byTeam.set(k, []);
      byTeam.get(k).push(b);
    }
    return { byTeam };
  }, [filteredBlocks]);

  const selectedBlock = useMemo(
    () => blocks.find((b) => (b.library_id || b.id) === selectedBid) || null,
    [blocks, selectedBid],
  );

  // ── Filter toggle helpers ────────────────────────────────────────────
  const toggleSet = (setter) => (v) => setter((prev) => {
    const next = new Set(prev);
    if (next.has(v)) next.delete(v); else next.add(v);
    return next;
  });
  const toggleTeam = toggleSet(setSelectedTeams);
  const toggleViz = toggleSet(setSelectedVizTypes);
  const toggleTag = toggleSet(setSelectedTags);
  const toggleTeamExpanded = (team) => setExpandedTeams((prev) => {
    const next = new Set(prev);
    if (next.has(team)) next.delete(team); else next.add(team);
    return next;
  });
  const resetFilters = useCallback(() => {
    setSelectedTeams(new Set());
    setSelectedVizTypes(new Set());
    setSelectedTags(new Set());
    setSearch("");
  }, []);
  const filtersActive =
    selectedTeams.size > 0 ||
    selectedVizTypes.size > 0 ||
    selectedTags.size > 0 ||
    !!debouncedSearch;

  // Selected → open the bottom panel. Closing the panel clears the
  // selection so the next click reopens.
  const isBottomOpen = !!selectedBlock;
  const closeBottom = useCallback(() => setSelectedBid(null), []);

  return (
    <>
      <Topbar userName={USER.name} userDept={USER.department} />
      <div className={`bloklar-shell${isBottomOpen ? " has-bottom" : ""}`}>
        <div className="kesif-body kesif-body--right-collapsed">
          <LeftRail
            facets={facets}
            loading={loading}
            error={error}
            search={search}
            onSearch={setSearch}
            selectedTeams={selectedTeams}
            selectedVizTypes={selectedVizTypes}
            selectedTags={selectedTags}
            onToggleTeam={toggleTeam}
            onToggleViz={toggleViz}
            onToggleTag={toggleTag}
            filtersActive={filtersActive}
            onResetFilters={resetFilters}
            treeGroups={treeGroups}
            expandedTeams={expandedTeams}
            onToggleTeamExpanded={toggleTeamExpanded}
            selectedBid={selectedBid}
            onSelect={setSelectedBid}
            totalCount={blocks.length}
          />
          {/* Center column wraps the grid + bottom panel so the panel
              only spans this column's width — never under the left rail. */}
          <div className="bloklar-center">
            <BlockGrid
              loading={loading}
              error={error}
              blocks={filteredBlocks}
              totalCount={blocks.length}
              selectedBid={selectedBid}
              onSelect={setSelectedBid}
              onEmptyClick={isBottomOpen ? closeBottom : undefined}
            />
            {isBottomOpen && (
              <BottomPanel
                block={selectedBlock}
                onClose={closeBottom}
              />
            )}
          </div>
        </div>
      </div>
    </>
  );
}


// ── Topbar (shared shape with Keşif) ─────────────────────────────────


const PRISMA_LOGO_URL = "/presentations/static/img/prisma_logo.png";

function Topbar({ userName, userDept }) {
  return (
    <header className="kesif-topbar">
      <a href="/presentations/" className="kesif-topbar__logo" title="PRISMA — Tüm sunumlara dön">
        <img src={PRISMA_LOGO_URL} alt="PRISMA" className="kesif-topbar__logo-img" />
      </a>
      <span className="kesif-topbar__brand">
        Atölye
        <span className="kesif-topbar__crumb">/</span>
        Bloklar
      </span>
      <nav className="kesif-topbar__tabs">
        <a href="/presentations/atolye/kesif" className="kesif-topbar__tab">Tables</a>
        <a href="/presentations/atolye/bloklar" className="kesif-topbar__tab is-active">Blocks</a>
        <a href="/presentations/atolye/surecler" className="kesif-topbar__tab">Processes</a>
      </nav>
      <span className="kesif-topbar__spacer" style={{ flex: 1 }} />
      {userName && (
        <span style={{ fontSize: 11, color: "#94a3b8" }}>
          {userName}{userDept ? ` · ${userDept}` : ""}
        </span>
      )}
      <a href="/presentations/" className="kesif-topbar__home">← Tüm Sunumlar</a>
    </header>
  );
}


// ── Left rail ────────────────────────────────────────────────────────


function LeftRail({
  facets, loading, error,
  search, onSearch,
  selectedTeams, selectedVizTypes, selectedTags,
  onToggleTeam, onToggleViz, onToggleTag,
  filtersActive, onResetFilters,
  treeGroups, expandedTeams, onToggleTeamExpanded,
  selectedBid, onSelect,
  totalCount,
}) {
  return (
    <aside className="kesif-left">
      <div className="kesif-left__scroll">
        <FilterGroup
          title="Ekip"
          items={facets.teams}
          selected={selectedTeams}
          onToggle={onToggleTeam}
        />
        <FilterGroup
          title="Görsel tipi"
          items={facets.viz}
          selected={selectedVizTypes}
          onToggle={onToggleViz}
          labelFor={vizLabel}
        />
        <FilterGroup
          title="Etiket"
          items={facets.tags}
          selected={selectedTags}
          onToggle={onToggleTag}
        />
        {filtersActive && (
          <div className="kesif-filter-apply">
            <button
              type="button"
              className="kesif-btn kesif-filter-apply__btn"
              onClick={onResetFilters}
              title="Filtreleri sıfırla"
            >
              Sıfırla
            </button>
          </div>
        )}

        <div className="kesif-tree">
          <h3 className="kesif-tree__section">
            Bloklar
            <span className="kesif-filter-option__count" style={{marginLeft:'auto'}}>
              {totalCount}
            </span>
          </h3>
          {/* Block search lives inside the Bloklar tree section — mirrors
             Keşif's tablo araması inside Şemalar so the two screens read
             as siblings. */}
          <div className="kesif-search kesif-search--inline">
            <input
              type="search"
              placeholder="🔍 Blok ara…"
              value={search}
              onChange={(e) => onSearch(e.target.value)}
              aria-label="Blok ara"
            />
          </div>
          {loading ? (
            <div className="kesif-tree__empty"><Loader2 size={12} className="kesif-spin" /> Yükleniyor…</div>
          ) : error ? (
            <div className="kesif-tree__empty" style={{ color: "#b91c1c" }}>{error}</div>
          ) : treeGroups.byTeam.size === 0 ? (
            <div className="kesif-tree__empty">Sonuç bulunamadı</div>
          ) : (
            [...treeGroups.byTeam.entries()].sort().map(([team, items]) => {
              const expanded = expandedTeams.has(team);
              return (
                <div key={team}>
                  <div
                    className="kesif-tree__dept"
                    onClick={() => onToggleTeamExpanded(team)}
                    role="button"
                    tabIndex={0}
                  >
                    <span className="kesif-tree__dept-caret">
                      {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                    </span>
                    {team === "—" ? "Diğer" : team}
                    <span className="kesif-filter-option__count" style={{marginLeft:'auto'}}>
                      {items.length}
                    </span>
                  </div>
                  {expanded && (
                    <div className="kesif-tree__tables">
                      {items.map((b) => {
                        const bid = b.library_id || b.id;
                        return (
                          <div
                            key={bid}
                            className={`kesif-tree__table${selectedBid === bid ? " is-selected" : ""}`}
                            onClick={() => onSelect(bid)}
                            role="button"
                            tabIndex={0}
                            title={b.name || bid}
                          >
                            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {b.name || "(adsız)"}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>{/* /.kesif-left__scroll */}

      {/* Chat panel — same Sunum-style markup as Keşif so the two screens
         read identically. propose_blocks LLM lands in Phase 10 (marketplace
         MVP), so this is read-only for now. */}
      <ChatDrawer
        title="Sohbet"
        readOnly
        readOnlyHint="Blok önerisi sohbeti Phase 10 ile geliyor."
        seedHistory={[]}
        basketTableIds={new Set()}
      />
    </aside>
  );
}


function FilterGroup({ title, items, selected, onToggle, labelFor, defaultLimit = 5 }) {
  const entries = Object.entries(items || {});
  const [showAll, setShowAll] = useState(false);
  if (entries.length === 0) return null;
  const visible = showAll ? entries : entries.slice(0, defaultLimit);
  const hiddenCount = entries.length - visible.length;
  return (
    <div>
      <h4 className="kesif-left__heading">{title}</h4>
      <div className="kesif-filter-group">
        {visible.map(([k, count]) => (
          <label key={k} className="kesif-filter-option">
            <input
              type="checkbox"
              checked={selected.has(k)}
              onChange={() => onToggle(k)}
            />
            <span>{labelFor ? labelFor(k) : k}</span>
            <span className="kesif-filter-option__count">{count}</span>
          </label>
        ))}
        {hiddenCount > 0 && (
          <button
            type="button"
            className="kesif-filter-group__more"
            onClick={() => setShowAll(true)}
          >
            +{hiddenCount} daha göster
          </button>
        )}
        {showAll && entries.length > defaultLimit && (
          <button
            type="button"
            className="kesif-filter-group__more"
            onClick={() => setShowAll(false)}
          >
            Daha az göster
          </button>
        )}
      </div>
    </div>
  );
}


// ── Center: card grid ────────────────────────────────────────────────


function BlockGrid({ loading, error, blocks, totalCount, selectedBid, onSelect, onEmptyClick }) {
  // Click anywhere on the canvas background (not a card) → close the
  // bottom panel. We test e.target === e.currentTarget so card clicks
  // (which bubble) don't accidentally dismiss.
  const onCanvasClick = (e) => {
    if (!onEmptyClick) return;
    if (e.target === e.currentTarget) onEmptyClick();
  };

  if (loading) {
    return (
      <section className="bloklar-canvas">
        <div className="bloklar-canvas__empty">
          <Loader2 size={20} className="kesif-spin" /> Bloklar yükleniyor…
        </div>
      </section>
    );
  }
  if (error) {
    return (
      <section className="bloklar-canvas">
        <div className="bloklar-canvas__empty" style={{ color: "#b91c1c" }}>{error}</div>
      </section>
    );
  }
  if (blocks.length === 0) {
    return (
      <section className="bloklar-canvas" onClick={onCanvasClick}>
        <div className="bloklar-canvas__empty">
          {totalCount === 0
            ? "Henüz library'de blok yok. Bir Sunum'da blok kaydedince burada görüneceksin."
            : "Filtreye uyan blok bulunamadı — yan paneldeki filtreleri gevşet."}
        </div>
      </section>
    );
  }

  return (
    <section className="bloklar-canvas" onClick={onCanvasClick}>
      <div className="bloklar-grid" onClick={onCanvasClick}>
        {blocks.map((b) => {
          const bid = b.library_id || b.id;
          return (
            <BlockCard
              key={bid}
              block={b}
              isSelected={selectedBid === bid}
              onSelect={onSelect}
            />
          );
        })}
      </div>
    </section>
  );
}


function BlockCard({ block, isSelected, onSelect }) {
  const bid = block.library_id || block.id;
  const viz = block.block_type || block.viz_type;
  const team = block.owner_department || block.team || "—";
  const tags = block.tags || [];
  // stopPropagation so the canvas-level "click outside" handler doesn't
  // immediately re-close the panel after a card click toggles it open.
  const onCardClick = (e) => {
    e.stopPropagation();
    onSelect(bid);
  };
  return (
    <article
      className={`bloklar-card${isSelected ? " is-selected" : ""}`}
      onClick={onCardClick}
      role="button"
      tabIndex={0}
    >
      <div className="bloklar-card__head">
        <div className="bloklar-card__name" title={block.name}>{block.name || "(adsız)"}</div>
        {viz && <span className="bloklar-card__viz">{vizLabel(viz)}</span>}
      </div>
      <div className="bloklar-card__meta">
        <span><Users size={11} /> {team}</span>
        {block.owner_id && <span style={{ color: "#94a3b8" }}>{block.owner_id}</span>}
      </div>
      {block.description && (
        <p className="bloklar-card__desc">{block.description}</p>
      )}
      {tags.length > 0 && (
        <div className="bloklar-card__tags">
          {tags.slice(0, 4).map((t) => (
            <span key={t} className="bloklar-card__tag"><Tag size={9} /> {t}</span>
          ))}
          {tags.length > 4 && (
            <span className="bloklar-card__tag bloklar-card__tag--more">+{tags.length - 4}</span>
          )}
        </div>
      )}
    </article>
  );
}


// ── Bottom panel (replaces the right rail) ──────────────────────────
//
// Mirrors Hazırlık's bottom-drawer pattern. Slides up from below when
// a block is selected. Two halves: left = full preview iframe, right
// = metadata + actions. Closing the panel clears the selection.
//
// Now lives inside .bloklar-center, so it spans the center column only
// (never extends under the left rail).

function BottomPanel({ block, onClose }) {
  if (!block) return null;
  const bid = block.library_id || block.id;
  const viz = block.block_type || block.viz_type;
  const team = block.owner_department || block.team || "—";
  const tags = block.tags || [];
  const tables = block.used_tables || [];

  // stopPropagation so clicks inside the panel don't bubble to the
  // canvas-level click-outside handler.
  const onPanelClick = (e) => e.stopPropagation();

  return (
    <section
      className="bloklar-bottom"
      role="region"
      aria-label="Blok detayı"
      onClick={onPanelClick}
    >
      <header className="bloklar-bottom__header">
        <div className="bloklar-bottom__title-block">
          <div className="bloklar-bottom__title">{block.name || "(adsız)"}</div>
          <div className="bloklar-bottom__subtitle">
            {team}{viz ? ` · ${vizLabel(viz)}` : ""}
          </div>
        </div>
        <button
          type="button"
          className="bloklar-bottom__close"
          onClick={onClose}
          title="Kapat"
          aria-label="Kapat"
        >
          ×
        </button>
      </header>

      <div className="bloklar-bottom__body">
        {/* Left half — preview iframe */}
        <div className="bloklar-bottom__preview">
          <iframe
            className="bloklar-bottom__iframe"
            src={previewUrl(bid)}
            title={`${block.name} önizleme`}
          />
        </div>

        {/* Right half — metadata + actions */}
        <div className="bloklar-bottom__meta">
          {block.description && (
            <div className="kesif-card__section">
              <div className="kesif-card__section-title">Açıklama</div>
              <div className="kesif-card__description">{block.description}</div>
            </div>
          )}

          {tags.length > 0 && (
            <div className="kesif-card__section">
              <div className="kesif-card__section-title">Etiketler</div>
              <div className="bloklar-card__tags" style={{ marginTop: 4 }}>
                {tags.map((t) => (
                  <span key={t} className="bloklar-card__tag"><Tag size={9} /> {t}</span>
                ))}
              </div>
            </div>
          )}

          {tables.length > 0 && (
            <div className="kesif-card__section">
              <div className="kesif-card__section-title">Kullanılan tablolar</div>
              <div className="kesif-columns">
                {tables.join(", ")}
              </div>
            </div>
          )}

          {block.owner_id && (
            <div className="kesif-card__section">
              <div className="kesif-card__section-title">Sahip</div>
              <div className="kesif-card__description">{block.owner_id}</div>
            </div>
          )}

          <div className="kesif-actions" style={{ marginTop: "auto" }}>
            <button
              type="button"
              className="kesif-btn kesif-btn--primary"
              disabled
              title="Yakında — Phase 10"
            >
              <PlusCircle size={14} />
              Sunumuma ekle
            </button>
            <a
              href={previewUrl(bid)}
              target="_blank"
              rel="noopener noreferrer"
              className="kesif-btn"
              title="Yeni sekmede aç"
            >
              <Eye size={14} />
              Tam ekran
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}


// ── Mount ──────────────────────────────────────────────────────────────

const style = document.createElement("style");
style.textContent = `
  @keyframes kesif-spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
  .kesif-spin { animation: kesif-spin 0.8s linear infinite; }
`;
document.head.appendChild(style);

createRoot(document.getElementById("bloklar-root")).render(<App />);
