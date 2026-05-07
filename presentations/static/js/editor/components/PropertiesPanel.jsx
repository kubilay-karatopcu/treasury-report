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
  narrative:      'Metin',
};

const WIDTH_OPTIONS = [
  { value: 'full', label: 'Tam' },
  { value: '2/3',  label: '2/3' },
  { value: '1/2',  label: '1/2' },
  { value: '1/3',  label: '1/3' },
];

function LockToggle({ block }) {
  const toggleLock = useStore((s) => s.toggleLock);
  if (block.type === 'section_header') return null;

  return (
    <div className="prop-row">
      <span className="prop-label">Kilit</span>
      <button
        className={`lock-toggle-btn${block.locked ? ' is-locked' : ''}`}
        onClick={() => toggleLock(block.id)}
        title={block.locked ? 'Kilidi kaldır' : 'Kilitle (LLM değiştiremez)'}
      >
        {block.locked ? '🔒 Kilitli' : '🔓 Kilitsiz'}
      </button>
    </div>
  );
}

function WidthPicker({ block }) {
  const setBlockWidth = useStore((s) => s.setBlockWidth);
  if (block.type === 'section_header') return null;

  const current = block.width || 'full';

  return (
    <div className="prop-row">
      <span className="prop-label">Genişlik</span>
      <div className="width-picker">
        {WIDTH_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            className={`width-picker-btn${current === opt.value ? ' is-active' : ''}`}
            onClick={() => setBlockWidth(block.id, opt.value)}
            title={`Genişlik: ${opt.label}`}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function PropertiesPanel() {
  const manifest         = useStore((s) => s.manifest);
  const selectedBlockId  = useStore((s) => s.selectedBlockId);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);

  if (!selectedBlockId || !manifest) {
    return (
      <div className="properties-panel properties-panel--empty">
        Bir blok seçin.
      </div>
    );
  }

  const block = manifest.blocks.find((b) => b.id === selectedBlockId);
  if (!block) return null;

  return (
    <div className="properties-panel">
      <div className="prop-header">
        <span className="prop-type-label">{TYPE_LABELS[block.type] || block.type}</span>
        <button
          className="prop-close-btn"
          onClick={() => setSelectedBlock(null)}
          aria-label="Seçimi kaldır"
        >
          ✕
        </button>
      </div>

      <div className="prop-title">{block.title}</div>

      <LockToggle block={block} />
      <WidthPicker block={block} />

      {block.source && (
        <div className="prop-row">
          <span className="prop-label">Kaynak</span>
          <span className="prop-value prop-value--mono">{block.source}</span>
        </div>
      )}

      <div className="prop-row">
        <span className="prop-label">ID</span>
        <span className="prop-value prop-value--mono">{block.id}</span>
      </div>
    </div>
  );
}
