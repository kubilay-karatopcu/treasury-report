/* Phase 11.workbench — Bloklar view inside the unified kesif shell.
 *
 * Exports:
 *   - useBlocksState(endpoints)  → hook returning all block-side state
 *   - <BlocksFilters {...state}/> → left-rail filter content (3 FilterGroups
 *                                    + reset bar + tree by team)
 *   - <BlocksCenter  {...state}/> → center grid + bottom detail panel
 *
 * Extracted verbatim from the legacy bloklar/index.jsx App so the kesif
 * shell can mount the same UI without page-reloading between views. The
 * legacy entry point is now dead; /atolye/bloklar serves kesif.html.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown, ChevronRight, Loader2,
  PlusCircle, Eye, Tag, Users,
} from "lucide-react";

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


// ── State hook ────────────────────────────────────────────────────────────

export function useBlocksState({ libraryListUrl, libraryPreviewTemplate }) {
  const [blocks, setBlocks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [selectedTeams, setSelectedTeams] = useState(new Set());
  const [selectedVizTypes, setSelectedVizTypes] = useState(new Set());
  const [selectedTags, setSelectedTags] = useState(new Set());
  const [expandedTeams, setExpandedTeams] = useState(new Set());
  const [selectedBid, setSelectedBid] = useState(null);

  // 200ms debounce — mirrors Keşif catalog search.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim().toLowerCase()), 200);
    return () => clearTimeout(t);
  }, [search]);

  // Lazy fetch. We don't paginate; even at 500+ blocks the JSON is small.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(libraryListUrl, { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`library HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (cancelled) return;
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
  }, [libraryListUrl]);

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

  const filteredBlocks = useMemo(() => blocks.filter((b) => {
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
  }), [blocks, selectedTeams, selectedVizTypes, selectedTags, debouncedSearch]);

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

  const toggleSet = (setter) => (v) => setter((prev) => {
    const next = new Set(prev);
    if (next.has(v)) next.delete(v); else next.add(v);
    return next;
  });
  const toggleTeam = useCallback(toggleSet(setSelectedTeams), []);
  const toggleViz  = useCallback(toggleSet(setSelectedVizTypes), []);
  const toggleTag  = useCallback(toggleSet(setSelectedTags), []);
  const toggleTeamExpanded = useCallback((team) => setExpandedTeams((prev) => {
    const next = new Set(prev);
    if (next.has(team)) next.delete(team); else next.add(team);
    return next;
  }), []);
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

  const previewUrl = useCallback(
    (bid) => libraryPreviewTemplate ? libraryPreviewTemplate.replace("{bid}", bid) : "#",
    [libraryPreviewTemplate],
  );

  return {
    blocks, loading, error,
    search, setSearch, debouncedSearch,
    selectedTeams, selectedVizTypes, selectedTags,
    toggleTeam, toggleViz, toggleTag, resetFilters,
    expandedTeams, toggleTeamExpanded,
    selectedBid, setSelectedBid, selectedBlock,
    facets, filteredBlocks, treeGroups,
    filtersActive, previewUrl,
  };
}


// ── Left-rail content (FilterGroups + tree by team + search) ───────────

function FilterGroup({ title, items, selected, onToggle, labelFor, defaultLimit = 5 }) {
  const entries = Object.entries(items || {});
  const [showAll, setShowAll] = useState(false);
  if (entries.length === 0) return null;
  const display = showAll ? entries : entries.slice(0, defaultLimit);
  const extra = entries.length - display.length;
  return (
    <div className="kesif-filter-group">
      <h4 className="kesif-filter-group__title">{title}</h4>
      {display.map(([k, n]) => (
        <label key={k} className="kesif-filter-option">
          <input
            type="checkbox"
            checked={selected.has(k)}
            onChange={() => onToggle(k)}
          />
          <span style={{ flex: 1 }}>{labelFor ? labelFor(k) : k}</span>
          <span className="kesif-filter-option__count">{n}</span>
        </label>
      ))}
      {extra > 0 && !showAll && (
        <button
          type="button"
          className="kesif-filter-group__more"
          onClick={() => setShowAll(true)}
        >
          +{extra} daha göster
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
  );
}


export function BlocksFilters({
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
    <>
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
          <span className="kesif-filter-option__count" style={{ marginLeft: "auto" }}>
            {totalCount}
          </span>
        </h3>
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
          <div className="kesif-tree__empty">
            <Loader2 size={12} className="kesif-spin" /> Yükleniyor…
          </div>
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
                  <span className="kesif-filter-option__count" style={{ marginLeft: "auto" }}>
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
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
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
    </>
  );
}


// ── Center: grid + bottom detail panel ─────────────────────────────────

export function BlocksCenter({
  loading, error, filteredBlocks, totalCount,
  selectedBid, setSelectedBid, selectedBlock, previewUrl,
  basketBlockIds, onAddBlockToBasket, basketBusy,
}) {
  const isBottomOpen = !!selectedBlock;
  const closeBottom = useCallback(() => setSelectedBid(null), [setSelectedBid]);
  const onCanvasClick = (e) => {
    if (!isBottomOpen) return;
    if (e.target === e.currentTarget) closeBottom();
  };

  return (
    <div className={`bloklar-center${isBottomOpen ? " has-bottom" : ""}`}>
      <BlockGrid
        loading={loading}
        error={error}
        blocks={filteredBlocks}
        totalCount={totalCount}
        selectedBid={selectedBid}
        onSelect={setSelectedBid}
        onEmptyClick={isBottomOpen ? closeBottom : undefined}
        basketBlockIds={basketBlockIds}
      />
      {isBottomOpen && (
        <BottomPanel
          block={selectedBlock}
          onClose={closeBottom}
          previewUrl={previewUrl}
          inBasket={basketBlockIds?.has(selectedBlock.library_id || selectedBlock.id)}
          onAddToBasket={onAddBlockToBasket}
          basketBusy={basketBusy}
        />
      )}
    </div>
  );
}


function BlockGrid({ loading, error, blocks, totalCount, selectedBid, onSelect, onEmptyClick, basketBlockIds }) {
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
              inBasket={basketBlockIds?.has(bid)}
              onSelect={onSelect}
            />
          );
        })}
      </div>
    </section>
  );
}


function BlockCard({ block, isSelected, inBasket = false, onSelect }) {
  const bid = block.library_id || block.id;
  const viz = block.block_type || block.viz_type;
  const team = block.owner_department || block.team || "—";
  const tags = block.tags || [];
  // Phase 11.polish-final: clicking the already-selected card toggles
  // the bottom panel closed. Lets the user dismiss without aiming the
  // tiny × close button.
  const onCardClick = (e) => {
    e.stopPropagation();
    onSelect(isSelected ? null : bid);
  };
  return (
    <article
      className={`bloklar-card${isSelected ? " is-selected" : ""}${inBasket ? " is-in-basket" : ""}`}
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
        {block.owner_id && <span style={{ color: "var(--editor-ink-faint, #94a3b8)" }}>{block.owner_id}</span>}
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


function BottomPanel({ block, onClose, previewUrl, inBasket, onAddToBasket, basketBusy }) {
  if (!block) return null;
  const bid = block.library_id || block.id;
  const viz = block.block_type || block.viz_type;
  const team = block.owner_department || block.team || "—";
  const tags = block.tags || [];
  const tables = block.used_tables || [];
  const onPanelClick = (e) => e.stopPropagation();

  // Phase 11.basket-blocks: "Sepete ekle". Calls the parent App's
  // addBlockToBasket which POSTs to /basket with kind:"block". User picks
  // up the block later when designing the Sunum (Phase 11+).
  const handleAddToBasket = async () => {
    if (inBasket || basketBusy) return;
    await onAddToBasket?.(block);
  };
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
        <div className="bloklar-bottom__preview">
          <iframe
            className="bloklar-bottom__iframe"
            src={previewUrl(bid)}
            title={`${block.name} önizleme`}
          />
        </div>
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
              onClick={handleAddToBasket}
              disabled={inBasket || basketBusy}
              title={inBasket
                ? "Bu blok sepetinde — Sunum'a geçince kullanabileceksin"
                : "Bu bloğu sepete ekle. Sunum'a geçince bloklarını oraya koyabilirsin."}
            >
              <PlusCircle size={14} />
              {inBasket ? "Sepette" : (basketBusy ? "Ekleniyor…" : "Sepete ekle")}
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
