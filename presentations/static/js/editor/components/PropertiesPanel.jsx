import useStore from '../lib/store.js';

const TYPE_LABELS = {
  section_header: 'Bölüm Başlığı',
  kpi:            'KPI',
  bar_chart:      'Çubuk Grafik',
  line_chart:     'Çizgi Grafik',
  area_chart:     'Alan Grafiği',
  pie_chart:      'Pasta Grafik',
  heatmap:        'Isı Haritası',
  radial_bar:     'Radyal Gösterge',
  data_table:     'Tablo',
  narrative:      'Metin',
};

const WIDTH_OPTIONS = [
  { value: 'full', label: 'Tam' },
  { value: '2/3',  label: '2/3' },
  { value: '1/2',  label: '1/2' },
  { value: '1/3',  label: '1/3' },
];


// Find a block by id, searching top-level + children.
function findBlock(blocks, id) {
  if (!id || !Array.isArray(blocks)) return null;
  for (const b of blocks) {
    if (b.id === id) return b;
    if (Array.isArray(b.children)) {
      for (const c of b.children) {
        if (c.id === id) return c;
      }
    }
  }
  return null;
}


export default function PropertiesPanel() {
  const manifest        = useStore((s) => s.manifest);
  const selectedBlockId = useStore((s) => s.selectedBlockId);
  const toggleLock      = useStore((s) => s.toggleLock);
  const setBlockWidth   = useStore((s) => s.setBlockWidth);

  const block = findBlock(manifest?.blocks, selectedBlockId);
  if (!block) return null;

  const isSectionHeader = block.type === 'section_header';

  return (
    <div className="props-panel">
      <div className="props-row">
        <span className="props-label">Tip</span>
        <span className="props-type">{TYPE_LABELS[block.type] || block.type}</span>
      </div>

      {!isSectionHeader && (
        <div className="props-row">
          <span className="props-label">Genişlik</span>
          <div className="width-picker">
            {WIDTH_OPTIONS.map((opt) => {
              const active = (block.width || 'full') === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  className={`width-picker-btn${active ? ' is-active' : ''}`}
                  onClick={() => setBlockWidth(block.id, opt.value)}
                  title={`Genişlik: ${opt.label}`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {!isSectionHeader && (
        <div className="props-row">
          <span className="props-label">Kilit</span>
          <button
            type="button"
            className={`lock-toggle-btn${block.locked ? ' is-locked' : ''}`}
            onClick={() => toggleLock(block.id)}
            title={block.locked ? 'Kilidi kaldır' : 'Kilitle (LLM değiştiremez)'}
          >
            {block.locked ? '🔒 Kilitli' : '🔓 Kilitsiz'}
          </button>
        </div>
      )}
    </div>
  );
}
