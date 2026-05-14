import { useEffect, useState } from 'react';
import useStore        from './lib/store.js';
import Header          from './components/Header.jsx';
import Sidebar         from './components/Sidebar.jsx';
import BlockCard       from './components/BlockCard.jsx';
import ShareModal      from './components/ShareModal.jsx';
import ReportTitle     from './components/ReportTitle.jsx';
import PropertiesPanel from './components/PropertiesPanel.jsx';
import TableDocsPanel  from './components/TableDocsPanel.jsx';
import ChatBox         from './components/ChatBox.jsx';
import { Sparkles, Plus, HelpCircle } from 'lucide-react';
import useResizable from './lib/useResizable.js';
import HelpModal from './components/HelpModal.jsx';

const WIDTH_SPAN = {
  'full': 12,
  '2/3':  8,
  '1/2':  6,
  '1/3':  4,
};

export default function App({ initialManifest, mode = 'editor' }) {
  const setManifest = useStore((s) => s.setManifest);
  const setMode     = useStore((s) => s.setMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const manifest    = useStore((s) => s.manifest);
  const viewMode    = useStore((s) => s.viewMode);
  const selectedBlockId  = useStore((s) => s.selectedBlockId);
  const layoutEditMode   = useStore((s) => s.layoutEditMode);
  const docsTable        = useStore((s) => s.docsTable);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);
  const closeDocsTable   = useStore((s) => s.closeDocsTable);

  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Resizable widths (persist to localStorage) — sol Sidebar sabit,
  // sadece DocsPanel ve PropertiesPanel genişletilebilir.
  const [docsW,  dragDocs]  = useResizable('docs',  460, 'right', { min: 280, max: 800 });
  const [propsW, dragProps] = useResizable('props', 340, 'left',  { min: 280, max: 600 });

  useEffect(() => {
    setManifest(initialManifest);
    setMode(mode);
    if (mode === 'snapshot') setViewMode('presentation');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!manifest) return <div className="editor-loading">Yükleniyor…</div>;

  const isSnapshot = mode === 'snapshot';
  const sections = manifest.blocks || [];
  const isEdit = viewMode === 'edit' && !isSnapshot;

  const rootClass = [
    'editor-root',
    `mode-${viewMode}`,
    isSnapshot ? 'is-snapshot' : '',
    !sidebarOpen && !isSnapshot ? 'sidebar-collapsed' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={rootClass}>
      <Header />
      <div className="editor-body">
        {!isSnapshot && <Sidebar />}
        {!isSnapshot && docsTable && <TableDocsPanel width={docsW} onResizeStart={dragDocs} />}
        <main
          className="blocks-canvas ts-scroll"
          onClick={(e) => {
            // Canvas boş alanına tıklama → seçimi kaldır + docs panelini kapat.
            // Blok ya da interactive element içine tıklanmışsa hiçbir şey yapma.
            if (e.target.closest('[data-block-id]')) return;
            if (e.target.closest('button, input, textarea, a, select, [role="button"]')) return;
            if (selectedBlockId) setSelectedBlock(null);
            if (docsTable) closeDocsTable();
          }}
        >
          <div className={`canvas-content${sections.length === 0 && !isSnapshot ? ' canvas-content--empty' : ''}`}>
            {sections.length > 0 && <ReportTitle meta={manifest.meta || {}} />}
            {isEdit && sections.length > 0 && <Hint hasSelection={!!selectedBlockId} />}
            <div className="sections-list">
              {sections.map((section) => (
                <SectionContainer
                  key={section.id}
                  section={section}
                  layoutEditMode={isEdit && layoutEditMode}
                />
              ))}
              {sections.length === 0 && (
                isSnapshot
                  ? <div className="editor-loading">Bu snapshot boş.</div>
                  : <EmptyStart />
              )}
              {isEdit && layoutEditMode && sections.length > 0 && (
                <AddSectionRow />
              )}
            </div>
          </div>
        </main>
        {isEdit && layoutEditMode && selectedBlockId && (
          <PropertiesPanel width={propsW} onResizeStart={dragProps} />
        )}
      </div>
      {!isSnapshot && <ShareModal />}
    </div>
  );
}


function SectionContainer({ section, layoutEditMode }) {
  const children = section.children || [];
  return (
    <section
      className={`section-container${section.locked ? ' is-locked' : ''}`}
      data-block-id={section.id}
    >
      <BlockCard block={section} />
      {children.length > 0 && (
        <div className="section-children-grid">
          {children.map((child) => {
            const width = child.width || 'full';
            const span = WIDTH_SPAN[width] ?? 12;
            return (
              <div
                key={child.id}
                data-block-id={child.id}
                className={`block-slot block-slot--${width.replace('/', '-')}`}
                style={{ gridColumn: `span ${span}` }}
              >
                <BlockCard block={child} />
              </div>
            );
          })}
        </div>
      )}
      {layoutEditMode && <AddChildRow sectionId={section.id} />}
    </section>
  );
}


function AddSectionRow() {
  const addSection = useStore((s) => s.addSection);
  return (
    <div className="layout-add-row layout-add-row--section">
      <button
        type="button"
        className="layout-add-btn"
        onClick={() => addSection()}
      >
        <Plus size={14} strokeWidth={2.5} />
        <span>Yeni Bölüm Ekle</span>
      </button>
    </div>
  );
}


const CHILD_BLOCK_TYPES = [
  { type: 'kpi',        label: 'KPI' },
  { type: 'bar_chart',  label: 'Çubuk' },
  { type: 'line_chart', label: 'Çizgi' },
  { type: 'area_chart', label: 'Alan' },
  { type: 'pie_chart',  label: 'Pasta' },
  { type: 'heatmap',    label: 'Isı Haritası' },
  { type: 'radial_bar', label: 'Gösterge' },
  { type: 'data_table', label: 'Tablo' },
  { type: 'narrative',  label: 'Metin' },
  { type: 'carousel',   label: '⟳ Carousel' },
];

function AddChildRow({ sectionId }) {
  const addChildBlock = useStore((s) => s.addChildBlock);
  const [open, setOpen] = useState(false);

  return (
    <div className="layout-add-row layout-add-row--child">
      <button
        type="button"
        className="layout-add-btn layout-add-btn--ghost"
        onClick={() => setOpen((v) => !v)}
      >
        <Plus size={13} strokeWidth={2.5} />
        <span>Bu bölüme blok ekle</span>
      </button>
      {open && (
        <div className="layout-type-menu" onMouseLeave={() => setOpen(false)}>
          {CHILD_BLOCK_TYPES.map((t) => (
            <button
              key={t.type}
              type="button"
              className="layout-type-menu-item"
              onClick={() => {
                setOpen(false);
                addChildBlock(sectionId, t.type);
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}


function EmptyStart() {
  const addSection        = useStore((s) => s.addSection);
  const layoutEditMode    = useStore((s) => s.layoutEditMode);
  const toggleLayoutEdit  = useStore((s) => s.toggleLayoutEdit);
  const [helpOpen, setHelpOpen] = useState(false);

  function handleManual() {
    addSection();
    if (!layoutEditMode) toggleLayoutEdit();
  }

  return (
    <>
    <div className="empty-start">
      <div className="empty-start__intro">
        <div className="empty-start__sparkle">
          <Sparkles size={28} strokeWidth={1.5} />
        </div>
        <h2 className="empty-start__title">Yeni sunuma başla</h2>
        <p className="empty-start__sub">
          Aşağıya rapor için ne istediğini yaz; sistem ilk bölümleri ve
          bloklarını senin için oluştursun.
          <button
            type="button"
            className="empty-start__help"
            onClick={() => setHelpOpen(true)}
            title="Komut yardımı — blok tipleri ve örnekler"
          >
            <HelpCircle size={15} strokeWidth={1.8} />
          </button>
        </p>
      </div>

      <div className="empty-start__chat-wrap">
        <div className="empty-start__chat">
          <ChatBox compact />
        </div>
      </div>

      <div className="empty-start__bottom">
        <div className="empty-start__divider">
          <span>veya</span>
        </div>
        <button
          type="button"
          className="empty-start__manual"
          onClick={handleManual}
        >
          <Plus size={14} strokeWidth={2.5} />
          <span>Manuel başla — boş bölümle aç</span>
        </button>
      </div>
    </div>
    <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </>
  );
}


function Hint({ hasSelection }) {
  return (
    <div className="editor-hint">
      <Sparkles size={13} strokeWidth={2} className="editor-hint-icon" />
      <div>
        {hasSelection ? (
          <span><strong>Bir blok seçili.</strong> Soldaki sohbet kutusu artık yalnızca bu bloğa etki ediyor.</span>
        ) : (
          <span><strong>Genel mod.</strong> Sohbet kutusuna yazılan komut tüm rapora etki eder. Bir bloğa tıklayarak yalnızca onu hedefleyin.</span>
        )}
      </div>
    </div>
  );
}