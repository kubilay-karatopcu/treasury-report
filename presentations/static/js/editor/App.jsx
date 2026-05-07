import { useEffect } from 'react';
import useStore    from './lib/store.js';
import Header      from './components/Header.jsx';
import Sidebar     from './components/Sidebar.jsx';
import BlockCard   from './components/BlockCard.jsx';
import ShareModal  from './components/ShareModal.jsx';

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

  useEffect(() => {
    setManifest(initialManifest);
    setMode(mode);
    if (mode === 'snapshot') setViewMode('presentation');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!manifest) return <div className="editor-loading">Yükleniyor…</div>;

  const isSnapshot = mode === 'snapshot';
  const sections = manifest.blocks || [];

  return (
    <div className={`editor-root mode-${viewMode}${isSnapshot ? ' is-snapshot' : ''}`}>
      <Header />
      <div className="editor-body">
        {!isSnapshot && <Sidebar />}
        <main className="blocks-canvas">
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
