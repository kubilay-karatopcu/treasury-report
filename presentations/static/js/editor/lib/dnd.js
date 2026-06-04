// Native HTML5 drag-and-drop yardımcıları (Madde 3 aşama 2). Yeni paket yok.
//
// Prop fabrikaları (hepsi store aksiyonlarını çağırır; `enabled` false → no-op):
//   draggableProps(blockId, enabled)                — sürüklenebilir leaf sarmalayıcı
//   dropBeforeProps(parentId, beforeBlockId, enabled) — bir bloğun ÖNÜNE bırakma
//   dropIntoProps(parentId, enabled)                — container/section SONUNA bırakma
//
// + planDropRender(...) — sürüklerken render edilecek listeyi (bloklar + hayalet
//   kutu) hesaplar; canlı "yeni layout" önizlemesini mümkün kılar.
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

export function dropBeforeProps(parentId, beforeBlockId, enabled) {
  if (!enabled) return {};
  return {
    onDragOver: (e) => {
      const dragging = useStore.getState().draggingBlockId;
      if (!dragging || dragging === beforeBlockId) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = 'move';
      useStore.getState().previewDrop(parentId, beforeBlockId);
    },
    onDrop: (e) => {
      if (!useStore.getState().draggingBlockId) return;
      e.preventDefault();
      e.stopPropagation();
      useStore.getState().commitDrop(parentId, beforeBlockId);
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
      useStore.getState().previewDrop(parentId, null);
    },
    onDrop: (e) => {
      if (!useStore.getState().draggingBlockId) return;
      e.preventDefault();
      e.stopPropagation();
      useStore.getState().commitDrop(parentId, null);
    },
  };
}

// Hayalet kutunun kendi drop prop'ları — KRİTİK: hayalet üstüne gelince mevcut
// önizlemeyi KORUR (previewDrop ÇAĞIRMAZ) ve event'i durdurur. Yoksa hayaletin
// altındaki section'ın "sona ekle" handler'ı devreye girer → hayalet sona kayar →
// imleç eski slot'a döner → önizleme geri döner → sonsuz titreme. stopPropagation
// + preventDefault hayaleti pasif (önizlemeyi sabitleyen) bir drop bölgesi yapar.
export function ghostProps() {
  return {
    onDragOver: (e) => {
      if (!useStore.getState().draggingBlockId) return;
      e.preventDefault();
      e.stopPropagation();   // mevcut dropPreview'i değiştirme → sabit kalır
    },
    onDrop: (e) => {
      const st = useStore.getState();
      if (!st.draggingBlockId) return;
      e.preventDefault();
      e.stopPropagation();
      const dp = st.dropPreview;
      if (dp) st.commitDrop(dp.parentId, dp.beforeId);
    },
  };
}

/**
 * Sürüklerken bir parent'ın (section/canvas) çocuk listesinin nasıl render
 * edileceğini planla. Dönen `items` her elemanı şu şekildedir:
 *   { kind:'block', block, dim? }   — normal blok (dim: origin'de soluk)
 *   { kind:'ghost', width, title }  — hayalet kutu (bırakılırsa buraya gelir)
 *
 * Mantık:
 *  - Sürükleme yokken: bloklar olduğu gibi.
 *  - Bu parent önizleme hedefiyse: sürüklenen blok GİZLENİR (display:none ile —
 *    DOM'da kalır ki tarayıcı dragend'i ateşlesin) ve hedef noktaya bir hayalet
 *    eklenir → tam sonuç layout'u (diğer bloklar kayar).
 *  - Bu parent sürüklenen bloğun ORIGIN'i ama hedef DEĞİLse: blok soluk gösterilir.
 */
export function planDropRender(children, parentId, dropPreview, draggingBlockId, draggedBlock) {
  if (!draggingBlockId) {
    return children.map((b) => ({ kind: 'block', block: b }));
  }
  const isPreviewParent = dropPreview && dropPreview.parentId === parentId;
  if (!isPreviewParent) {
    return children.map((b) => ({ kind: 'block', block: b, dim: b.id === draggingBlockId }));
  }
  const ghost = {
    kind: 'ghost',
    width: draggedBlock?.width || 'full',
    title: draggedBlock?.title || 'Blok',
  };
  const items = [];
  let inserted = false;
  for (const b of children) {
    if (dropPreview.beforeId && b.id === dropPreview.beforeId && !inserted) {
      items.push(ghost);
      inserted = true;
    }
    // Sürüklenen blok gizlenir (layout'tan çıkar ama mount'ta kalır).
    items.push({ kind: 'block', block: b, hidden: b.id === draggingBlockId });
  }
  if (!inserted) items.push(ghost);   // sona ekleme (beforeId yok / bulunamadı)
  return items;
}
