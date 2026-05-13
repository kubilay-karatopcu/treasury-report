import { useState } from 'react';
import {
  Menu, ChevronRight, Presentation, FileText, ExternalLink, Home, HelpCircle,
} from 'lucide-react';
import useStore from '../lib/store.js';
import { createSnapshot, createPresentation } from '../lib/api.js';
import HelpModal from './HelpModal.jsx';

export default function Header({ sidebarOpen, onToggleSidebar }) {
  const manifest        = useStore((s) => s.manifest);
  const mode            = useStore((s) => s.mode);
  const viewMode        = useStore((s) => s.viewMode);
  const openShareModal  = useStore((s) => s.openShareModal);

  const [snapshotting, setSnapshotting] = useState(false);
  const [creating, setCreating]         = useState(false);
  const [helpOpen, setHelpOpen]         = useState(false);

  if (!manifest) return null;
  const meta = manifest.meta || {};
  const isSnapshot     = mode === 'snapshot';
  const isPresentation = viewMode === 'presentation';
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
    <>
      <header className="editor-header">
        <div className="editor-header-left">
          {!isSnapshot && (
            <button
              type="button"
              className={`header-sidebar-toggle${sidebarOpen ? ' is-active' : ''}`}
              onClick={onToggleSidebar}
              title={sidebarOpen ? 'Paneli kapat' : 'Paneli aç'}
            >
              <Menu size={16} strokeWidth={2} />
            </button>
          )}

          <div className="header-logo">T</div>

          <div className="header-breadcrumb">
            <span>Hazine Platformu</span>
            <ChevronRight size={12} />
            <span>{isSnapshot ? 'Snapshot' : 'Sunumlar'}</span>
            <ChevronRight size={12} />
            <span className="header-breadcrumb-current" title={meta.title}>
              {meta.title}
            </span>
          </div>

          {!isSnapshot && (
            <div className={`header-mode-pill${isPresentation ? ' is-presentation' : ''}`}>
              {isPresentation
                ? <Presentation size={11} strokeWidth={2.2} />
                : <FileText size={11} strokeWidth={2.2} />}
              <span>{isPresentation ? 'Sunum' : 'Düzenle'}</span>
            </div>
          )}

          {isSnapshot && (
            <span className="header-snapshot-pill">Snapshot</span>
          )}
        </div>

        <div className="editor-header-right">
          {meta.date && (
            <span className="header-meta">
              {meta.date}
              {meta.author_label ? ` · ${meta.author_label}` : ''}
            </span>
          )}

          {!isSnapshot && (
            <>
              <button
                type="button"
                className="header-sidebar-toggle"
                onClick={() => setHelpOpen(true)}
                title="Komut yardımı — blok tipleri ve örnekler"
                style={{ marginRight: 0 }}
              >
                <HelpCircle size={16} strokeWidth={1.8} />
              </button>

              <a
                href={listUrl}
                className="btn-ghost"
                title="Tüm sunumlarımı gör"
              >
                <Home size={13} strokeWidth={1.8} />
                <span>Sunumlarım</span>
              </a>
              <NewPresentationButton
                hasBasket={(manifest.basket?.length || 0) > 0}
                creating={creating}
                onCreate={newPresentation}
              />
              <button
                type="button"
                className="btn-secondary"
                onClick={takeSnapshot}
                disabled={snapshotting}
                title="Sunum'un anlık halini paylaşılabilir bağlantı olarak dondur"
              >
                <ExternalLink size={13} strokeWidth={1.8} />
                <span>{snapshotting ? 'Oluşturuluyor…' : 'Snapshot Al'}</span>
              </button>
            </>
          )}
        </div>
      </header>

      <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </>
  );
}


function NewPresentationButton({ hasBasket, creating, onCreate }) {
  const [open, setOpen] = useState(false);

  if (!hasBasket) {
    return (
      <button
        type="button"
        className="btn-primary"
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
        type="button"
        className="btn-primary"
        onClick={() => setOpen((v) => !v)}
        disabled={creating}
      >
        {creating ? 'Açılıyor…' : '+ Yeni Sunu ▾'}
      </button>
      {open && (
        <div className="new-pres-dropdown" onMouseLeave={() => setOpen(false)}>
          <button
            type="button"
            className="new-pres-dropdown-item"
            onClick={() => { setOpen(false); onCreate(false); }}
          >
            Boş başla
          </button>
          <button
            type="button"
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
