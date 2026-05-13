import { useEffect, useState } from 'react';
import useStore     from './lib/store.js';
import Header       from './components/Header.jsx';
import Sidebar      from './components/Sidebar.jsx';
import BlockCard    from './components/BlockCard.jsx';
import ShareModal   from './components/ShareModal.jsx';
import ReportTitle  from './components/ReportTitle.jsx';
import { Sparkles } from 'lucide-react';

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
  const selectedBlockId = useStore((s) => s.selectedBlockId);

  const [sidebarOpen, setSidebarOpen] = useState(true);

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
      <Header
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((o) => !o)}
      />
      <div className="editor-body">
        {!isSnapshot && <Sidebar />}
        <main className="blocks-canvas ts-scroll">
          <div className="canvas-content">
            <ReportTitle meta={manifest.meta || {}} />
            {isEdit && <Hint hasSelection={!!selectedBlockId} />}
            <div className="sections-list">
              {sections.map((section) => (
                <SectionContainer key={section.id} section={section} />
              ))}
              {sections.length === 0 && (
                <div className="editor-loading">
                  {isSnapshot
                    ? 'Bu snapshot boş.'
                    : 'Henüz bölüm yok. Sohbet ile başlık ekleyebilirsiniz.'}
                </div>
              )}
            </div>
          </div>
        </main>
      </div>
      {!isSnapshot && <ShareModal />}
    </div>
  );
}


function SectionContainer({ section }) {
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
    </section>
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
