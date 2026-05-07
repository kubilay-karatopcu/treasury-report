import { create } from 'zustand';
import { applyPatches as _applyPatches } from './patch.js';
import { submitPatches } from './api.js';

const useStore = create((set) => ({
  mode:            'editor', // 'editor' | 'snapshot'
  manifest:        null,
  viewMode:        'edit',   // 'edit' | 'presentation'
  selectedBlockId: null,
  chatHistory:     [],
  loading:         false,
  flashIds:        new Set(),
  shareModal:      null,     // null | { snapshot_id, url, title, ... }

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

  // Direct user actions — optimistic local update + backend persistence via
  // POST /<pid>/patch (bypasses the LLM but goes through validate+persist).

  toggleLock: (blockId) => {
    const state = useStore.getState();
    if (!state.manifest) return;
    const idx = state.manifest.blocks.findIndex((b) => b.id === blockId);
    if (idx < 0) return;
    const newLocked = !state.manifest.blocks[idx].locked;

    // Optimistic local update
    set((s) => ({
      manifest: {
        ...s.manifest,
        blocks: s.manifest.blocks.map((b, i) =>
          i === idx ? { ...b, locked: newLocked } : b
        ),
      },
    }));

    // Persist
    submitPatches([
      { op: 'replace', path: `/blocks/${idx}/locked`, value: newLocked },
    ]).catch((e) => console.error('toggleLock persist failed:', e));
  },

  setBlockWidth: (blockId, width) => {
    const state = useStore.getState();
    if (!state.manifest) return;
    const idx = state.manifest.blocks.findIndex((b) => b.id === blockId);
    if (idx < 0) return;

    const goingToFull = width === 'full' || !width;
    const block = state.manifest.blocks[idx];
    const hadWidth = 'width' in block;

    // Optimistic local update
    set((s) => ({
      manifest: {
        ...s.manifest,
        blocks: s.manifest.blocks.map((b, i) => {
          if (i !== idx) return b;
          if (goingToFull) {
            const { width: _drop, ...rest } = b;
            return rest;
          }
          return { ...b, width };
        }),
      },
    }));

    // Persist
    let patch;
    if (goingToFull && hadWidth) {
      patch = { op: 'remove', path: `/blocks/${idx}/width` };
    } else if (goingToFull) {
      return; // already full, nothing to persist
    } else if (hadWidth) {
      patch = { op: 'replace', path: `/blocks/${idx}/width`, value: width };
    } else {
      patch = { op: 'add', path: `/blocks/${idx}/width`, value: width };
    }
    submitPatches([patch]).catch((e) => console.error('setBlockWidth persist failed:', e));
  },
}));

export default useStore;
