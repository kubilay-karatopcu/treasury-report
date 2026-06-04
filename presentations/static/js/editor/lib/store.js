import { create } from 'zustand';
import { applyPatches as _applyPatches } from './patch.js';
import {
  submitPatches, refreshBlockData, runBlockManual,
  applyDashboardFilters,
} from './api.js';

// ── Helpers for nested manifest navigation ─────────────────────────────────

// Level-2 container block types — they live inside section.children and hold
// leaf blocks at level 3. carousel = slides (one shown at a time); canvas =
// 12-column grid (madde 2). Path/traversal logic treats them identically.
export const CONTAINER_TYPES = new Set(['carousel', 'canvas']);

export function findBlockPath(manifest, blockId) {
  const sections = manifest?.blocks || [];
  for (let si = 0; si < sections.length; si++) {
    const section = sections[si];
    if (section.id === blockId) {
      return { section, sectionIdx: si, child: null, childIdx: null, slide: null, slideIdx: null, path: `/blocks/${si}` };
    }
    const children = section.children || [];
    for (let ci = 0; ci < children.length; ci++) {
      const child = children[ci];
      if (child.id === blockId) {
        return {
          section, sectionIdx: si,
          child, childIdx: ci,
          slide: null, slideIdx: null,
          path: `/blocks/${si}/children/${ci}`,
        };
      }
      // Container (carousel/canvas) çocukları — 3. seviye
      if (CONTAINER_TYPES.has(child.type)) {
        const kids = child.children || [];
        for (let ki = 0; ki < kids.length; ki++) {
          if (kids[ki].id === blockId) {
            return {
              section, sectionIdx: si,
              child, childIdx: ci,
              slide: kids[ki], slideIdx: ki,
              path: `/blocks/${si}/children/${ci}/children/${ki}`,
            };
          }
        }
      }
    }
  }
  return null;
}

// Bir bloğu id ile bul ve nesnesini döndür (section / child / container child).
// DnD önizlemesi sürüklenen bloğun width/title'ını buradan okur.
export function getBlockById(manifest, blockId) {
  const loc = findBlockPath(manifest, blockId);
  if (!loc) return null;
  return loc.slide ?? loc.child ?? loc.section ?? null;
}

function updateBlockInPlace(manifest, blockId, fn) {
  const sections = manifest.blocks.map((section) => {
    if (section.id === blockId) return fn(section);
    const children = section.children || [];
    let touched = false;
    const newChildren = children.map((c) => {
      if (c.id === blockId) { touched = true; return fn(c); }
      // Container (carousel/canvas) çocukları — 3. seviye nesting
      if (CONTAINER_TYPES.has(c.type) && Array.isArray(c.children)) {
        let kidTouched = false;
        const newKids = c.children.map((kid) => {
          if (kid.id === blockId) { kidTouched = true; return fn(kid); }
          return kid;
        });
        if (kidTouched) {
          touched = true;
          return { ...c, children: newKids };
        }
      }
      return c;
    });
    return touched ? { ...section, children: newChildren } : section;
  });
  return { ...manifest, blocks: sections };
}

// ── Empty block templates (used when user manually adds blocks) ────────────

/**
 * Walk every leaf block in `manifest` and return the set of semantic_tags
 * that are referenced by at least one variable. Used by the orphan filter
 * cleanup to decide which filters still have a "purpose".
 */
function _collectUsedSemanticTags(manifest) {
  const tags = new Set();
  for (const section of manifest?.blocks || []) {
    for (const child of section?.children || []) {
      for (const v of (child.variables || [])) {
        if (v.semantic_tag) tags.add(v.semantic_tag);
      }
      if (CONTAINER_TYPES.has(child.type)) {
        for (const kid of (child.children || [])) {
          for (const v of (kid.variables || [])) {
            if (v.semantic_tag) tags.add(v.semantic_tag);
          }
        }
      }
    }
  }
  return tags;
}


/**
 * After a block delete, any filter whose semantic_tag is no longer referenced
 * by ANY remaining block variable is an orphan. Return JSON-Patch remove ops
 * that drop them. Run on the post-delete manifest.
 *
 * Walking back-to-front so list indices stay valid as we remove.
 */
function _computeOrphanFilterPatches(manifestAfter) {
  const filters = manifestAfter?.filters;
  if (!Array.isArray(filters) || filters.length === 0) return [];
  const usedTags = _collectUsedSemanticTags(manifestAfter);
  const patches = [];
  for (let i = filters.length - 1; i >= 0; i--) {
    const f = filters[i];
    if (!usedTags.has(f.semantic_tag)) {
      patches.push({ op: 'remove', path: `/filters/${i}` });
    }
  }
  return patches;
}


/**
 * Walk every leaf block in `manifest` and propose JSON-Patch operations
 * that auto-bind matching variables to a newly-added dashboard filter.
 *
 * Match rules (mirror presentations/dashboards/binding.py:propose_auto_bindings):
 * - variable.semantic_tag === filter.semantic_tag
 * - variable.name has no existing binding
 * - For date variables targeting a date_range filter, the variable's name
 *   suffix (_from / _since / _start vs _to / _until / _end) picks the
 *   accessor. Ambiguous date vars (no suffix) are left unbound — the
 *   "Filter eklemek ister misiniz?" banner in 6.5.c.2.b will surface them.
 * - For other types, types must match exactly (enum_multi ↔ enum_multi, etc.).
 */
