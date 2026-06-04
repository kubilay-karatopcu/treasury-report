// Native HTML5 drag-and-drop yardımcıları (Madde 3 aşama 2). Yeni paket yok.
//
// Üç tür prop seti döndürür:
//   draggableProps(blockId, enabled)        — sürüklenebilir leaf sarmalayıcısı
//   dropBeforeProps(beforeBlockId, enabled) — bir bloğun ÖNÜNE bırakma bölgesi
//   dropIntoProps(parentId, enabled)        — container/section SONUNA bırakma
//
// Hepsi store aksiyonlarını çağırır (setDragging/endDragging/setDropTarget/
// dropBeforeBlock/dropIntoParent). `enabled` (genelde layoutEditMode) false ise
// boş obje döner → element normal davranır.
import useStore from './store.js';

export function draggableProps(blockId, enabled) {
  if (!enabled) return {};
  return {
    draggable: true,
    onDragStart: (e) => {
      e.stopPropagation();
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', blockId); } catch { /* IE guard */ }
      useStore.getState().setDragging(blockId);
    },
    onDragEnd: () => useStore.getState().endDragging(),
  };
}

export function dropBeforeProps(beforeBlockId, enabled) {
  if (!enabled) return {};
  return {
    onDragOver: (e) => {
      const dragging = useStore.getState().draggingBlockId;
      if (!dragging || dragging === beforeBlockId) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = 'move';
      useStore.getState().setDropTarget(beforeBlockId);
    },
    onDrop: (e) => {
      if (!useStore.getState().draggingBlockId) return;
      e.preventDefault();
      e.stopPropagation();
      useStore.getState().dropBeforeBlock(beforeBlockId);
    },
  };
}

export function dropIntoProps(parentId, enabled) {
  if (!enabled) return {};
  return {
    onDragOver: (e) => {
      const dragging = useStore.getState().draggingBlockId;
      if (!dragging || dragging === parentId) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = 'move';
      useStore.getState().setDropTarget(parentId);
    },
    onDrop: (e) => {
      if (!useStore.getState().draggingBlockId) return;
      e.preventDefault();
      e.stopPropagation();
      useStore.getState().dropIntoParent(parentId);
    },
  };
}
