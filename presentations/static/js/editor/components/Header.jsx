import { useState } from 'react';
import {
  Presentation, Home, HelpCircle, Pencil, Save,
} from 'lucide-react';
import useStore from '../lib/store.js';
import HelpModal from './HelpModal.jsx';

export default function Header() {
  const manifest         = useStore((s) => s.manifest);
  const mode             = useStore((s) => s.mode);
  const viewMode         = useStore((s) => s.viewMode);
  const layoutEditMode   = useStore((s) => s.layoutEditMode);
  const toggleLayoutEdit = useStore((s) => s.toggleLayoutEdit);
  const openSaveModal    = useStore((s) => s.openSaveModal);
  const setMetaTitle     = useStore((s) => s.setMetaTitle);

  const [helpOpen, setHelpOpen]         = useState(false);
  const [titleEditing, setTitleEditing] = useState(false);
  const [titleLocal, setTitleLocal]     = useState('');

  if (!manifest) return null;
  const meta = manifest.meta || {};
  const isSnapshot     = mode === 'snapshot';
  const isPresentation = viewMode === 'presentation';
  const listUrl = window.location.pathname.replace(/\/[^/]+$/, '/');

  return (
    <>
      <header className="editor-header">
        <div className="editor-header-left">
          <a href="/home" className="header-home-link" title="Ana sayfaya dön">
            <img src="/static/prisma_logo.png" alt="PRISMA" className="header-home-logo" />
          </a>

          {titleEditing ? (
            <input
              autoFocus
              type="text"
              className="header-title-input"
              value={titleLocal}
              onChange={(e) => setTitleLocal(e.target.value)}
              onBlur={() => {
                setTitleEditing(false);
                setMetaTitle(titleLocal);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  setTitleEditing(false);
                  setMetaTitle(titleLocal);
                } else if (e.key === 'Escape') {
                  setTitleEditing(false);
                  setTitleLocal(meta.title || '');
                }
              }}
            />
          ) : (
            <span
              className={`header-breadcrumb-current${!isSnapshot ? ' is-editable' : ''}`}
              title={!isSnapshot ? `${meta.title} — düzenlemek için tıkla` : meta.title}
              onClick={() => {
                if (isSnapshot) return;
                setTitleLocal(meta.title || '');
                setTitleEditing(true);
              }}
            >
              {meta.title}
              {!isSnapshot && (
                <Pencil size={12} strokeWidth={2} className="header-title-edit-icon" />
              )}
            </span>
          )}

          {isPresentation && !isSnapshot && (
            <div className="header-mode-pill is-presentation">
              <Presentation size={11} strokeWidth={2.2} />
              <span>Sunum</span>
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

              <button
                type="button"
                className={`btn-secondary${layoutEditMode ? ' is-active' : ''}`}
                onClick={toggleLayoutEdit}
                title={layoutEditMode
                  ? 'Düzenleme modundan çık'
                  : 'Yeni bölüm/blok ekleme moduna gir'}
              >
                <Pencil size={13} strokeWidth={1.8} />
                <span>{layoutEditMode ? 'Düzenleme Açık' : 'Düzenle'}</span>
              </button>

              <a
                href={listUrl}
                className="btn-ghost"
                title="Tüm sunumlarımı gör"
              >
                <Home size={13} strokeWidth={1.8} />
                <span>Sunumlarım</span>
              </a>

              <button
                type="button"
                className="btn-secondary"
                onClick={openSaveModal}
                title="Snapshot oluştur, PDF indir veya Ekip Raporları'na yayınla"
              >
                <Save size={13} strokeWidth={1.8} />
                <span>Kaydet</span>
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