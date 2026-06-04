import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, Layers } from 'lucide-react';
import useStore from '../lib/store.js';
import BlockCard from '../components/BlockCard.jsx';
import { dropIntoProps } from '../lib/dnd.js';

/**
 * En içteki ilk seçilebilir bloğun id'si. Slide bir leaf'se kendi id'si;
 * bir container (canvas/carousel) ise ilk çocuğa iner — madde 2'deki canvas
 * bloğuyla ileriye dönük uyumlu (bir slide birden fazla blok içerebilir → ilki).
 */
function firstSelectableId(b) {
  if (!b) return null;
  const kids = b.children;
  if (Array.isArray(kids) && kids.length) return firstSelectableId(kids[0]);
  return b.id;
}

/** id, block'un alt ağacında (kendisi hariç) bulunuyor mu? */
function subtreeContains(b, id) {
  if (!b || !id) return false;
  for (const c of (b.children || [])) {
    if (c.id === id || subtreeContains(c, id)) return true;
  }
  return false;
}

/**
 * Carousel container — birden çok bloğu kart formatında üst üste tutar.
 * Üst barda başlık + slide geçiş okları. Slide BLOK'una tıklanırsa o slide
 * seçilir; başlık barına tıklanırsa carousel'in kendisi seçilir (Properties
 * Panel'i carousel'i hedefler — slide reorder/add/remove orada yapılır).
 *
 * Schema:
 *   { id, type:'carousel', title?, locked, width?, children: [leaf, leaf, ...] }
 */
export default function Carousel({ block }) {
  const slides = block.children || [];
  const viewMode         = useStore((s) => s.viewMode);
  const selectedBlockId  = useStore((s) => s.selectedBlockId);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);
  const mode             = useStore((s) => s.mode);

  const [idx, setIdx] = useState(0);

  // Slide sayısı değiştiğinde idx out-of-range olmasın (useEffect bir sonraki
  // render'da clamp eder; bu render için inline safeIdx kullanıyoruz)
  useEffect(() => {
    if (idx >= slides.length) setIdx(Math.max(0, slides.length - 1));
  }, [slides.length, idx]);

  const layoutEditMode     = useStore((s) => s.layoutEditMode);
  const dropTargetId       = useStore((s) => s.dropTargetId);
  const draggingBlockId    = useStore((s) => s.draggingBlockId);

  const isCarouselSelected = selectedBlockId === block.id;
  const isEdit             = viewMode === 'edit' && mode !== 'snapshot';
  const dndEnabled         = isEdit && layoutEditMode;
  const dndOn              = dndEnabled && !!draggingBlockId;

  function selectCarousel(e) {
    if (!isEdit) return;
    e.stopPropagation();
    setSelectedBlock(isCarouselSelected ? null : block.id);
  }

  const total = slides.length;
  // Slide silinmiş olabilir → idx eski değerde olabilir. Bu render için clamp.
  const safeIdx = total > 0 ? Math.min(idx, total - 1) : 0;
  const activeSlide = total > 0 ? slides[safeIdx] : null;

  // Slide değişince, bu carousel'in bir slide-bloğu düzenleniyorsa (alt-blok
  // seçili) properties paneli yeni slide'ın ilk bloğuna geçsin — aksi halde
  // panel eski slide'da kalıyordu (madde 9). Yalnız carousel'in kendisi
  // seçiliyse (carousel ayarları) seçim değiştirilmez.
  function goTo(n) {
    setIdx(n);
    if (isEdit && subtreeContains(block, selectedBlockId)) {
      const slide = slides[n];
      if (slide) setSelectedBlock(firstSelectableId(slide));
    }
  }
  function prev(e) {
    e.stopPropagation();
    if (total === 0) return;
    goTo((safeIdx - 1 + total) % total);
  }
  function next(e) {
    e.stopPropagation();
    if (total === 0) return;
    goTo((safeIdx + 1) % total);
  }

  return (
    <div
      className={`carousel${isCarouselSelected ? ' is-selected' : ''}${block.locked ? ' is-locked' : ''}`
        + (dndOn ? ' is-dnd-zone' : '')
        + (dropTargetId === block.id ? ' is-dnd-over' : '')}
      data-block-id={block.id}
      {...dropIntoProps(block.id, dndEnabled)}
    >
      {/* Üst bar: başlık (tıklanır → carousel seçili olur) + slide oklar */}
      <div className="carousel-header">
        <button
          type="button"
          className="carousel-title-btn"
          onClick={selectCarousel}
          title={isEdit ? 'Carousel ayarları' : undefined}
          disabled={!isEdit}
        >
          <Layers size={14} strokeWidth={2} />
          <span className="carousel-title-text">
            {block.title || 'Carousel'}
          </span>
        </button>

        {total > 1 && (
          <div className="carousel-nav-group">
            <span className="carousel-counter">{safeIdx + 1} / {total}</span>
            <button
              type="button"
              className="carousel-nav-btn"
              onClick={prev}
              title="Önceki slide"
            >
              <ChevronLeft size={16} strokeWidth={2.2} />
            </button>
            <button
              type="button"
              className="carousel-nav-btn"
              onClick={next}
              title="Sonraki slide"
            >
              <ChevronRight size={16} strokeWidth={2.2} />
            </button>
          </div>
        )}
      </div>

      {/* İçerik: aktif slide ya da boş state */}
      {total === 0 ? (
        <div className="carousel-empty">
          Henüz slide yok. Carousel'i seçip <strong>“+ Slide ekle”</strong> ile başlat.
        </div>
      ) : (
        <div className="carousel-slide" data-slide-id={activeSlide.id}>
          <BlockCard block={activeSlide} />
        </div>
      )}

      {/* Dots göstergesi (slide tıklanır) */}
      {total > 1 && (
        <div className="carousel-dots">
          {slides.map((_, i) => (
            <button
              key={i}
              type="button"
              className={`carousel-dot${i === safeIdx ? ' is-active' : ''}`}
              onClick={(e) => { e.stopPropagation(); goTo(i); }}
              title={`Slide ${i + 1}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