function _computeAutoBindPatches(manifest, filterDef) {
  if (!manifest) return [];
  const out = [];

  function visit(block, basePath) {
    if (!block || block.type === 'section_header') return;
    if (!Array.isArray(block.variables)) return;
    const existing = block.variable_bindings || {};
    const newBindings = { ...existing };
    let changed = false;

    for (const v of block.variables) {
      if (v.semantic_tag !== filterDef.semantic_tag) continue;
      if (newBindings[v.name]) continue;   // already bound, don't clobber
      // Pairing rules:
      if (v.type === 'date' && filterDef.type === 'date_range') {
        const lower = v.name.toLowerCase();
        let accessor = null;
        if (/_(from|since|start)$/.test(lower)) accessor = 'from';
        else if (/_(to|until|end)$/.test(lower)) accessor = 'to';
        if (!accessor) continue;
        newBindings[v.name] = { from_filter: filterDef.id, accessor };
        changed = true;
      } else if (v.type === filterDef.type) {
        newBindings[v.name] = { from_filter: filterDef.id };
        changed = true;
      }
    }
    if (changed) {
      const op = block.variable_bindings ? 'replace' : 'add';
      out.push({ op, path: `${basePath}/variable_bindings`, value: newBindings });
    }
  }

  const sections = manifest.blocks || [];
  for (let si = 0; si < sections.length; si++) {
    const sec = sections[si];
    const children = sec.children || [];
    for (let ci = 0; ci < children.length; ci++) {
      const child = children[ci];
      visit(child, `/blocks/${si}/children/${ci}`);
      if (CONTAINER_TYPES.has(child.type) && Array.isArray(child.children)) {
        for (let sli = 0; sli < child.children.length; sli++) {
          visit(child.children[sli], `/blocks/${si}/children/${ci}/children/${sli}`);
        }
      }
    }
  }
  return out;
}


function _emptyBlockTemplate(id, type) {
  // Phase 6.5: every data-bound block carries `query` (raw SQL with :binds)
  // and `variables` (per-bind metadata). Whether the SQL is authored by the
  // user or by the LLM, the shape is identical — only one editor surface in
  // Properties handles both.
  const base = { id, type, title: _defaultTitle(type), locked: false };
  const dataBound = !['narrative', 'carousel', 'canvas'].includes(type);
  if (dataBound) {
    base.query = '';
    base.variables = [];
  }
  switch (type) {
    case 'kpi':
      return { ...base, data_source: { original_sql: '' },
               config: { value: 0, unit: '', delta: 0, delta_label: '', period: '' } };
    case 'bar_chart':
      return { ...base, data_source: { original_sql: '' },
               config: { categories: [], series: [{ name: 'Seri 1', values: [] }] } };
    case 'line_chart':
    case 'area_chart':
      return { ...base, data_source: { original_sql: '' },
               config: { x_axis: [], series: [{ name: 'Seri 1', values: [] }] } };
    case 'combo_chart':
      // Combo: single query → [kategori, deger1, deger2, …]. Series + roles
      // (kind/axis) are populated on run by the backend; empty until then.
      return { ...base, data_source: { original_sql: '' },
               config: { categories: [], series: [], left_axis_title: '', right_axis_title: '' } };
    case 'pie_chart':
      return { ...base, data_source: { original_sql: '' },
               config: { labels: [], values: [] } };
    case 'heatmap':
      return { ...base, data_source: { original_sql: '' },
               config: { x_axis: [], series: [] } };
    case 'radial_bar':
      return { ...base, data_source: { original_sql: '' },
               config: { value: 0, max: 100 } };
    case 'data_table':
      return { ...base, data_source: { original_sql: '' },
               config: { columns: [], rows: [] } };
    case 'narrative':
      return { ...base, config: { text: 'Metin yazın…' } };
    case 'carousel':
      return { ...base, title: 'Yeni Carousel', config: {}, children: [] };
    case 'canvas':
      return { ...base, title: 'Yeni Tuval', config: {}, children: [] };
    default:
      return { ...base, config: {} };
  }
}

function _defaultTitle(type) {
  const map = {
    kpi:         'Yeni KPI',
    bar_chart:   'Yeni Çubuk Grafik',
    line_chart:  'Yeni Çizgi Grafik',
    combo_chart: 'Yeni Combo Grafik',
    area_chart:  'Yeni Alan Grafiği',
    pie_chart:   'Yeni Pasta Grafik',
    heatmap:     'Yeni Isı Haritası',
    radial_bar:  'Yeni Gösterge',
    data_table:  'Yeni Tablo',
    narrative:   'Yeni Metin',
    carousel:    'Yeni Carousel',
    canvas:      'Yeni Tuval',
  };
  return map[type] || 'Yeni Blok';
}


// ── Store ───────────────────────────────────────────────────────────────────

