import { useEffect, useRef, useState } from 'react';
import { Plus, Pencil, X } from 'lucide-react';
import Modal from './Modal.jsx';
import useStore, { effectivePageId } from '../lib/store.js';

/**
 * Sayfa sekmeleri — manifest.pages varsa canvas'ın üstünde görünür.
 * Hiyerarşi: Page > Başlıklar (section'lar `page` alanıyla bağlanır;
 * alansızlar her sayfada). Edit modunda sayfa ekle / yeniden adlandır / sil —
 * ekle/adlandır site tasarımındaki Modal ile (tarayıcı prompt'u değil).
 */
export default function PageTabs({ isEdit }) {
  const manifest     = useStore((s) => s.manifest);
  const activeRaw    = useStore((s) => s.activePageId);
  const setActive    = useStore((s) => s.setActivePage);
  const addPage      = useStore((s) => s.addPage);
  const renamePage   = useStore((s) => s.renamePage);
  const deletePage   = useStore((s) => s.deletePage);

  // dialog: null | {mode:'add'} | {mode:'rename', page} | {mode:'delete', page}
  const [dialog, setDialog] = useState(null);
  const [title, setTitle] = useState('');
  const inputRef = useRef(null);

  useEffect(() => {
    if (dialog && (dialog.mode === 'add' || dialog.mode === 'rename')) {
      setTitle(dialog.mode === 'rename' ? dialog.page.title : '');
      // Modal mount sonrası odak
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [dialog]);

  const pages = manifest?.pages || [];
  if (pages.length === 0 && !isEdit) return null;

  const active = effectivePageId(manifest, activeRaw);

  function commitDialog() {
    if (!dialog) return;
    if (dialog.mode === 'add') {
      const t = title.trim();
      if (t) addPage(t);
    } else if (dialog.mode === 'rename') {
      const t = title.trim();
      if (t && t !== dialog.page.title) renamePage(dialog.page.id, t);
    } else if (dialog.mode === 'delete') {
      deletePage(dialog.page.id);
    }
    setDialog(null);
  }

  const isForm = dialog && (dialog.mode === 'add' || dialog.mode === 'rename');

  return (
    <>
      <div className="page-tabs" role="tablist">
        {pages.map((pg) => {
          const isActive = pg.id === active;
          return (
            <div key={pg.id} className={`page-tab${isActive ? ' is-active' : ''}`}>
              <button
                type="button"
                role="tab"
                aria-selected={isActive}
                className="page-tab__label"
                onClick={() => setActive(pg.id)}
              >
                {pg.title}
              </button>
              {isEdit && isActive && (
                <span className="page-tab__actions">
                  <button type="button" className="page-tab__icon" title="Yeniden adlandır"
                          onClick={() => setDialog({ mode: 'rename', page: pg })}>
                    <Pencil size={11} strokeWidth={2} />
                  </button>
                  <button type="button" className="page-tab__icon page-tab__icon--danger"
                          title="Sayfayı sil" onClick={() => setDialog({ mode: 'delete', page: pg })}>
                    <X size={12} strokeWidth={2.2} />
                  </button>
                </span>
              )}
            </div>
          );
        })}
        {isEdit && (
          <button
            type="button"
            className="page-tab page-tab--add"
            title="Yeni sayfa ekle"
            onClick={() => setDialog({ mode: 'add' })}
          >
            <Plus size={13} strokeWidth={2} />
            <span>Sayfa</span>
          </button>
        )}
      </div>

      <Modal
        open={!!dialog}
        onClose={() => setDialog(null)}
        title={dialog?.mode === 'add' ? 'Yeni Sayfa'
          : dialog?.mode === 'rename' ? 'Sayfayı Yeniden Adlandır'
          : 'Sayfayı Sil'}
        size="sm"
        footer={(
          <>
            <button type="button" className="btn-secondary" onClick={() => setDialog(null)}>
              Vazgeç
            </button>
            <button
              type="button"
              className={dialog?.mode === 'delete' ? 'btn-danger' : 'btn-primary'}
              onClick={commitDialog}
              disabled={isForm && !title.trim()}
            >
              {dialog?.mode === 'add' ? 'Ekle'
                : dialog?.mode === 'rename' ? 'Kaydet' : 'Sil'}
            </button>
          </>
        )}
      >
        {isForm ? (
          <div className="page-modal__form">
            <label className="page-modal__label" htmlFor="page-title-input">Sayfa adı</label>
            <input
              id="page-title-input"
              ref={inputRef}
              className="page-modal__input"
              type="text"
              value={title}
              maxLength={60}
              placeholder="örn. Monthly Averages"
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); commitDialog(); }
              }}
            />
          </div>
        ) : dialog?.mode === 'delete' ? (
          <div className="page-modal__form">
            <span>
              <strong>{dialog.page.title}</strong> sayfası silinsin mi?
              {pages.length <= 1
                ? ' Son sayfa — sekmeler kaybolur, tüm başlıklar tek sayfada görünür.'
                : ' Bu sayfaya bağlı başlıklar silinmez; her sayfada görünür olurlar.'}
            </span>
          </div>
        ) : null}
      </Modal>
    </>
  );
}
