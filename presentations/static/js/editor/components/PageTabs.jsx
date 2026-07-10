import { Plus, Pencil, X } from 'lucide-react';
import useStore, { effectivePageId } from '../lib/store.js';

/**
 * Sayfa sekmeleri — manifest.pages varsa canvas'ın üstünde görünür.
 * Hiyerarşi: Page > Başlıklar (section'lar `page` alanıyla bağlanır;
 * alansızlar her sayfada). Edit modunda sayfa ekle / yeniden adlandır / sil.
 */
export default function PageTabs({ isEdit }) {
  const manifest     = useStore((s) => s.manifest);
  const activeRaw    = useStore((s) => s.activePageId);
  const setActive    = useStore((s) => s.setActivePage);
  const addPage      = useStore((s) => s.addPage);
  const renamePage   = useStore((s) => s.renamePage);
  const deletePage   = useStore((s) => s.deletePage);

  const pages = manifest?.pages || [];
  if (pages.length === 0 && !isEdit) return null;

  const active = effectivePageId(manifest, activeRaw);

  function onRename(pg) {
    const title = window.prompt('Sayfa adı:', pg.title);
    if (title && title.trim() && title !== pg.title) renamePage(pg.id, title.trim());
  }

  function onDelete(pg) {
    if (pages.length <= 1) {
      if (!window.confirm(`Son sayfa "${pg.title}" silinsin mi? Sekmeler kaybolur, tüm başlıklar tek sayfada görünür.`)) return;
    } else if (!window.confirm(`"${pg.title}" sayfası silinsin mi? Başlıkları diğer sayfalarda görünür olur.`)) {
      return;
    }
    deletePage(pg.id);
  }

  return (
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
                        onClick={() => onRename(pg)}>
                  <Pencil size={11} strokeWidth={2} />
                </button>
                <button type="button" className="page-tab__icon page-tab__icon--danger"
                        title="Sayfayı sil" onClick={() => onDelete(pg)}>
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
          onClick={() => {
            const title = window.prompt('Yeni sayfa adı:', 'Yeni Sayfa');
            if (title && title.trim()) addPage(title.trim());
          }}
        >
          <Plus size={13} strokeWidth={2} />
          <span>Sayfa</span>
        </button>
      )}
    </div>
  );
}
