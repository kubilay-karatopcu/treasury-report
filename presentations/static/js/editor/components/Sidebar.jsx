import { useEffect, useState } from 'react';
import { Database, Hash, Sparkles, Presentation, ArrowLeft } from 'lucide-react';
import useStore from '../lib/store.js';
import Basket from './Basket.jsx';
import ChatBox from './ChatBox.jsx';

function scrollToBlock(blockId) {
  const el = document.querySelector(`[data-block-id="${CSS.escape(blockId)}"]`);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export default function Sidebar({ width, onResizeStart }) {
  const viewMode  = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);

  if (viewMode === 'presentation') {
    return <PresentationSidebar onExit={() => setViewMode('edit')}
                                 width={width} onResizeStart={onResizeStart} />;
  }
  return <EditSidebar onPresent={() => setViewMode('presentation')}
                      width={width} onResizeStart={onResizeStart} />;
}


/* ── Edit-mode sidebar ─────────────────────────────────────────────────── */

function EditSidebar({ onPresent, width, onResizeStart }) {
  const layoutEditMode = useStore((s) => s.layoutEditMode);
  const manifest       = useStore((s) => s.manifest);
  const hasBlocks      = (manifest?.blocks?.length || 0) > 0;

  return (
    <aside className="editor-sidebar" style={width ? { width } : undefined}>
      {onResizeStart && (
        <div className="resize-handle resize-handle--right"
             onMouseDown={onResizeStart}
             title="Sürükle: panel genişliğini değiştir" />
      )}
      <div className="sidebar-inner">
        <div className="sidebar-section sidebar-section--sources ts-scroll">
          <div className="sidebar-label">
            <span className="sidebar-label-icon"><Database size={12} strokeWidth={2} /></span>
            <span>Veri Kaynakları</span>
          </div>
          <Basket />
        </div>

        {/* Chat sol panelde:
            - Düzenle modunda gizli (sağ panel açıldığı için)
            - Boş sunumda gizli (chat orta canvas'ta gösteriliyor) */}
        {!layoutEditMode && hasBlocks && (
          <div className="sidebar-section sidebar-section--chat">
            <ChatBox />
          </div>
        )}

        <div className="sidebar-section sidebar-section--bottom">
          <button
            type="button"
            className="mode-cta mode-cta--present"
            onClick={onPresent}
            title="Sunum modu — düzenleme araçları gizlenir"
          >
            <Presentation size={14} strokeWidth={2} />
            <span>Sunum Formatına Geç</span>
          </button>
        </div>
      </div>
    </aside>
  );
}


/* ── Presentation-mode sidebar ─────────────────────────────────────────── */

function PresentationSidebar({ onExit, width, onResizeStart }) {
  const manifest = useStore((s) => s.manifest);
  const mode     = useStore((s) => s.mode);
  const isSnapshot = mode === 'snapshot';

  // Flatten section_header blocks at top level + inside children, in document order.
  const headers = collectHeaders(manifest?.blocks || []);
  const [activeId, setActiveId] = useState(headers[0]?.id);

  // Scroll-spy: highlight the heading whose top is just above the 25% scroll line.
  useEffect(() => {
    if (!headers.length) return;
    const main = document.querySelector('.blocks-canvas');
    if (!main) return;

    function update() {
      const threshold = window.innerHeight * 0.25;
      const els = headers
        .map((h) => document.querySelector(`[data-block-id="${CSS.escape(h.id)}"]`))
        .filter(Boolean);
      let current = headers[0]?.id;
      for (let i = 0; i < els.length; i++) {
        const rect = els[i].getBoundingClientRect();
        if (rect.top <= threshold) current = headers[i].id;
      }
      setActiveId(current);
    }

    main.addEventListener('scroll', update, { passive: true });
    update();
    return () => main.removeEventListener('scroll', update);
  }, [headers.length]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <aside className="editor-sidebar" style={width ? { width } : undefined}>
      {onResizeStart && (
        <div className="resize-handle resize-handle--right"
             onMouseDown={onResizeStart} />
      )}
      <div className="sidebar-inner">
        <div className="sidebar-section sidebar-section--toc ts-scroll">
          <div className="sidebar-label">
            <span className="sidebar-label-icon"><Hash size={12} strokeWidth={2} /></span>
            <span>İçindekiler</span>
          </div>

          {headers.length === 0 ? (
            <div className="sidebar-empty">Bölüm başlığı yok.</div>
          ) : (
            <nav className="toc-list">
              {headers.map((b, idx) => {
                const active = b.id === activeId;
                return (
                  <button
                    type="button"
                    key={b.id}
                    className={`toc-item${active ? ' is-active' : ''}`}
                    onClick={() => {
                      setActiveId(b.id);   // anlık highlight güncelle
                      scrollToBlock(b.id);
                    }}
                    title={`'${b.title}' bölümüne git`}
                  >
                    <span className="toc-item-num">{idx + 1}</span>
                    <span className="toc-item-title">{b.title}</span>
                  </button>
                );
              })}
            </nav>
          )}

          <div className="toc-helper">
            <div className="toc-helper-title">
              <Sparkles size={11} strokeWidth={2} style={{ color: 'var(--ts-primary)' }} />
              <span>{isSnapshot ? 'Dondurulmuş rapor' : 'Sunum modu'}</span>
            </div>
            {isSnapshot
              ? 'Bu rapor anlık bir kopyadır. Yan menüden başlıklara atlayabilirsiniz.'
              : 'Bloklar düzenlenemez. Veri kaynakları gizli. Yan menüden başlıklara atlayabilirsiniz.'}
          </div>
        </div>

        {!isSnapshot && (
          <div className="sidebar-section sidebar-section--bottom">
            <button
              type="button"
              className="mode-cta mode-cta--exit"
              onClick={onExit}
            >
              <ArrowLeft size={14} strokeWidth={2} />
              <span>Düzenlemeye Dön</span>
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}


function collectHeaders(blocks) {
  const out = [];
  for (const b of blocks) {
    if (b.type === 'section_header') out.push(b);
    if (Array.isArray(b.children)) {
      for (const c of b.children) {
        if (c.type === 'section_header') out.push(c);
      }
    }
  }
  return out;
}