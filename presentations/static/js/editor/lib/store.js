import { create } from 'zustand';
import { applyPatches as _applyPatches } from './patch.js';
import { submitPatches, refreshBlockData, runBlockManual } from './api.js';

// ── Helpers for nested manifest navigation ─────────────────────────────────

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
      // Carousel slides — 3. seviye
      if (child.type === 'carousel') {
        const slides = child.children || [];
        for (let ki = 0; ki < slides.length; ki++) {
          if (slides[ki].id === blockId) {
            return {
              section, sectionIdx: si,
              child, childIdx: ci,
              slide: slides[ki], slideIdx: ki,
              path: `/blocks/${si}/children/${ci}/children/${ki}`,
            };
          }
        }
      }
    }
  }
  return null;
}

function updateBlockInPlace(manifest, blockId, fn) {
  const sections = manifest.blocks.map((section) => {
    if (section.id === blockId) return fn(section);
    const children = section.children || [];
    let touched = false;
    const newChildren = children.map((c) => {
      if (c.id === blockId) { touched = true; return fn(c); }
      // Carousel slides — 3. seviye nesting
      if (c.type === 'carousel' && Array.isArray(c.children)) {
        let slideTouched = false;
        const newSlides = c.children.map((slide) => {
          if (slide.id === blockId) { slideTouched = true; return fn(slide); }
          return slide;
        });
        if (slideTouched) {
          touched = true;
          return { ...c, children: newSlides };
        }
      }
      return c;
    });
    return touched ? { ...section, children: newChildren } : section;
  });
  return { ...manifest, blocks: sections };
}

// ── Empty block templates (used when user manually adds blocks) ────────────

function _emptyBlockTemplate(id, type, opts) {
  // opts.manual_sql=true → seed Phase 6.5 manual-authoring fields so the
  // ManualSqlEditor takes over the Properties panel for this block.
  const manual = !!(opts && opts.manual_sql);
  const base = { id, type, title: _defaultTitle(type), locked: false };
  if (manual) {
    base.manual_sql = true;
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
    default:
      return { ...base, config: {} };
  }
}

function _defaultTitle(type) {
  const map = {
    kpi:         'Yeni KPI',
    bar_chart:   'Yeni Çubuk Grafik',
    line_chart:  'Yeni Çizgi Grafik',
    area_chart:  'Yeni Alan Grafiği',
    pie_chart:   'Yeni Pasta Grafik',
    heatmap:     'Yeni Isı Haritası',
    radial_bar:  'Yeni Gösterge',
    data_table:  'Yeni Tablo',
    narrative:   'Yeni Metin',
    carousel:    'Yeni Carousel',
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
  setManifest:      (manifest) => set({
    manifest,
    // Hydrate chat history from manifest on initial load / page refresh.
    chatHistory: Array.isArray(manifest?.chat_history) ? manifest.chat_history : [],
  }),
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

  // Library bloğunu klonlayıp section.children sonuna ekle. ID yeniden üretilir,
  // runtime alanları (rows/view_name) zaten library kaydında temizlenmişti.
  addLibraryBlockToSection: (sectionId, libraryBlock) => {
    const state = useStore.getState();
    if (!state.manifest || !sectionId || !libraryBlock) return;
    const loc = findBlockPath(state.manifest, sectionId);
    if (!loc || loc.child) return;
    const section = loc.section;
    const childIdx = (section.children || []).length;

    // Deep clone + new ID
    const cloned = JSON.parse(JSON.stringify(libraryBlock));
    const prefix = cloned.type === 'narrative' ? 't_' : 'b_';
    cloned.id = prefix + Math.random().toString(36).slice(2, 8);
    if (cloned.locked) cloned.locked = false;

    const patch = {
      op: 'add',
      path: `${loc.path}/children/${childIdx}`,
      value: cloned,
    };
    set((s) => {
      if (!s.manifest) return {};
      const newManifest = _applyPatches(s.manifest, [patch]);
      newManifest.version = (newManifest.version || 0) + 1;
      return { manifest: newManifest, selectedBlockId: cloned.id };
    });
    submitPatches([patch]).catch((e) => console.error('addLibraryBlock persist failed:', e));
    return cloned.id;
  },

  addChildBlock: (sectionId, blockType, opts) => {
    const state = useStore.getState();
    if (!state.manifest || !sectionId || !blockType) return;
    const loc = findBlockPath(state.manifest, sectionId);
    if (!loc || loc.child) return;  // sadece section'a child ekleniyor
    const section = loc.section;
    const childIdx = (section.children || []).length;

    const prefix = blockType === 'narrative' ? 't_' : 'b_';
    const id = prefix + Math.random().toString(36).slice(2, 8);
    const newBlock = _emptyBlockTemplate(id, blockType, opts);

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
    const patch = { op: 'remove', path: loc.path };
    try {
      set((s) => {
        if (!s.manifest) return {};
        const newManifest = _applyPatches(s.manifest, [patch]);
        newManifest.version = (newManifest.version || 0) + 1;
        // Silinen blok seçiliyse selection'ı temizle.
        // Slide silindiğinde carousel seçili olsa bile selection'ı koruyoruz
        // (CarouselActions slide listesinde silinmiş slide olmadan re-render olur).
        const nextSelected = (s.selectedBlockId === blockId) ? null : s.selectedBlockId;
        return { manifest: newManifest, selectedBlockId: nextSelected };
      });
    } catch (err) {
      console.error('deleteBlock local apply failed:', err);
      // Manifest'i değiştirme — backend'e de gönderme
      return;
    }
    submitPatches([patch]).catch((e) => console.error('deleteBlock persist failed:', e));
  },

  // Phase 6.5 — run a manual-SQL block's query with declared variables.
  // Returns {block, version, warnings}; throws on resolution / SQL errors.
  runBlockManualSql: async (blockId, { query, variables, variableOverrides } = {}) => {
    if (!blockId) throw new Error('blockId zorunlu.');
    const result = await runBlockManual(blockId, { query, variables, variableOverrides });
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
          node[seg] = { ...(node[seg] || {}) };
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