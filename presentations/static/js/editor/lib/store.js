import { create } from 'zustand';
import { applyPatches as _applyPatches } from './patch.js';
import { submitPatches } from './api.js';

// ── Helpers for nested manifest navigation ─────────────────────────────────

function findBlockPath(manifest, blockId) {
  const sections = manifest?.blocks || [];
  for (let si = 0; si < sections.length; si++) {
    const section = sections[si];
    if (section.id === blockId) {
      return { section, sectionIdx: si, child: null, childIdx: null, path: `/blocks/${si}` };
    }
    const children = section.children || [];
    for (let ci = 0; ci < children.length; ci++) {
      if (children[ci].id === blockId) {
        return {
          section, sectionIdx: si,
          child: children[ci], childIdx: ci,
          path: `/blocks/${si}/children/${ci}`,
        };
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
      return c;
    });
    return touched ? { ...section, children: newChildren } : section;
  });
  return { ...manifest, blocks: sections };
}

// ── Store ───────────────────────────────────────────────────────────────────

const useStore = create((set) => ({
  mode:            'editor',
  manifest:        null,
  viewMode:        'edit',
  selectedBlockId: null,
  chatHistory:     [],
  loading:         false,
  flashIds:        new Set(),
  shareModal:      null,

  setMode:          (mode)     => set({ mode }),
  setManifest:      (manifest) => set({ manifest }),
  setViewMode:      (mode)     => set({ viewMode: mode }),
  setSelectedBlock: (id)       => set({ selectedBlockId: id }),
  setLoading:       (loading)  => set({ loading }),
  openShareModal:   (info)     => set({ shareModal: info }),
  closeShareModal:  ()         => set({ shareModal: null }),

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
}));

export default useStore;

// Export helpers so other components can navigate the manifest.
export { findBlockPath };
