import { useState } from 'react';
import { HelpCircle, Pencil, Save } from 'lucide-react';
import useStore from '../lib/store.js';
import HelpModal from './HelpModal.jsx';

// Phase 12.sunum-toolbar — the legacy full-width editor-header is gone.
// What remains: a tiny floating toolbar pinned to the top-left of the
// canvas (same overlay pattern as Keşif's kesif-graph-tabs pill cluster),
// holding only three controls — Help, Düzenle (layout-edit toggle),
// and Kaydet (open SaveModal). Title editing lives on the canvas itself
// via ReportTitle.jsx (always-visible gold pencil). Sunumlarım, the
// home link, the meta date and the mode pill were dropped — they
// duplicated info already present in the PRISMA topbar / sidebar.
export default function Header() {
  const manifest         = useStore((s) => s.manifest);
  const mode             = useStore((s) => s.mode);
  const layoutEditMode   = useStore((s) => s.layoutEditMode);
  const toggleLayoutEdit = useStore((s) => s.toggleLayoutEdit);
  const openSaveModal    = useStore((s) => s.openSaveModal);

  const [helpOpen, setHelpOpen] = useState(false);

  if (!manifest) return null;
  const isSnapshot = mode === 'snapshot';

  return (
    <>
      <div className="editor-toolbar" role="toolbar" aria-label="Sunum araç çubuğu">
        {!isSnapshot && (
          <>
            <button
              type="button"
              className="editor-toolbar__btn"
              onClick={() => setHelpOpen(true)}
              title="Komut yardımı — blok tipleri ve örnekler"
              aria-label="Yardım"
            >
              <HelpCircle size={14} strokeWidth={1.8} />
            </button>

            <button
              type="button"
              className={`editor-toolbar__btn editor-toolbar__btn--toggle${layoutEditMode ? ' is-active' : ''}`}
              onClick={toggleLayoutEdit}
              title={layoutEditMode
                ? 'Düzenleme modundan çık'
                : 'Yeni bölüm/blok ekleme moduna gir — SQL düzenleyebilirsin'}
            >
              <Pencil size={13} strokeWidth={1.8} />
              <span>{layoutEditMode ? 'Düzenleme Açık' : 'Düzenle'}</span>
            </button>

            <button
              type="button"
              className="editor-toolbar__btn editor-toolbar__btn--primary"
              onClick={openSaveModal}
              title="Süreç olarak yayınla, HTML indir veya Ekip Raporları'na yayınla"
            >
              <Save size={13} strokeWidth={1.8} />
              <span>Kaydet</span>
            </button>
          </>
        )}
      </div>

      <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </>
  );
}
