import { useState, useEffect } from 'react';
import useStore from '../lib/store.js';
import PropertiesPanel from './PropertiesPanel.jsx';
import Basket from './Basket.jsx';
import ChatBox from './ChatBox.jsx';

function scrollToBlock(blockId) {
  const el = document.querySelector(`[data-block-id="${CSS.escape(blockId)}"]`);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export default function Sidebar() {
  const manifest        = useStore((s) => s.manifest);
  const viewMode        = useStore((s) => s.viewMode);
  const setViewMode     = useStore((s) => s.setViewMode);
  const selectedBlockId = useStore((s) => s.selectedBlockId);

  const [topTab, setTopTab] = useState('properties');

  useEffect(() => {
    if (selectedBlockId && topTab !== 'properties') setTopTab('properties');
  }, [selectedBlockId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (viewMode === 'presentation') {
    const headers = (manifest?.blocks || []).filter((b) => b.type === 'section_header');
    return (
      <aside className="editor-sidebar">
        <div className="sidebar-section sidebar-section--toc">
          <div className="sidebar-label">İçindekiler</div>
          {headers.length === 0
            ? <div className="sidebar-empty">Bölüm başlığı yok.</div>
            : (
              <div className="toc-list">
                {headers.map((b, i) => (
                  <button
                    key={b.id}
                    className="toc-item"
                    onClick={() => scrollToBlock(b.id)}
                    title={`'${b.title}' bölümüne git`}
                  >
                    <span className="toc-item-num">{i + 1}</span>
                    <span className="toc-item-title">{b.title}</span>
                  </button>
                ))}
              </div>
            )
          }
        </div>

        <div className="sidebar-section sidebar-section--bottom">
          <button
            className="mode-toggle-btn mode-toggle-btn--exit"
            onClick={() => setViewMode('edit')}
          >
            ← Düzenleme Modu
          </button>
        </div>
      </aside>
    );
  }

  // Edit mode
  return (
    <aside className="editor-sidebar">
      <div className="sidebar-tabs">
        <button
          className={`sidebar-tab${topTab === 'properties' ? ' is-active' : ''}`}
          onClick={() => setTopTab('properties')}
        >
          Özellikler
        </button>
        <button
          className={`sidebar-tab${topTab === 'basket' ? ' is-active' : ''}`}
          onClick={() => setTopTab('basket')}
        >
          Veri
          {(manifest?.basket?.length || 0) > 0 && (
            <span className="sidebar-tab-badge">{manifest.basket.length}</span>
          )}
        </button>
      </div>

      <div className="sidebar-section sidebar-section--top">
        {topTab === 'properties' && (
          selectedBlockId
            ? <PropertiesPanel />
            : <div className="sidebar-empty">Düzenlemek için bir bloka tıklayın.</div>
        )}
        {topTab === 'basket' && <Basket />}
      </div>

      <div className="sidebar-section sidebar-section--chat">
        <ChatBox />
      </div>

      <div className="sidebar-section sidebar-section--bottom">
        <button
          className="mode-toggle-btn mode-toggle-btn--present"
          onClick={() => setViewMode('presentation')}
          title="Sunum modu — düzenleme araçları gizlenir, sadece içerik gösterilir"
        >
          ▶ Sunu Modu
        </button>
      </div>
    </aside>
  );
}
