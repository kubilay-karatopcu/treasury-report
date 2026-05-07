import { useEffect } from 'react';
import useStore    from './lib/store.js';
import Header      from './components/Header.jsx';
import Sidebar     from './components/Sidebar.jsx';
import BlockCard   from './components/BlockCard.jsx';
import ShareModal  from './components/ShareModal.jsx';

// Width → CSS grid span (out of 12 columns).
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

  return (
    <div className={`editor-root mode-${viewMode}${isSnapshot ? ' is-snapshot' : ''}`}>
      <Header />
      <div className="editor-body">
        {!isSnapshot && <Sidebar />}
        <main className="blocks-canvas">
          <div className="blocks-grid">
            {manifest.blocks.map((block) => {
              // Section headers always span the full row regardless of width.
              const width = block.type === 'section_header'
                ? 'full'
                : (block.width || 'full');
              const span = WIDTH_SPAN[width] ?? 12;
              return (
                <div
                  key={block.id}
                  data-block-id={block.id}
                  className={`block-slot block-slot--${width.replace('/', '-')}`}
                  style={{ gridColumn: `span ${span}` }}
                >
                  <BlockCard block={block} />
                </div>
              );
            })}
            {manifest.blocks.length === 0 && (
              <div className="editor-loading" style={{ gridColumn: 'span 12' }}>
                {isSnapshot
                  ? 'Bu snapshot boş.'
                  : "Henüz blok yok. Sohbet ile blok ekleyebilirsiniz."}
              </div>
            )}
          </div>
        </main>
      </div>
      {!isSnapshot && <ShareModal />}
    </div>
  );
}
