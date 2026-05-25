/* Phase 9.e — Atölye / Bloklar (Hybrid C).
 *
 * Layout mirrors Keşif:
 *   left rail  — search + filters (team / viz_type / tag) + tree of saved
 *                blocks grouped by team + chat panel at the bottom
 *   center     — responsive card grid of block summaries
 *   right rail — selected block's detail card + actions
 *
 * Backend: reuses Phase 6.5.a's existing /presentations/library JSON.
 * No new server-side schema introduced here — just a new way to browse
 * what's already there. The detail iframe points to
 * /presentations/library/<bid>/preview which renders the block in a
 * read-only Sunum shell.
 *
 * Chat is wired but the real "propose_blocks" LLM contract lands in
 * Phase 10 with the marketplace MVP. For 9.e the panel surfaces a
 * "Yakında" hint instead of a live send button.
 */
import { createRoot } from "react-dom/client";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Search, X, Filter, ChevronDown, ChevronRight, Loader2, MessageCircle,
  PlusCircle, Eye, Tag, Users, Layers,
} from "lucide-react";


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
  const [collapsedTeams, setCollapsedTeams] = useState(new Set());

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
  const toggleTeamCollapsed = (team) => setCollapsedTeams((prev) => {
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

  return (
    <>
      <Topbar userName={USER.name} userDept={USER.department} />
      <div className="kesif-body">
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
          collapsedTeams={collapsedTeams}
          onToggleTeamCollapsed={toggleTeamCollapsed}
          selectedBid={selectedBid}
          onSelect={setSelectedBid}
          totalCount={blocks.length}
        />
        <BlockGrid
          loading={loading}
          error={error}
          blocks={filteredBlocks}
          totalCount={blocks.length}
          selectedBid={selectedBid}
          onSelect={setSelectedBid}
        />
        <DetailRail
          block={selectedBlock}
        />
      </div>
    </>
  );
}


// ── Topbar (shared shape with Keşif) ─────────────────────────────────


function Topbar({ userName, userDept }) {
  return (
    <header className="kesif-topbar">
      <span className="kesif-topbar__brand">
        <Layers size={16} />
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
  treeGroups, collapsedTeams, onToggleTeamCollapsed,
  selectedBid, onSelect,
  totalCount,
}) {
  return (
    <aside className="kesif-left">
      <div className="kesif-search">
        <input
          type="search"
          placeholder="🔍 Blok ara…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          aria-label="Blok ara"
        />
      </div>

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
        {loading ? (
          <div className="kesif-tree__empty"><Loader2 size={12} className="kesif-spin" /> Yükleniyor…</div>
        ) : error ? (
          <div className="kesif-tree__empty" style={{ color: "#b91c1c" }}>{error}</div>
        ) : treeGroups.byTeam.size === 0 ? (
          <div className="kesif-tree__empty">Sonuç bulunamadı</div>
        ) : (
          [...treeGroups.byTeam.entries()].sort().map(([team, items]) => {
            const collapsed = collapsedTeams.has(team);
            return (
              <div key={team}>
                <div
                  className="kesif-tree__dept"
                  onClick={() => onToggleTeamCollapsed(team)}
                  role="button"
                  tabIndex={0}
                >
                  <span className="kesif-tree__dept-caret">
                    {collapsed ? <ChevronRight size={10} /> : <ChevronDown size={10} />}
                  </span>
                  {team === "—" ? "Diğer" : team}
                  <span className="kesif-filter-option__count" style={{marginLeft:'auto'}}>
                    {items.length}
                  </span>
                </div>
                {!collapsed && (
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

      {/* Chat panel placeholder for 9.e — propose_blocks LLM lands in
         Phase 10 (marketplace MVP). The visual slot is reserved so the
         layout stays consistent with Keşif. */}
      <section className="kesif-chat kesif-chat--collapsed">
        <header className="kesif-chat__header" title="Yakında — Phase 10">
          <span className="kesif-chat__title">
            <MessageCircle size={12} />
            Sohbet (yakında)
          </span>
        </header>
      </section>
    </aside>
  );
}


function FilterGroup({ title, items, selected, onToggle, labelFor }) {
  const entries = Object.entries(items || {});
  if (entries.length === 0) return null;
  return (
    <div>
      <h4 className="kesif-left__heading">{title}</h4>
      <div className="kesif-filter-group">
        {entries.map(([k, count]) => (
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
      </div>
    </div>
  );
}


// ── Center: card grid ────────────────────────────────────────────────


function BlockGrid({ loading, error, blocks, totalCount, selectedBid, onSelect }) {
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
      <section className="bloklar-canvas">
        <div className="bloklar-canvas__empty">
          {totalCount === 0
            ? "Henüz library'de blok yok. Bir Sunum'da blok kaydedince burada görüneceksin."
            : "Filtreye uyan blok bulunamadı — yan paneldeki filtreleri gevşet."}
        </div>
      </section>
    );
  }

  return (
    <section className="bloklar-canvas">
      <div className="bloklar-grid">
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
  return (
    <article
      className={`bloklar-card${isSelected ? " is-selected" : ""}`}
      onClick={() => onSelect(bid)}
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


// ── Right rail: detail card ──────────────────────────────────────────


function DetailRail({ block }) {
  if (!block) {
    return (
      <aside className="kesif-right">
        <div className="kesif-card">
          <h3 className="kesif-left__heading">Detay</h3>
          <div className="kesif-card__empty">Soldan bir blok seç</div>
        </div>
      </aside>
    );
  }
  const bid = block.library_id || block.id;
  const viz = block.block_type || block.viz_type;
  const team = block.owner_department || block.team || "—";
  const tags = block.tags || [];
  const tables = block.used_tables || [];

  return (
    <aside className="kesif-right">
      <div className="kesif-card">
        <div className="kesif-card__header">
          <div>
            <div className="kesif-card__title">{block.name || "(adsız)"}</div>
            <div className="kesif-card__schema">
              {team}
              {viz ? ` · ${vizLabel(viz)}` : ""}
            </div>
          </div>
          {viz && (
            <span className="kesif-card__source-badge">
              {vizLabel(viz)}
            </span>
          )}
        </div>

        <div className="kesif-card__metaline">
          {block.owner_id && <span>👤 {block.owner_id}</span>}
          {tables.length > 0 && <span>📊 {tables.length} tablo</span>}
        </div>

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

        <div className="kesif-card__section">
          <div className="kesif-card__section-title">Önizleme</div>
          <iframe
            className="bloklar-preview-iframe"
            src={previewUrl(bid)}
            title={`${block.name} önizleme`}
          />
        </div>

        <div className="kesif-actions">
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
    </aside>
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