const useStore = create((set) => ({
  mode:            'editor',
  manifest:        null,
  viewMode:        'edit',
  layoutEditMode:  false,        // "Düzenle" toggle — structural editing UI
  selectedBlockId: null,
  chatHistory:     [],
  loading:         false,
  flashIds:        new Set(),
  shareModal:      null,
  saveModalOpen:   false,        // "Kaydet" tablı modal
  saveBlockModal:  null,         // { blockId } — "Blok kütüphanesine kaydet" modal
  userInfo:        null,         // { sicil, name, department, dashboard_maker }
  addBlockPanel:   null,         // { sectionId } — sağ taraf "Blok Ekle" panel'i
  docsTable:       null,         // { table, domain } — side panel for catalog table docs

  setMode:          (mode)     => set({ mode }),
  setManifest:      (manifest) => {
    // Hydrate filter state from manifest (Phase 6.5.c).
    let fs = {};
    if (manifest?.filter_state && Object.keys(manifest.filter_state).length > 0) {
      fs = { ...manifest.filter_state };
    } else {
      for (const f of (manifest?.filters || [])) {
        if (f.default != null) fs[f.id] = f.default;
      }
    }
    set({
      manifest,
      // Hydrate chat history from manifest on initial load / page refresh.
      chatHistory: Array.isArray(manifest?.chat_history) ? manifest.chat_history : [],
      filterState: fs,
    });
  },
  setViewMode:      (mode)     => set({ viewMode: mode }),
  setSelectedBlock: (id)       => set({ selectedBlockId: id }),
  setLoading:       (loading)  => set({ loading }),
  openShareModal:   (info)     => set({ shareModal: info }),
  closeShareModal:  ()         => set({ shareModal: null }),
  openSaveModal:    ()         => set({ saveModalOpen: true }),
  closeSaveModal:   ()         => set({ saveModalOpen: false }),
  openSaveBlockModal:  (blockId) => set({ saveBlockModal: { blockId } }),
  closeSaveBlockModal: ()        => set({ saveBlockModal: null }),
  setUserInfo:      (info)     => set({ userInfo: info }),
  openAddBlockPanel:  (sectionId) => set({ addBlockPanel: { sectionId } }),
  closeAddBlockPanel: ()         => set({ addBlockPanel: null }),
  toggleLayoutEdit: ()         => set((s) => ({ layoutEditMode: !s.layoutEditMode })),
  setDocsTable:     (info)     => set({ docsTable: info }),
  closeDocsTable:   ()         => set({ docsTable: null }),

  addChatMessage: (msg) => set((state) => ({
    chatHistory: [...state.chatHistory, { ...msg, ts: Date.now() }],
  })),

  clearChat: () => set({ chatHistory: [] }),

  applyPatches: (patches) => set((state) => {
    if (!state.manifest) return {};
    const newManifest = _applyPatches(state.manifest, patches);
    newManifest.version = (newManifest.version || 0) + 1;
    return { manifest: newManifest };
  }),

  // Direct user actions — optimistic local update + backend persistence.

  toggleLock: (blockId) => {
    const state = useStore.getState();
    if (!state.manifest) return;
    const loc = findBlockPath(state.manifest, blockId);
    if (!loc) return;
    const target = loc.child ?? loc.section;
    const newLocked = !target.locked;

    set((s) => ({
      manifest: updateBlockInPlace(s.manifest, blockId, (b) => ({ ...b, locked: newLocked })),
    }));

    submitPatches([
      { op: 'replace', path: `${loc.path}/locked`, value: newLocked },
    ]).catch((e) => console.error('toggleLock persist failed:', e));
  },

  setBlockWidth: (blockId, width) => {
    const state = useStore.getState();
    if (!state.manifest) return;
    const loc = findBlockPath(state.manifest, blockId);
    if (!loc) return;
    const target = loc.child ?? loc.section;
    const goingToFull = width === 'full' || !width;
    const hadWidth = 'width' in target;

    set((s) => ({
      manifest: updateBlockInPlace(s.manifest, blockId, (b) => {
        if (goingToFull) {
          const { width: _drop, ...rest } = b;
          return rest;
        }
        return { ...b, width };
      }),
    }));

    let patch;
    if (goingToFull && hadWidth) {
      patch = { op: 'remove', path: `${loc.path}/width` };
    } else if (goingToFull) {
      return;
    } else if (hadWidth) {
      patch = { op: 'replace', path: `${loc.path}/width`, value: width };
    } else {
      patch = { op: 'add', path: `${loc.path}/width`, value: width };
    }
    submitPatches([patch]).catch((e) => console.error('setBlockWidth persist failed:', e));
  },
  // Layout-edit yapısal işlemler — yeni section / yeni child block ekle.
  // PropertiesPanel hemen açılsın diye yeni bloğu auto-select ediyoruz.
  addSection: () => {
    const state = useStore.getState();
    if (!state.manifest) return;
    const id = 'h_' + Math.random().toString(36).slice(2, 8);
    const newSection = {
      id, type: 'section_header', title: 'Yeni Bölüm',
      locked: false, config: {}, children: [],
    };
    const patch = { op: 'add', path: '/blocks/-', value: newSection };
    set((s) => {
      if (!s.manifest) return {};
      const newManifest = _applyPatches(s.manifest, [patch]);
      newManifest.version = (newManifest.version || 0) + 1;
      return { manifest: newManifest, selectedBlockId: id };
    });
    submitPatches([patch]).catch((e) => console.error('addSection persist failed:', e));
    return id;
  },

  // Phase 6.5.c — insert a saved BlockStore template into a section,
  // auto-binding its variables against the dashboard's filters by
  // matching semantic_tag (spec §3.5).
  addBlockTemplateToSection: (sectionId, templatePayload, ref) => {
    const state = useStore.getState();
    if (!state.manifest || !sectionId || !templatePayload) return;
    const loc = findBlockPath(state.manifest, sectionId);
    if (!loc || loc.child) return;
    const section = loc.section;
    const childIdx = (section.children || []).length;

    // Inline auto-binding by semantic_tag: mirrors propose_auto_bindings()
    // in presentations/dashboards/binding.py. Single-match → bind; multiple
    // or zero → leave unbound (UI banner takes over later).
    const filters = state.manifest.filters || [];
    const byTag = {};
    for (const f of filters) {
      (byTag[f.semantic_tag] = byTag[f.semantic_tag] || []).push(f);
    }
    const variable_bindings = {};
    for (const v of (templatePayload.variables || [])) {
      const candidates = byTag[v.semantic_tag] || [];
      if (candidates.length !== 1) continue;
      const f = candidates[0];
      if (v.type === 'date' && f.type === 'date_range') {
        const lower = v.name.toLowerCase();
        let accessor = null;
        if (/_(from|since|start)$/.test(lower)) accessor = 'from';
        else if (/_(to|until|end)$/.test(lower)) accessor = 'to';
        if (accessor) variable_bindings[v.name] = { from_filter: f.id, accessor };
      } else if (v.type === f.type) {
        variable_bindings[v.name] = { from_filter: f.id };
      }
    }

    // Build the in-presentation block from the template.
    const id = 'b_' + Math.random().toString(36).slice(2, 8);
    const vizType = templatePayload.visualization?.type || 'kpi';
    const newBlock = {
      ..._emptyBlockTemplate(id, vizType === 'bar' ? 'bar_chart'
                            : vizType === 'line' ? 'line_chart'
                            : vizType === 'table' ? 'data_table'
                            : vizType === 'pie' ? 'pie_chart'
                            : vizType),
      title: templatePayload.title || 'Yeni Şablon',
      query: templatePayload.query || '',
      variables: templatePayload.variables || [],
      template_ref: ref,                     // {team, id, version}
      variable_bindings,                     // auto-bound where unambiguous
    };

    const patch = {
      op: 'add',
      path: `${loc.path}/children/${childIdx}`,
      value: newBlock,
    };
    set((s) => {
      if (!s.manifest) return {};
      const newManifest = _applyPatches(s.manifest, [patch]);
      newManifest.version = (newManifest.version || 0) + 1;
      return { manifest: newManifest, selectedBlockId: id };
    });
    submitPatches([patch]).catch((e) =>
      console.error('addBlockTemplateToSection persist failed:', e),
    );
    return id;
  },

  addChildBlock: (sectionId, blockType) => {
    const state = useStore.getState();
    if (!state.manifest || !sectionId || !blockType) return;
    const loc = findBlockPath(state.manifest, sectionId);
    if (!loc || loc.child) return;  // sadece section'a child ekleniyor
    const section = loc.section;
    const childIdx = (section.children || []).length;

    const prefix = blockType === 'narrative' ? 't_' : 'b_';
    const id = prefix + Math.random().toString(36).slice(2, 8);
    const newBlock = _emptyBlockTemplate(id, blockType);

    const patch = {
      op: 'add',
      path: `${loc.path}/children/${childIdx}`,
      value: newBlock,
    };
    set((s) => {
      if (!s.manifest) return {};
      const newManifest = _applyPatches(s.manifest, [patch]);
      newManifest.version = (newManifest.version || 0) + 1;
      return { manifest: newManifest, selectedBlockId: id };
    });
    submitPatches([patch]).catch((e) => console.error('addChildBlock persist failed:', e));
    return id;
  },

  // Sunum başlığını güncelle — meta.title, optimistic + persist.
  setMetaTitle: (newTitle) => {
    const state = useStore.getState();
    if (!state.manifest) return;
    const oldTitle = state.manifest.meta?.title ?? '';
    const v = (newTitle || 'Başlıksız Sunum').trim() || 'Başlıksız Sunum';
    if (v === oldTitle) return;
    set((s) => ({
      manifest: {
        ...s.manifest,
        meta: { ...(s.manifest.meta || {}), title: v },
        version: (s.manifest.version || 0) + 1,
      },
    }));
    submitPatches([{ op: 'replace', path: '/meta/title', value: v }])
      .catch((e) => console.error('setMetaTitle persist failed:', e));
  },

  // ── Carousel actions ─────────────────────────────────────────────────────

  // Carousel'e yeni boş slide ekle
  addSlideToCarousel: (carouselId, slideType) => {
    const state = useStore.getState();
    if (!state.manifest || !carouselId || !slideType) return;
    const loc = findBlockPath(state.manifest, carouselId);
    if (!loc?.child || loc.child.type !== 'carousel') return;

    const slides = loc.child.children || [];
    const id = (slideType === 'narrative' ? 't_' : 'b_') + Math.random().toString(36).slice(2, 8);
    const newSlide = _emptyBlockTemplate(id, slideType);
    if ('width' in newSlide) delete newSlide.width;  // carousel kontrolünde

    const patch = {
      op: 'add',
      path: `${loc.path}/children/${slides.length}`,
      value: newSlide,
    };
    set((s) => {
      const newManifest = _applyPatches(s.manifest, [patch]);
      newManifest.version = (newManifest.version || 0) + 1;
      return { manifest: newManifest, selectedBlockId: id };
    });
    submitPatches([patch]).catch((e) => console.error('addSlideToCarousel persist failed:', e));
    return id;
  },

  // Canvas'a yeni boş leaf blok ekle (madde 2). Carousel slide'ından farkı:
  // width KORUNUR (canvas 12-kolon grid'inde child'ın width'i kolon span'ini
  // verir, varsayılan full = tam satır). Yeni blok seçilir → Properties hemen.
  addBlockToCanvas: (canvasId, blockType) => {
    const state = useStore.getState();
    if (!state.manifest || !canvasId || !blockType) return;
    const loc = findBlockPath(state.manifest, canvasId);
    if (!loc?.child || loc.child.type !== 'canvas') return;

    const kids = loc.child.children || [];
    const prefix = blockType === 'narrative' ? 't_' : 'b_';
    const id = prefix + Math.random().toString(36).slice(2, 8);
    const newBlock = _emptyBlockTemplate(id, blockType);

    const patch = {
      op: 'add',
      path: `${loc.path}/children/${kids.length}`,
      value: newBlock,
    };
    set((s) => {
      const newManifest = _applyPatches(s.manifest, [patch]);
      newManifest.version = (newManifest.version || 0) + 1;
      return { manifest: newManifest, selectedBlockId: id };
    });
    submitPatches([patch]).catch((e) => console.error('addBlockToCanvas persist failed:', e));
    return id;
  },

  // Madde 3 — bir leaf bloğu başka bir parent'a (container carousel/canvas VEYA
  // bir section) taşı. `beforeBlockId` verilirse hedef parent'ın children'ında o
  // bloğun ÖNÜNE eklenir (sürükle-bırak ile sıra/konum) — yoksa sona eklenir
  // (menüyle "Taşı"). Hem menü hem DnD bunu kullanır.
  //
  // Uygulama: manifest'i klonla, taşınan bloğu kaldır, hedefe yerleştir; sonra
  // SADECE etkilenen üst-section'ları `replace /blocks/{si}` ile gönder. Bu,
  // RFC6902 add/remove index aritmetiğinden kaçınır ve aynı-parent reorder dahil
  // her senaryoda doğrudur (backend tüm manifest'i yine de doğruluyor).
  moveBlockBetweenParents: (blockId, targetParentId, beforeBlockId = null) => {
    const state = useStore.getState();
    if (!state.manifest || !blockId || !targetParentId || blockId === targetParentId) return;

    const m = JSON.parse(JSON.stringify(state.manifest));
    const sloc = findBlockPath(m, blockId);
    if (!sloc) return;
    // Taşınanı parent array'inden çıkar (leaf VEYA container child; section değil).
    let movedNode;
    const srcSectionIdx = sloc.sectionIdx;
    if (sloc.slideIdx != null) {
      const container = m.blocks[sloc.sectionIdx].children[sloc.childIdx];
      movedNode = container.children.splice(sloc.slideIdx, 1)[0];
      // Carousel'in son slide'ı dışarı taşındıysa carousel boş kalır — manifest
      // carousel'de ≥1 slide şart koşar → boş carousel'i çöz (section'dan kaldır).
      // Canvas boş kalabilir (Madde 2), o yüzden sadece carousel.
      if (container.type === 'carousel' && (container.children || []).length === 0) {
        m.blocks[sloc.sectionIdx].children.splice(sloc.childIdx, 1);
      }
    } else if (sloc.childIdx != null) {
      movedNode = m.blocks[sloc.sectionIdx].children.splice(sloc.childIdx, 1)[0];
    } else {
      return; // section'ın kendisini taşıma desteklenmiyor
    }
    if (!movedNode || movedNode.type === 'section_header') return;

    // Hedefi KALDIRMA SONRASI bul (aynı parent içinde index kaymış olabilir).
    const tloc = findBlockPath(m, targetParentId);
    if (!tloc) return;
    const targetNode = tloc.child ?? tloc.section;
    const targetIsContainer = CONTAINER_TYPES.has(targetNode.type);
    const targetIsSection = targetNode.type === 'section_header' && !tloc.child;
    if (!targetIsContainer && !targetIsSection) return;
    // Container içine container atılamaz (nested container yok).
    if (targetIsContainer && CONTAINER_TYPES.has(movedNode.type)) return;
    // Carousel slide'ı width taşımaz.
    if (targetNode.type === 'carousel' && 'width' in movedNode) delete movedNode.width;

    if (!Array.isArray(targetNode.children)) targetNode.children = [];
    let idx = targetNode.children.length;
    if (beforeBlockId) {
      const bi = targetNode.children.findIndex((c) => c.id === beforeBlockId);
      if (bi >= 0) idx = bi;
    }
    targetNode.children.splice(idx, 0, movedNode);

    const changedSections = [...new Set([srcSectionIdx, tloc.sectionIdx])];
    const patches = changedSections.map((si) => ({
      op: 'replace', path: `/blocks/${si}`, value: m.blocks[si],
    }));
    try {
      set({ manifest: { ...m, version: (m.version || 0) + 1 }, selectedBlockId: blockId });
    } catch (err) {
      console.error('moveBlockBetweenParents local apply failed:', err);
      return;
    }
    submitPatches(patches).catch((e) => console.error('moveBlockBetweenParents persist failed:', e));
  },

  // ── Sürükle-bırak (native HTML5 DnD) durumu + bırakma aksiyonları ──────────
  // dropPreview: { parentId, beforeId } — sürüklenen blok şu an bırakılırsa
  // NEREYE gideceği. Render katmanı bunu kullanıp o noktaya bir "hayalet" kutu
  // açar (diğer bloklar kayar → kullanıcı yeni layout'u bırakmadan görür).
  draggingBlockId: null,
  dropPreview: null,
  setDragging:   (id) => set({ draggingBlockId: id, dropPreview: null }),
  endDragging:   ()   => set({ draggingBlockId: null, dropPreview: null }),
  previewDrop: (parentId, beforeId) => set((s) => {
    const p = s.dropPreview;
    if (p && p.parentId === parentId && p.beforeId === (beforeId || null)) return {};
    return { dropPreview: { parentId, beforeId: beforeId || null } };
  }),

  // Sürüklenen bloğu `parentId` içine, `beforeId` verilirse onun ÖNÜNE (yoksa
  // sona) bırak — önizlemenin gösterdiği yere taşı.
  commitDrop: (parentId, beforeId) => {
    const s = useStore.getState();
    const draggedId = s.draggingBlockId;
    set({ draggingBlockId: null, dropPreview: null });
    if (!draggedId || !parentId || draggedId === parentId) return;
    s.moveBlockBetweenParents(draggedId, parentId, beforeId || null);
  },

  // Bloğu kendi parent array'inde yukarı/aşağı taşı (3 seviyeli generic).
  // direction: -1 (yukarı) | +1 (aşağı). Seçim korunur.
  moveBlock: (blockId, direction) => {
    const state = useStore.getState();
    if (!state.manifest || !blockId || !direction) return;
    const loc = findBlockPath(state.manifest, blockId);
    if (!loc) return;

    let arr, fromIdx, parentPath;
    if (loc.slideIdx != null) {
      arr = loc.child.children || [];
      fromIdx = loc.slideIdx;
      parentPath = `/blocks/${loc.sectionIdx}/children/${loc.childIdx}/children`;
    } else if (loc.childIdx != null) {
      arr = loc.section.children || [];
      fromIdx = loc.childIdx;
      parentPath = `/blocks/${loc.sectionIdx}/children`;
    } else {
      arr = state.manifest.blocks || [];
      fromIdx = loc.sectionIdx;
      parentPath = '/blocks';
    }

    const toIdx = fromIdx + direction;
    if (toIdx < 0 || toIdx >= arr.length) return;

    const clone = JSON.parse(JSON.stringify(arr[fromIdx]));
    const patches = [
      { op: 'remove', path: `${parentPath}/${fromIdx}` },
      { op: 'add',    path: `${parentPath}/${toIdx}`, value: clone },
    ];
    set((s) => {
      const newManifest = _applyPatches(s.manifest, patches);
      newManifest.version = (newManifest.version || 0) + 1;
      return { manifest: newManifest, selectedBlockId: blockId };
    });
    submitPatches(patches).catch((e) => console.error('moveBlock persist failed:', e));
  },

  // Slide'ı carousel içinde yukarı/aşağı taşı
  reorderSlide: (slideId, direction) => {
    // direction: -1 (yukarı) | +1 (aşağı)
    const state = useStore.getState();
    if (!state.manifest || !slideId) return;
    const loc = findBlockPath(state.manifest, slideId);
    if (!loc?.slide) return;
    const slides = loc.child.children || [];
    const from = loc.slideIdx;
    const to = from + direction;
    if (to < 0 || to >= slides.length) return;

    const slideClone = { ...slides[from] };
    const carouselPath = `/blocks/${loc.sectionIdx}/children/${loc.childIdx}`;

    const patches = [
      { op: 'remove', path: `${carouselPath}/children/${from}` },
      { op: 'add', path: `${carouselPath}/children/${to}`, value: slideClone },
    ];
    set((s) => {
      const newManifest = _applyPatches(s.manifest, patches);
      newManifest.version = (newManifest.version || 0) + 1;
      return { manifest: newManifest };
    });
    submitPatches(patches).catch((e) => console.error('reorderSlide persist failed:', e));
  },

  // Slide'ı carousel'den çıkar → bulunduğu section'ın children sonuna taşı
  removeSlideFromCarousel: (slideId) => {
    const state = useStore.getState();
    if (!state.manifest || !slideId) return;
    const loc = findBlockPath(state.manifest, slideId);
    if (!loc || loc.slideIdx == null) {
      console.warn('removeSlideFromCarousel: slide not found or not in carousel', slideId);
      return;
    }
    const slideClone = JSON.parse(JSON.stringify(loc.slide || {}));
    const carouselPath = `/blocks/${loc.sectionIdx}/children/${loc.childIdx}`;
    const sectionPath  = `/blocks/${loc.sectionIdx}`;

    // 1) carousel'den sil  2) section.children sonuna append
    const patches = [
      { op: 'remove', path: `${carouselPath}/children/${loc.slideIdx}` },
      { op: 'add', path: `${sectionPath}/children/-`, value: slideClone },
    ];

    try {
      set((s) => {
        if (!s.manifest) return {};
        const newManifest = _applyPatches(s.manifest, patches);
        newManifest.version = (newManifest.version || 0) + 1;
        return {
          manifest: newManifest,
          // Slide hâlâ var ama farklı bir yerde — selection'ı kaldır, kullanıcı
          // tekrar tıklasın. Bu beyaz-ekran riskini de ortadan kaldırır.
          selectedBlockId: null,
        };
      });
    } catch (err) {
      console.error('removeSlideFromCarousel local apply failed:', err);
      return;
    }
    submitPatches(patches).catch((e) => console.error('removeSlideFromCarousel persist failed:', e));
  },

  // Block silme — leaf, section_header (children dahil) veya carousel slide.
  deleteBlock: (blockId) => {
    const state = useStore.getState();
    if (!state.manifest || !blockId) return;
    const loc = findBlockPath(state.manifest, blockId);
    if (!loc) {
      console.warn('deleteBlock: block not found', blockId);
      return;
    }
    const patches = [{ op: 'remove', path: loc.path }];

    // Phase 6.5.c orphan filter cleanup: after removing the block, any
    // dashboard filter whose semantic_tag is no longer represented by any
    // remaining block's variables becomes "orphan" — keeping it would clutter
    // the FilterBar AND make the "+ Filtre ekle" suggestions stale (the user
    // can't re-add a tag that already has a filter). Drop them automatically.
    // We compute against a hypothetical post-delete manifest.
    try {
      const after = _applyPatches(state.manifest, patches);
      const orphanPatches = _computeOrphanFilterPatches(after);
      patches.push(...orphanPatches);
    } catch (e) {
      console.warn('deleteBlock orphan scan failed:', e);
    }

    try {
      set((s) => {
        if (!s.manifest) return {};
        const newManifest = _applyPatches(s.manifest, patches);
        newManifest.version = (newManifest.version || 0) + 1;
        const nextSelected = (s.selectedBlockId === blockId) ? null : s.selectedBlockId;
        // Drop filter_state entries for filters we just removed.
        const filterIds = new Set((newManifest.filters || []).map((f) => f.id));
        const fs = {};
        for (const [k, v] of Object.entries(s.filterState || {})) {
          if (filterIds.has(k)) fs[k] = v;
        }
        return { manifest: newManifest, selectedBlockId: nextSelected, filterState: fs };
      });
    } catch (err) {
      console.error('deleteBlock local apply failed:', err);
      return;
    }
    submitPatches(patches).catch((e) => console.error('deleteBlock persist failed:', e));
  },

  // ═══════════════════════════════════════════════════════════════════════
  // Phase 6.5.c — dashboard filters
  // ═══════════════════════════════════════════════════════════════════════

  // Local mutable filter state (form values); persisted to manifest on Save,
  // applied to all blocks on Güncelle.
  filterState: {},
  filterStatus: {},   // {blockId: 'cache_hit'|'subset'|'refetching'|'refetched'|'error', ...}
  // Detail for blocks whose apply-filters status was 'error' — drives the
  // hover tooltip on the "N hata" indicator so failures aren't silent.
  filterErrors: [],   // [{id, kind, error, title}]
  // Phase 7 concept compilation per block (from apply-filters response):
  //   {blockId: {blind: [conceptId,...], applied: [{filter_id,concept,sql}], injected: bool}}
  conceptStatus: {},
  // Phase B — library cache freshness per block (only set when apply-filters
  // served from / via the library cache):
  //   {blockId: {source, freshness, fetchedAt, ageSeconds, refreshing}}
  freshnessStatus: {},
  filterBusy: false,

  setFilterValue: (filterId, value) => {
    set((s) => ({ filterState: { ...s.filterState, [filterId]: value } }));
  },

  initFilterStateFromManifest: () => {
    const state = useStore.getState();
    const m = state.manifest;
    if (!m) return;
    // Prefer the manifest's persisted filter_state (last user save).
    // Otherwise compute from filter defaults.
    if (m.filter_state && Object.keys(m.filter_state).length > 0) {
      set({ filterState: { ...m.filter_state } });
      return;
    }
    const initial = {};
    for (const f of (m.filters || [])) {
      if (f.default != null) initial[f.id] = f.default;
    }
    set({ filterState: initial });
  },

  // Non-destructive: seed defaults ONLY for filters not already in
  // filterState. Used after a chat turn that seeded a new dashboard filter
  // (Phase 7) so the new filter's default value populates the widget +
  // flows to the backend, without clobbering the user's existing selections.
  // Returns true if anything was added.
  hydrateFilterDefaults: () => {
    const state = useStore.getState();
    const m = state.manifest;
    if (!m) return false;
    const next = { ...state.filterState };
    let changed = false;
    for (const f of (m.filters || [])) {
      if (!(f.id in next) && f.default != null) {
        next[f.id] = f.default;
        changed = true;
      }
    }
    if (changed) set({ filterState: next });
    return changed;
  },

  addDashboardFilter: (filterDef) => {
    const state = useStore.getState();
    if (!state.manifest) return;
    const existing = state.manifest.filters || [];
    // Idempotent: if a filter with the same id already exists, REPLACE it
    // instead of inserting (prevents accidental duplicates from fast clicks
    // or stale closures). Caller can still pre-check existing for UX
    // messaging, but the action itself is safe to retry.
    const existingIdx = existing.findIndex((f) => f.id === filterDef.id);
    const patches = [];
    if (state.manifest.filters === undefined) {
      patches.push({ op: 'add', path: '/filters', value: [] });
    }
    if (existingIdx >= 0) {
      patches.push({
        op: 'replace',
        path: `/filters/${existingIdx}`,
        value: filterDef,
      });
    } else {
      const idx = existing.length;
      patches.push({ op: 'add', path: `/filters/${idx}`, value: filterDef });
    }

    // ── Auto-bind matching block variables to this new filter ───────────
    // Walk every leaf block; for each variable whose semantic_tag matches
    // and which isn't already bound, write a variable_binding pointing at
    // this filter.
    const bindPatches = _computeAutoBindPatches(state.manifest, filterDef);
    patches.push(...bindPatches);

    set((s) => {
      if (!s.manifest) return {};
      const next = _applyPatches(s.manifest, patches);
      next.version = (next.version || 0) + 1;
      const fs = { ...s.filterState };
      if (filterDef.default != null) fs[filterDef.id] = filterDef.default;
      return { manifest: next, filterState: fs };
    });
    submitPatches(patches).catch((e) =>
      console.error('addDashboardFilter persist failed:', e),
    );
  },

  removeDashboardFilter: (filterId) => {
    const state = useStore.getState();
    if (!state.manifest) return;
    const filters = state.manifest.filters || [];
    const idx = filters.findIndex((f) => f.id === filterId);
    if (idx < 0) return;
    const patch = { op: 'remove', path: `/filters/${idx}` };
    set((s) => {
      if (!s.manifest) return {};
      const next = _applyPatches(s.manifest, [patch]);
      next.version = (next.version || 0) + 1;
      const fs = { ...s.filterState };
      delete fs[filterId];
      return { manifest: next, filterState: fs };
    });
    submitPatches([patch]).catch((e) =>
      console.error('removeDashboardFilter persist failed:', e),
    );
  },

  applyFilters: async () => {
    const state = useStore.getState();
    if (!state.manifest) return;
    set({ filterBusy: true, filterStatus: {}, conceptStatus: {}, filterErrors: [] });
    try {
      const result = await applyDashboardFilters(state.filterState);
      // Refresh manifest from server (server wrote block.data_source +
      // block.config + bumped version).
      // We don't have a single-shot fetch helper in the store; the simplest
      // path is to mark statuses, then trust the server's per-block data
      // mutations by re-fetching manifest. For now we rely on the version
      // bump and the existing manifest refresh on next mount, OR we walk
      // the response and merge each block in-place by id.
      const statusMap = {};
      const conceptMap = {};
      const freshnessMap = {};
      const errs = [];
      for (const blk of result.blocks || []) {
        statusMap[blk.id] = blk.status;
        if (blk.status === 'error') {
          const loc = findBlockPath(state.manifest, blk.id);
          const title = blk.title || loc?.slide?.title || loc?.child?.title || loc?.section?.title;
          errs.push({ id: blk.id, kind: blk.kind, error: blk.error, title });
        }
        // Phase 7: capture concept compilation outcome for the block badge.
        if (blk.blind_filters || blk.applied_predicates || blk.concept_injected !== undefined) {
          conceptMap[blk.id] = {
            blind: blk.blind_filters || [],
            applied: blk.applied_predicates || [],
            injected: !!blk.concept_injected,
          };
        }
        // Phase B — library cache freshness for the BlockCard badge.
        if (blk.source === 'library_cache' || blk.freshness) {
          freshnessMap[blk.id] = {
            source: blk.source || null,
            freshness: blk.freshness || null,     // 'fresh' | 'stale' | 'expired'
            fetchedAt: blk.fetched_at || null,
            ageSeconds: typeof blk.age_seconds === 'number' ? blk.age_seconds : null,
            refreshing: !!blk.library_refreshing,
          };
        }
      }
      if (errs.length) {
        // Surface the silent failures: the "N hata" chip only counts; the
        // actual message lives here (and in console for quick copy-paste).
        console.warn(
          '[apply-filters] blok hataları:\n'
          + errs.map((e) => `  • ${e.id} [${e.kind || '?'}]: ${e.error || '(mesaj yok)'}`).join('\n'),
        );
      }
      set({
        filterStatus: statusMap,
        conceptStatus: conceptMap,
        freshnessStatus: freshnessMap,
        filterErrors: errs,
      });
      // Re-fetch manifest to pick up the per-block data_source/config that
      // the server already wrote. The lightweight path: GET /manifest.
      const refreshed = await fetch(`${window.location.pathname.replace(/\/$/, '')}/manifest`);
      if (refreshed.ok) {
        const m = await refreshed.json();
        set({ manifest: m });
      }
      return result;
    } finally {
      set({ filterBusy: false });
    }
  },

  // Phase 6.5 — run a manual-SQL block's query with declared variables.
  // Returns {block, version, warnings}; throws on resolution / SQL errors.
  runBlockManualSql: async (blockId, { query, variables, variableOverrides, scanOnly } = {}) => {
    if (!blockId) throw new Error('blockId zorunlu.');
    const result = await runBlockManual(blockId, { query, variables, variableOverrides, scanOnly });
    set((s) => {
      if (!s.manifest) return {};
      return {
        manifest: {
          ...updateBlockInPlace(s.manifest, blockId, () => result.block),
          version: result.version ?? s.manifest.version,
        },
      };
    });
    return result;
  },

  refreshBlock: async (blockId, newSql) => {
    if (!blockId) throw new Error('blockId zorunlu.');
    // newSql verilirse data_source.original_sql üzerine yazılır + execute edilir.
    const result = await refreshBlockData(blockId, newSql);
    set((s) => {
      if (!s.manifest) return {};
      return {
        manifest: {
          ...updateBlockInPlace(s.manifest, blockId, () => result.block),
          version: result.version ?? s.manifest.version,
        },
      };
    });
    return result;
  },

  // Generic block field setter — optimistic local update + persist via /patch.
  // fieldPath: dot-notation relative to block, e.g. "title", "config.unit",
  // "config.curve". value null → remove the field; otherwise replace/add.
  setBlockField: (blockId, fieldPath, value) => {
    const state = useStore.getState();
    if (!state.manifest || !blockId || !fieldPath) return;
    const loc = findBlockPath(state.manifest, blockId);
    if (!loc) return;

    const segments = fieldPath.split('.').filter(Boolean);
    const target = loc.child ?? loc.section;
    // Check if the deepest field exists already (replace) or not (add).
    let existed = true;
    let cursor = target;
    for (const seg of segments) {
      if (cursor && typeof cursor === 'object' && seg in cursor) {
        cursor = cursor[seg];
      } else {
        existed = false;
        break;
      }
    }

    // Optimistic local update.
    set((s) => ({
      manifest: updateBlockInPlace(s.manifest, blockId, (b) => {
        const next = { ...b };
        // Walk and clone path, set last segment.
        let node = next;
        for (let i = 0; i < segments.length - 1; i++) {
          const seg = segments[i];
          // Clone arrays as arrays (not objects) so index paths like
          // config.series.0.kind don't turn `series` into a plain object
          // and break `.map()` in the renderer.
          node[seg] = Array.isArray(node[seg]) ? [...node[seg]] : { ...(node[seg] || {}) };
          node = node[seg];
        }
        const last = segments[segments.length - 1];
        if (value === null || value === undefined) {
          delete node[last];
        } else {
          node[last] = value;
        }
        return next;
      }),
    }));

    const ptr = `${loc.path}/${segments.join('/')}`;
    let patch;
    if (value === null || value === undefined) {
      if (!existed) return;            // nothing to remove
      patch = { op: 'remove', path: ptr };
    } else if (existed) {
      patch = { op: 'replace', path: ptr, value };
    } else {
      patch = { op: 'add', path: ptr, value };
    }
    submitPatches([patch]).catch((e) => console.error('setBlockField persist failed:', e));
  },
}));

export default useStore;