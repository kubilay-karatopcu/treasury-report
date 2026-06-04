import { LayoutGrid } from 'lucide-react';
import useStore, { getBlockById } from '../lib/store.js';
import BlockCard from '../components/BlockCard.jsx';
import { draggableProps, dropBeforeProps, dropIntoProps, planDropRender } from '../lib/dnd.js';

/**
 * Canvas — genel container (madde 2). Çocuk leaf blokları bir 12-kolon CSS
 * grid'inde yan yana dizer; her child'ın `width`'i kolon span'ini verir
 * (full = 12 = tam satır, 2/3 = 8, 1/2 = 6, 1/3 = 4). Carousel'in tersine
 * hepsi aynı anda görünür (slide yok).
 *
 * Üst bara/başlığa tıklanırsa canvas'ın kendisi seçilir (Properties Panel'i
 * canvas'ı hedefler — blok ekle/sırala orada). Bir child'a tıklamak o child'ı
 * seçer (BlockCard kendi seçimini yönetir).
 *
 * Schema: { id, type:'canvas', title?, locked, children: [leaf, leaf, ...] }
 */
const WIDTH_SPAN = { full: 12, '2/3': 8, '1/2': 6, '1/3': 4 };

export default function Canvas({ block }) {
  const children         = block.children || [];
  const viewMode         = useStore((s) => s.viewMode);
  const selectedBlockId  = useStore((s) => s.selectedBlockId);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);
  const mode             = useStore((s) => s.mode);

  const layoutEditMode   = useStore((s) => s.layoutEditMode);
  const dropPreview      = useStore((s) => s.dropPreview);
  const draggingBlockId  = useStore((s) => s.draggingBlockId);
  const manifest         = useStore((s) => s.manifest);
  const draggedBlock     = draggingBlockId ? getBlockById(manifest, draggingBlockId) : null;

  const isCanvasSelected = selectedBlockId === block.id;
  const isEdit           = viewMode === 'edit' && mode !== 'snapshot';
  const dndEnabled       = isEdit && layoutEditMode;
  const dndOn            = dndEnabled && !!draggingBlockId;
  const items            = planDropRender(children, block.id, dropPreview, draggingBlockId, draggedBlock);

  function selectCanvas(e) {
    if (!isEdit) return;
    e.stopPropagation();
    setSelectedBlock(isCanvasSelected ? null : block.id);
  }

  return (
    <div
      className={`canvas-block${isCanvasSelected ? ' is-selected' : ''}${block.locked ? ' is-locked' : ''}`
        + (dndOn ? ' is-dnd-zone' : '')}
      data-block-id={block.id}
      {...dropIntoProps(block.id, dndEnabled)}
    >
      <div className="canvas-header">
        <button
          type="button"
          className="canvas-title-btn"
          onClick={selectCanvas}
          title={isEdit ? 'Tuval ayarları' : undefined}
          disabled={!isEdit}
        >
          <LayoutGrid size={14} strokeWidth={2} />
          <span className="canvas-title-text">{block.title || 'Tuval'}</span>
        </button>
      </div>

      {items.length === 0 ? (
        <div className="canvas-empty">
          Tuval boş. Tuval'ı seçip <strong>“+ Blok ekle”</strong> ile başlat —
          bloklar 12-kolon grid'de yan yana dizilir (genişliği Properties'ten ayarla).
        </div>
      ) : (
        <div className="canvas-grid">
          {items.map((it) => {
            if (it.kind === 'ghost') {
              return (
                <div key="__dnd_ghost__" className="dnd-ghost"
                     style={{ gridColumn: `span ${WIDTH_SPAN[it.width] || 12}` }}>
                  <span className="dnd-ghost-label">{it.title}</span>
                </div>
              );
            }
            const child = it.block;
            return (
              <div
                key={child.id}
                className={`canvas-cell${dndEnabled ? ' is-draggable' : ''}`
                  + (it.dim ? ' is-dnd-dragging' : '')
                  + (it.hidden ? ' is-dnd-hidden' : '')}
                style={{ gridColumn: `span ${WIDTH_SPAN[child.width] || 12}` }}
                {...draggableProps(child.id, dndEnabled)}
                {...dropBeforeProps(block.id, child.id, dndEnabled)}
              >
                <BlockCard block={child} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
