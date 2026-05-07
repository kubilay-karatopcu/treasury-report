import { useState } from 'react';
import useStore from '../lib/store.js';
import { createSnapshot, createPresentation } from '../lib/api.js';

export default function Header() {
  const manifest        = useStore((s) => s.manifest);
  const mode            = useStore((s) => s.mode);
  const openShareModal  = useStore((s) => s.openShareModal);

  const [snapshotting, setSnapshotting] = useState(false);
  const [creating, setCreating] = useState(false);

  if (!manifest) return null;
  const { meta } = manifest;
  const isSnapshot = mode === 'snapshot';
  const listUrl = window.location.pathname.replace(/\/[^/]+$/, '/');

  async function takeSnapshot() {
    if (snapshotting) return;
    setSnapshotting(true);
    try {
      const result = await createSnapshot();
      const fullUrl = new URL(result.url, window.location.origin).href;
      openShareModal({ ...result, url: fullUrl });
    } catch (e) {
      alert(e.message);
    } finally {
      setSnapshotting(false);
    }
  }

  async function newPresentation(carryBasket) {
    if (creating) return;
    setCreating(true);
    try {
      const payload = carryBasket && manifest.basket?.length
        ? { basket: manifest.basket } : {};
      const result = await createPresentation(payload);
      window.location.href = new URL(result.url, window.location.origin).href;
    } catch (e) {
      alert(e.message);
      setCreating(false);
    }
  }

  return (
    <div className="editor-header">
      <div className="editor-header-left">
        <div className="header-title-block">
          <div className="header-eyebrow">
            {meta.eyebrow}
            {isSnapshot && <span className="header-snapshot-pill">Snapshot</span>}
          </div>
          <div className="header-title">{meta.title}</div>
        </div>
      </div>
      <div className="editor-header-right">
        {meta.date && (
          <span className="header-meta">
            {meta.date}{meta.author_label ? ` · ${meta.author_label}` : ''}
          </span>
        )}
        {!isSnapshot && (
          <>
            <a href={listUrl} className="header-nav-link" title="Tüm sunumlarımı gör">
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
                   fill="none" stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 12L5 10L12 3L19 10L21 12"/>
                <path d="M5 10v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1 -1V10"/>
                <path d="M9 21V12h6v9"/>
              </svg>
              Sunumlarım
            </a>
            <NewPresentationButton
              hasBasket={(manifest.basket?.length || 0) > 0}
              creating={creating}
              onCreate={newPresentation}
            />
            <button
              className="header-btn header-btn--secondary"
              onClick={takeSnapshot}
              disabled={snapshotting}
              title="Sunum'un anlık halini paylaşılabilir bağlantı olarak dondur"
            >
              {snapshotting ? 'Oluşturuluyor…' : 'Snapshot Al'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function NewPresentationButton({ hasBasket, creating, onCreate }) {
  const [open, setOpen] = useState(false);

  if (!hasBasket) {
    return (
      <button
        className="header-btn header-btn--primary"
        onClick={() => onCreate(false)}
        disabled={creating}
        title="Yeni boş sunum başlat"
      >
        {creating ? 'Açılıyor…' : '+ Yeni Sunu'}
      </button>
    );
  }

  return (
    <div className="new-pres-menu">
      <button
        className="header-btn header-btn--primary"
        onClick={() => setOpen((v) => !v)}
        disabled={creating}
      >
        {creating ? 'Açılıyor…' : '+ Yeni Sunu ▾'}
      </button>
      {open && (
        <div className="new-pres-dropdown" onMouseLeave={() => setOpen(false)}>
          <button
            className="new-pres-dropdown-item"
            onClick={() => { setOpen(false); onCreate(false); }}
          >
            Boş başla
          </button>
          <button
            className="new-pres-dropdown-item"
            onClick={() => { setOpen(false); onCreate(true); }}
          >
            Bu basket ile başla
          </button>
        </div>
      )}
    </div>
  );
}
