import { useEffect, useState } from 'react';
import { X, RefreshCw, Lock, Unlock, Trash2, Layers, Plus, Save, MoveRight } from 'lucide-react';
import useStore, { CONTAINER_TYPES, comboSeriesDefaults } from '../lib/store.js';
import ManualSqlEditor from './ManualSqlEditor.jsx';

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
  waterfall_chart: 'Waterfall (Köprü)',
  scatter_chart:  'Bubble / Scatter',
  narrative:      'Metin',
  carousel:       'Carousel',
  canvas:         'Tuval',
};

const SLIDE_TYPES = [
  { type: 'kpi',        label: 'KPI' },
  { type: 'bar_chart',  label: 'Çubuk' },
  { type: 'line_chart', label: 'Çizgi' },
  { type: 'combo_chart', label: 'Combo' },
  { type: 'area_chart', label: 'Alan' },
  { type: 'pie_chart',  label: 'Pasta' },
  { type: 'heatmap',    label: 'Isı' },
  { type: 'radial_bar', label: 'Gösterge' },
  { type: 'data_table', label: 'Tablo' },
  { type: 'waterfall_chart', label: 'Waterfall' },
  { type: 'scatter_chart',   label: 'Bubble' },
  { type: 'narrative',  label: 'Metin' },
];

const WIDTH_OPTIONS = [
  { value: 'full', label: 'Tam' },
  { value: '2/3',  label: '2/3' },
  { value: '1/2',  label: '1/2' },
  { value: '1/3',  label: '1/3' },
];

const DATA_BOUND_TYPES = new Set([
  'kpi', 'bar_chart', 'line_chart', 'combo_chart', 'area_chart',
  'pie_chart', 'heatmap', 'radial_bar', 'data_table',
  'waterfall_chart', 'scatter_chart',
]);


function findBlock(blocks, id) {
  if (!id || !Array.isArray(blocks)) return null;
  for (const b of blocks) {
    if (b.id === id) return b;
    if (Array.isArray(b.children)) {
      const hit = findBlock(b.children, id);   // recursive — herhangi derinlik
      if (hit) return hit;
    }
  }
  return null;
}

// Bir bloğun direkt parent'ının tipini bul (carousel/canvas/section_header) —
// "çıkar" butonlarının görünürlüğü için. Herhangi derinlik. (id top-level ise
// veya bulunamazsa null.)
function parentTypeOf(blocks, id, parentType = null) {
  if (!Array.isArray(blocks)) return null;
  for (const b of blocks) {
    if (b.id === id) return parentType;
    if (Array.isArray(b.children)) {
      const hit = parentTypeOf(b.children, id, b.type);
      if (hit !== null) return hit;
    }
  }
  return null;
}

// Bir bloğun direkt parent'ının id'sini bul (herhangi derinlik). Top-level ise
// veya bulunamazsa null. DnD/menü hedef listesinde mevcut parent'ı dışlamak için.
function parentIdOf(blocks, id, parentBlockId = null) {
  if (!Array.isArray(blocks)) return null;
  for (const b of blocks) {
    if (b.id === id) return parentBlockId;
    if (Array.isArray(b.children)) {
      const found = parentIdOf(b.children, id, b.id);
      if (found !== null) return found;
    }
  }
  return null;
}


export default function PropertiesPanel({ width, onResizeStart }) {
  const manifest        = useStore((s) => s.manifest);
  const selectedBlockId = useStore((s) => s.selectedBlockId);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);
  const viewMode        = useStore((s) => s.viewMode);

  // ESC ile panel kapansın
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') setSelectedBlock(null);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setSelectedBlock]);

  const block = findBlock(manifest?.blocks, selectedBlockId);
  if (!block || viewMode !== 'edit') return null;

  const isSection = block.type === 'section_header';
  const isDataBound = DATA_BOUND_TYPES.has(block.type);

  return (
    <aside className="props-side-panel" style={width ? { width } : undefined}>
      {onResizeStart && (
        <div className="resize-handle resize-handle--left"
             onMouseDown={onResizeStart} />
      )}
      <header className="props-side-panel__header">
        <div>
          <div className="props-side-panel__type">{TYPE_LABELS[block.type] || block.type}</div>
          <div className="props-side-panel__id" title={block.id}>{block.id}</div>
        </div>
        <button
          type="button"
          className="props-close-btn"
          onClick={() => setSelectedBlock(null)}
          title="Kapat (ESC)"
        >
          <X size={16} strokeWidth={2} />
        </button>
      </header>

      <div className="props-side-panel__body ts-scroll">
        <Section title="Genel">
          <TitleField block={block} />
          {isDataBound && <TypeField block={block} />}
          {!isSection && <WidthField block={block} />}
          {!isSection && <LockField block={block} />}
        </Section>

        {block.type === 'section_header' && <SectionHeaderControls block={block} />}
        {block.type === 'kpi'        && <KpiControls block={block} />}
        {block.type === 'bar_chart'  && <BarChartControls block={block} />}
        {block.type === 'line_chart' && <LineChartControls block={block} type="line_chart" />}
        {block.type === 'combo_chart' && <ComboChartControls block={block} />}
        {block.type === 'area_chart' && <LineChartControls block={block} type="area_chart" />}
        {block.type === 'pie_chart'  && <PieChartControls block={block} />}
        {block.type === 'heatmap'    && <HeatmapControls block={block} />}
        {block.type === 'radial_bar' && <RadialBarControls block={block} />}
        {block.type === 'narrative'  && <NarrativeControls block={block} />}
        {REF_LINE_TYPES.has(block.type) && <RefLinesControls block={block} />}

        {isDataBound && <ManualSqlEditor block={block} />}

        <MoveBlockSection block={block} />
        <CarouselActions block={block} />
        <CanvasActions block={block} />

        {!isSection && <SaveToLibrarySection block={block} />}

        <DangerZone block={block} />
      </div>
    </aside>
  );
}

function CarouselActions({ block }) {
  const manifest                = useStore((s) => s.manifest);
  const addSlideToCarousel      = useStore((s) => s.addSlideToCarousel);
  const reorderSlide            = useStore((s) => s.reorderSlide);
  const removeSlideFromCarousel = useStore((s) => s.removeSlideFromCarousel);
  const setSelectedBlock        = useStore((s) => s.setSelectedBlock);
  const deleteBlock             = useStore((s) => s.deleteBlock);

  const [slideMenu, setSlideMenu] = useState(false);

  // Carousel seçili → slide listesi + slide ekle
  if (block.type === 'carousel') {
    const slides = block.children || [];
    return (
      <Section title={`Slides (${slides.length})`}>
        {slides.length === 0 ? (
          <div className="props-form-hint" style={{ padding: 6 }}>
            Henüz slide yok — aşağıdan ekle.
          </div>
        ) : (
          <div className="carousel-slide-list">
            {slides.map((s, i) => (
              <div key={s.id} className="carousel-slide-row">
                <button
                  type="button"
                  className="carousel-slide-row__label"
                  onClick={() => setSelectedBlock(s.id)}
                  title="Slide ayarlarını aç"
                >
                  <span className="carousel-slide-idx">{i + 1}.</span>
                  <span className="carousel-slide-title">{s.title || s.id}</span>
                  <span className="carousel-slide-type">{TYPE_LABELS[s.type] || s.type}</span>
                </button>
                <div className="carousel-slide-row__actions">
                  <button
                    type="button"
                    className="props-btn props-btn--icon"
                    onClick={() => reorderSlide(s.id, -1)}
                    disabled={i === 0}
                    title="Yukarı taşı"
                  >↑</button>
                  <button
                    type="button"
                    className="props-btn props-btn--icon"
                    onClick={() => reorderSlide(s.id, +1)}
                    disabled={i === slides.length - 1}
                    title="Aşağı taşı"
                  >↓</button>
                  <button
                    type="button"
                    className="props-btn props-btn--icon props-btn--icon-danger"
                    onClick={() => {
                      if (window.confirm(`'${s.title || s.id}' slide'ı silinsin mi?`)) {
                        deleteBlock(s.id);
                      }
                    }}
                    title="Slide'ı sil"
                  >🗑</button>
                </div>
              </div>
            ))}
          </div>
        )}

        <Row label="Slide ekle">
          <button
            type="button"
            className="props-btn props-btn--primary props-btn--block"
            onClick={() => setSlideMenu((v) => !v)}
          >
            <Plus size={13} strokeWidth={2.5} />
            <span>Yeni slide</span>
          </button>
          {slideMenu && (
            <div className="props-inline-menu">
              {SLIDE_TYPES.map((t) => (
                <button
                  key={t.type}
                  type="button"
                  className="props-inline-menu-item"
                  onClick={() => {
                    setSlideMenu(false);
                    addSlideToCarousel(block.id, t.type);
                  }}
                >{t.label}</button>
              ))}
            </div>
          )}
        </Row>
      </Section>
    );
  }

  // Slide seçili (parent'ı carousel) — carousel'den çıkar butonu
  const isSlide = parentTypeOf(manifest?.blocks, block.id) === 'carousel';

  if (isSlide) {
    return (
      <Section title="Bu Slide">
        <Row label="Carousel'den çıkar" hint="Slide carousel'den ayrılır ve aynı section'ın sonuna taşınır.">
          <button
            type="button"
            className="props-btn props-btn--block"
            onClick={() => removeSlideFromCarousel(block.id)}
          >
            <Layers size={13} strokeWidth={2} />
            <span>Carousel'den çıkar</span>
          </button>
        </Row>
      </Section>
    );
  }

  // Diğer durumlar (normal section.child leaf veya section_header): hiçbir şey
  return null;
}


function CanvasActions({ block }) {
  const manifest                = useStore((s) => s.manifest);
  const addBlockToCanvas        = useStore((s) => s.addBlockToCanvas);
  const moveBlock               = useStore((s) => s.moveBlock);
  const removeSlideFromCarousel = useStore((s) => s.removeSlideFromCarousel);  // container-agnostik eject
  const setSelectedBlock        = useStore((s) => s.setSelectedBlock);
  const deleteBlock             = useStore((s) => s.deleteBlock);

  const [addMenu, setAddMenu] = useState(false);

  // Canvas seçili → child listesi + blok ekle. Bloklar 12-kolon grid'de yan
  // yana; sıra grid akışını belirler (genişlik Genel sekmesindeki "Genişlik").
  if (block.type === 'canvas') {
    const kids = block.children || [];
    return (
      <Section title={`Bloklar (${kids.length})`}>
        {kids.length === 0 ? (
          <div className="props-form-hint" style={{ padding: 6 }}>
            Henüz blok yok — aşağıdan ekle. Genişlikle yan yana diz.
          </div>
        ) : (
          <div className="carousel-slide-list">
            {kids.map((c, i) => (
              <div key={c.id} className="carousel-slide-row">
                <button
                  type="button"
                  className="carousel-slide-row__label"
                  onClick={() => setSelectedBlock(c.id)}
                  title="Blok ayarlarını aç"
                >
                  <span className="carousel-slide-idx">{i + 1}.</span>
                  <span className="carousel-slide-title">{c.title || c.id}</span>
                  <span className="carousel-slide-type">{TYPE_LABELS[c.type] || c.type}</span>
                </button>
                <div className="carousel-slide-row__actions">
                  <button
                    type="button"
                    className="props-btn props-btn--icon"
                    onClick={() => moveBlock(c.id, -1)}
                    disabled={i === 0}
                    title="Öne al"
                  >↑</button>
                  <button
                    type="button"
                    className="props-btn props-btn--icon"
                    onClick={() => moveBlock(c.id, +1)}
                    disabled={i === kids.length - 1}
                    title="Geri al"
                  >↓</button>
                  <button
                    type="button"
                    className="props-btn props-btn--icon props-btn--icon-danger"
                    onClick={() => {
                      if (window.confirm(`'${c.title || c.id}' bloğu silinsin mi?`)) {
                        deleteBlock(c.id);
                      }
                    }}
                    title="Bloğu sil"
                  >🗑</button>
                </div>
              </div>
            ))}
          </div>
        )}

        <Row label="Blok ekle">
          <button
            type="button"
            className="props-btn props-btn--primary props-btn--block"
            onClick={() => setAddMenu((v) => !v)}
          >
            <Plus size={13} strokeWidth={2.5} />
            <span>Yeni blok</span>
          </button>
          {addMenu && (
            <div className="props-inline-menu">
              {SLIDE_TYPES.map((t) => (
                <button
                  key={t.type}
                  type="button"
                  className="props-inline-menu-item"
                  onClick={() => {
                    setAddMenu(false);
                    addBlockToCanvas(block.id, t.type);
                  }}
                >{t.label}</button>
              ))}
            </div>
          )}
        </Row>
      </Section>
    );
  }

  // Canvas child seçili (parent'ı canvas) — tuval'dan çıkar.
  const isCanvasChild = parentTypeOf(manifest?.blocks, block.id) === 'canvas';

  if (isCanvasChild) {
    return (
      <Section title="Bu Blok">
        <Row label="Tuval'dan çıkar" hint="Blok tuvalden ayrılır ve aynı section'ın sonuna taşınır.">
          <button
            type="button"
            className="props-btn props-btn--block"
            onClick={() => removeSlideFromCarousel(block.id)}
          >
            <Layers size={13} strokeWidth={2} />
            <span>Tuval'dan çıkar</span>
          </button>
        </Row>
      </Section>
    );
  }

  return null;
}


// Madde 3 — menü ile bir leaf bloğu başka bir parent'a taşı: bir container
// (carousel/canvas) ya da bir section. Serbest dnd-kit sürükle-bırak sonraki iş.
function MoveBlockSection({ block }) {
  const manifest    = useStore((s) => s.manifest);
  const moveBetween = useStore((s) => s.moveBlockBetweenParents);
  const [menu, setMenu] = useState(false);

  // Sadece leaf bloklar taşınır — section_header ve container'ların kendisi değil.
  if (block.type === 'section_header' || CONTAINER_TYPES.has(block.type)) return null;

  // Bloğun mevcut parent id'sini bul (hedef listesinden çıkarmak için).
  const parentId = parentIdOf(manifest?.blocks, block.id);

  // Hedef listesi: tüm section'lar + container'lar (herhangi derinlik), mevcut
  // parent + kendisi hariç. Container içine container atılamayacağı kuralı
  // moveBlockBetweenParents'ta zorlanır; burada leaf taşındığı için hepsi geçerli.
  const targets = [];
  (function walk(arr, depthTag) {
    for (const b of (arr || [])) {
      if (b.type === 'section_header') {
        if (b.id !== parentId) targets.push({ id: b.id, label: `Bölüm: ${b.title || b.id}` });
      } else if (CONTAINER_TYPES.has(b.type)) {
        if (b.id !== parentId && b.id !== block.id) {
          const tag = b.type === 'carousel' ? 'Carousel' : 'Tuval';
          targets.push({ id: b.id, label: `${tag}: ${b.title || b.id}` });
        }
      }
      if (Array.isArray(b.children)) walk(b.children);
    }
  })(manifest?.blocks || []);
  if (targets.length === 0) return null;

  return (
    <Section title="Taşı">
      <Row label="Başka bir yere taşı" hint="Bloğu bir container'a (carousel/tuval) veya başka bir bölüme taşı.">
        <button
          type="button"
          className="props-btn props-btn--block"
          onClick={() => setMenu((v) => !v)}
        >
          <MoveRight size={13} strokeWidth={2} />
          <span>Taşı…</span>
        </button>
        {menu && (
          <div className="props-inline-menu">
            {targets.map((t) => (
              <button
                key={t.id}
                type="button"
                className="props-inline-menu-item"
                onClick={() => { setMenu(false); moveBetween(block.id, t.id); }}
                title={t.label}
              >{t.label}</button>
            ))}
          </div>
        )}
      </Row>
    </Section>
  );
}


function SaveToLibrarySection({ block }) {
  const openModal = useStore((s) => s.openSaveBlockModal);
  // filter_bar kütüphaneye kaydedilmez. Carousel/canvas → composite şablon
  // (madde 1): SaveBlockModal container'ı children'ıyla kaydeder.
  if (block.type === 'filter_bar') return null;
  return (
    <Section title="Şablon">
      <button
        type="button"
        className="props-btn props-btn--ghost"
        onClick={() => openModal(block.id)}
        title="Bu bloğu Bloklar listesine şablon olarak kaydet. Detayları (açıklama, dokümantasyon) Bloklar > Düzenle ekranında girersin."
      >
        <Save size={12} strokeWidth={2} />
        <span>Şablon Olarak Kaydet</span>
      </button>
    </Section>
  );
}


/* ── Refresh policy ──────────────────────────────────────────────────────
   Three-kind picker + sub-form. Mutates block.refresh_policy via
   setBlockField; backend Block schema validates on save. */

const RP_DAYS = [
  { code: 'MON', short: 'Pzt' }, { code: 'TUE', short: 'Sal' },
  { code: 'WED', short: 'Çar' }, { code: 'THU', short: 'Per' },
  { code: 'FRI', short: 'Cum' }, { code: 'SAT', short: 'Cmt' },
  { code: 'SUN', short: 'Paz' },
];

export function RefreshPolicyControls({ block }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const rp = block.refresh_policy || null;
  const kind = rp?.kind || 'on_open';
  const isScheduled = kind === 'scheduled';
  const isLazy = kind === 'lazy_ttl';
  const usingInterval = isScheduled && typeof rp?.interval_seconds === 'number';

  function setPolicy(next) {
    if (next == null || next.kind === 'on_open') {
      setBlockField(block.id, 'refresh_policy', null);
      return;
    }
    setBlockField(block.id, 'refresh_policy', next);
  }

  function setKind(newKind) {
    if (newKind === 'on_open') { setPolicy(null); return; }
    const base = {
      kind: newKind,
      fresh_for_seconds: rp?.fresh_for_seconds ?? 600,
      serve_stale: rp?.serve_stale !== false,
      max_age_seconds: rp?.max_age_seconds ?? 86400,
    };
    if (newKind === 'scheduled') {
      // Default to a single 09:00 weekday schedule.
      base.schedule = rp?.schedule || {
        times: ['09:00'],
        days: ['MON','TUE','WED','THU','FRI'],
        timezone: 'Europe/Istanbul',
      };
    }
    setPolicy(base);
  }

  return (
    <Section title="Veri Yenileme Politikası">
      <div className="props-form-hint" style={{ paddingBottom: 6 }}>
        Bu blok hangi sıklıkta tazelensin? Kütüphane önbelleğine yazılır,
        tüm görüntüleyiciler paylaşır.
      </div>

      <div className="rp-kinds">
        <RpKindOption checked={kind === 'on_open'} onChange={() => setKind('on_open')}
          title="on_open"
          desc="Önbellek yok. Her açılışta yeniden çalışır." />
        <RpKindOption checked={isLazy} onChange={() => setKind('lazy_ttl')}
          title="lazy_ttl"
          desc="İlk açan çeker, TTL boyunca herkes hızlı görür. Bayatsa eski sonuç + arka planda yenilenir." />
        <RpKindOption checked={isScheduled} onChange={() => setKind('scheduled')}
          title="scheduled"
          desc="Kimse açmasa da arkada belirli saatlerde tazelenir. Uzun sorgular için ideal." />
      </div>

      {isLazy && (
        <div className="rp-sub">
          <Row label="Tazelik süresi (sn)">
            <input type="number" className="props-input props-input--num"
              min={10} max={86400}
              value={rp.fresh_for_seconds || 600}
              onChange={(e) => setPolicy({ ...rp, fresh_for_seconds: Number(e.target.value) || 600 })} />
          </Row>
          <Row label="Max bayatlık (sn)">
            <input type="number" className="props-input props-input--num"
              min={60} max={2592000}
              value={rp.max_age_seconds || 86400}
              onChange={(e) => setPolicy({ ...rp, max_age_seconds: Number(e.target.value) || 86400 })} />
          </Row>
          <label className="rp-check">
            <input type="checkbox"
              checked={rp.serve_stale !== false}
              onChange={(e) => setPolicy({ ...rp, serve_stale: e.target.checked })} />
            Bayat veri göster (serve_stale)
          </label>
        </div>
      )}

      {isScheduled && (
        <div className="rp-sub">
          <div className="rp-mode">
            <button type="button"
              className={`rp-mode-btn${!usingInterval ? ' is-active' : ''}`}
              onClick={() => setPolicy({
                ...rp,
                interval_seconds: undefined,
                schedule: rp?.schedule || {
                  times: ['09:00'],
                  days: ['MON','TUE','WED','THU','FRI'],
                  timezone: 'Europe/Istanbul',
                },
              })}>
              Saat tabanlı
            </button>
            <button type="button"
              className={`rp-mode-btn${usingInterval ? ' is-active' : ''}`}
              onClick={() => setPolicy({
                ...rp,
                schedule: undefined,
                interval_seconds: rp?.interval_seconds || 600,
              })}>
              Periyot (sn)
            </button>
          </div>

          {usingInterval ? (
            <Row label="Periyot (sn)" hint="Her N saniyede bir.">
              <input type="number" className="props-input props-input--num"
                min={10} max={86400}
                value={rp.interval_seconds || 600}
                onChange={(e) => setPolicy({ ...rp, interval_seconds: Number(e.target.value) || 600 })} />
            </Row>
          ) : (
            <RpSchedule rp={rp} onChange={(sched) => setPolicy({ ...rp, schedule: sched })} />
          )}
        </div>
      )}
    </Section>
  );
}

function RpKindOption({ checked, onChange, title, desc }) {
  return (
    <label className={`rp-kind${checked ? ' is-active' : ''}`}>
      <input type="radio" checked={checked} onChange={onChange} />
      <div className="rp-kind__body">
        <div className="rp-kind__title">{title}</div>
        <div className="rp-kind__desc">{desc}</div>
      </div>
    </label>
  );
}

function RpSchedule({ rp, onChange }) {
  const sched = rp?.schedule || {
    times: ['09:00'],
    days: ['MON','TUE','WED','THU','FRI'],
    timezone: 'Europe/Istanbul',
  };
  const [newTime, setNewTime] = useState('09:00');

  function addTime() {
    const m = /^(\d{1,2}):(\d{2})$/.exec((newTime || '').trim());
    if (!m) return;
    const h = Number(m[1]), mm = Number(m[2]);
    if (h < 0 || h > 23 || mm < 0 || mm > 59) return;
    const canonical = `${String(h).padStart(2,'0')}:${String(mm).padStart(2,'0')}`;
    const set = new Set(sched.times || []);
    if (set.has(canonical)) return;
    set.add(canonical);
    onChange({ ...sched, times: Array.from(set).sort() });
  }
  function removeTime(t) {
    onChange({ ...sched, times: (sched.times || []).filter((x) => x !== t) });
  }
  function toggleDay(code) {
    const set = new Set(sched.days || []);
    if (set.has(code)) set.delete(code); else set.add(code);
    // Sort by weekday order.
    const order = RP_DAYS.map((d) => d.code);
    onChange({
      ...sched,
      days: order.filter((d) => set.has(d)),
    });
  }

  return (
    <>
      <Row label="Tetik saatleri (HH:MM)" hint="Birden fazla saat ekleyebilirsin.">
        <div className="rp-times">
          {(sched.times || []).map((t) => (
            <span key={t} className="rp-time-pill">
              {t}<button type="button" onClick={() => removeTime(t)} aria-label="Sil">×</button>
            </span>
          ))}
        </div>
        <div className="rp-add-time">
          <input type="time" className="props-input"
            value={newTime} onChange={(e) => setNewTime(e.target.value)} />
          <button type="button" className="rp-add-time-btn" onClick={addTime}>+ Ekle</button>
        </div>
      </Row>
      <Row label="Aktif günler">
        <div className="rp-days">
          {RP_DAYS.map((d) => {
            const on = (sched.days || []).includes(d.code);
            return (
              <label key={d.code} className={`rp-day${on ? ' is-on' : ''}`}>
                <input type="checkbox" checked={on} onChange={() => toggleDay(d.code)} />
                {d.short}
              </label>
            );
          })}
        </div>
      </Row>
      <Row label="Zaman dilimi" hint="IANA timezone (Europe/Istanbul, UTC, …).">
        <input type="text" className="props-input"
          value={sched.timezone || 'Europe/Istanbul'}
          onChange={(e) => onChange({ ...sched, timezone: e.target.value })} />
      </Row>
    </>
  );
}


function DangerZone({ block }) {
  const deleteBlock = useStore((s) => s.deleteBlock);
  const isSection = block.type === 'section_header';
  const childCount = isSection ? (block.children || []).length : 0;

  function handleDelete() {
    const msg = isSection
      ? (childCount > 0
          ? `'${block.title || block.id}' bölümünü ve içindeki ${childCount} bloğu silmek üzeresiniz. Devam edilsin mi?`
          : `'${block.title || block.id}' bölümünü silmek üzeresiniz. Devam edilsin mi?`)
      : `'${block.title || block.id}' bloğunu silmek üzeresiniz. Devam edilsin mi?`;
    if (!window.confirm(msg)) return;
    deleteBlock(block.id);
  }

  return (
    <Section title="Tehlikeli İşlem">
      <button
        type="button"
        className="props-btn props-btn--danger"
        onClick={handleDelete}
      >
        <Trash2 size={13} strokeWidth={2} />
        <span>{isSection ? 'Bölümü Sil' : 'Bloğu Sil'}</span>
      </button>
    </Section>
  );
}


/* ── Building blocks ─────────────────────────────────────────────────────── */

function Section({ title, children }) {
  return (
    <section className="props-section">
      <h4 className="props-section__title">{title}</h4>
      <div className="props-section__body">{children}</div>
    </section>
  );
}

function Row({ label, children, hint }) {
  return (
    <div className="props-form-row">
      <label className="props-form-label">{label}</label>
      <div className="props-form-control">{children}</div>
      {hint && <div className="props-form-hint">{hint}</div>}
    </div>
  );
}


/* ── Field components (all common) ──────────────────────────────────────── */

function TitleField({ block }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const [local, setLocal] = useState(block.title || '');
  useEffect(() => { setLocal(block.title || ''); }, [block.id, block.title]);

  function commit() {
    if (local !== (block.title || '')) {
      setBlockField(block.id, 'title', local);
    }
  }

  return (
    <Row label="Başlık">
      <input
        type="text"
        className="props-input"
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); }
          else if (e.key === 'Escape') {
            setLocal(block.title || '');
            e.currentTarget.blur();
          }
        }}
      />
    </Row>
  );
}

// Type-change dropdown. Switching type marks block.data_stale so the renderer
// can show "Bu blok yeni veri bekliyor" badge until the user re-runs the SQL.
// store.changeBlockType migrates the config's data keys (x_axis ↔ categories,
// combo kind/axis defaults) and persists type+config atomically; style
// settings are preserved and the next /run-manual refreshes the data fields.
const TYPE_CHANGE_OPTIONS = [
  { value: 'kpi',        label: 'KPI' },
  { value: 'bar_chart',  label: 'Çubuk Grafik' },
  { value: 'line_chart', label: 'Çizgi Grafik' },
  { value: 'combo_chart', label: 'Combo (Çubuk+Çizgi)' },
  { value: 'area_chart', label: 'Alan Grafiği' },
  { value: 'pie_chart',  label: 'Pasta Grafik' },
  { value: 'heatmap',    label: 'Isı Haritası' },
  { value: 'waterfall_chart', label: 'Waterfall (Köprü)' },
  { value: 'scatter_chart',   label: 'Bubble / Scatter' },
  { value: 'radial_bar', label: 'Radyal Gösterge' },
  { value: 'data_table', label: 'Tablo' },
];

function TypeField({ block }) {
  const changeBlockType = useStore((s) => s.changeBlockType);
  return (
    <Row label="Tip" hint="Tip değişirse mevcut veri uyumsuz olabilir — yeniden Çalıştır.">
      <select
        className="props-select"
        value={block.type}
        onChange={(e) => changeBlockType(block.id, e.target.value)}
      >
        {TYPE_CHANGE_OPTIONS.map((t) => (
          <option key={t.value} value={t.value}>{t.label}</option>
        ))}
      </select>
    </Row>
  );
}


function WidthField({ block }) {
  const setBlockWidth = useStore((s) => s.setBlockWidth);
  return (
    <Row label="Genişlik">
      <div className="width-picker">
        {WIDTH_OPTIONS.map((opt) => {
          const active = (block.width || 'full') === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              className={`width-picker-btn${active ? ' is-active' : ''}`}
              onClick={() => setBlockWidth(block.id, opt.value)}
            >{opt.label}</button>
          );
        })}
      </div>
    </Row>
  );
}

function LockField({ block }) {
  const toggleLock = useStore((s) => s.toggleLock);
  return (
    <Row label="Kilit">
      <button
        type="button"
        className={`lock-toggle-btn${block.locked ? ' is-locked' : ''}`}
        onClick={() => toggleLock(block.id)}
        title={block.locked ? 'Kilidi kaldır' : 'Kilitle (LLM değiştiremez)'}
      >
        {block.locked
          ? <><Lock size={13} strokeWidth={2} /> Kilitli</>
          : <><Unlock size={13} strokeWidth={2} /> Kilitsiz</>}
      </button>
    </Row>
  );
}


/* ── Generic helpers (controlled-input pattern) ─────────────────────────── */

function TextInput({ block, path, placeholder }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const initial = getByDotPath(block, path) ?? '';
  const [local, setLocal] = useState(String(initial));
  useEffect(() => { setLocal(String(initial ?? '')); }, [block.id, initial]);

  function commit() {
    if (local !== String(initial ?? '')) setBlockField(block.id, path, local);
  }

  return (
    <input
      type="text"
      className="props-input"
      value={local}
      placeholder={placeholder}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); }
        else if (e.key === 'Escape') {
          setLocal(String(initial ?? ''));
          e.currentTarget.blur();
        }
      }}
    />
  );
}

function NumberInput({ block, path, min, max, step, suffix }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const initial = getByDotPath(block, path);
  const [local, setLocal] = useState(initial == null ? '' : String(initial));
  useEffect(() => { setLocal(initial == null ? '' : String(initial)); }, [block.id, initial]);

  function commit() {
    const num = local === '' ? null : Number(local);
    if (num !== initial) setBlockField(block.id, path, num);
  }

  return (
    <div className="props-num-wrap">
      <input
        type="number"
        className="props-input props-input--num"
        value={local}
        min={min} max={max} step={step}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); }
          else if (e.key === 'Escape') {
            setLocal(initial == null ? '' : String(initial));
            e.currentTarget.blur();
          }
        }}
      />
      {suffix && <span className="props-num-suffix">{suffix}</span>}
    </div>
  );
}

function ToggleInput({ block, path }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const checked = !!getByDotPath(block, path);
  return (
    <label className="props-toggle">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => setBlockField(block.id, path, e.target.checked ? true : null)}
      />
      <span className="props-toggle-slider" />
    </label>
  );
}

function SelectInput({ block, path, options }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const value = getByDotPath(block, path) ?? '';
  return (
    <select
      className="props-input props-select"
      value={value}
      onChange={(e) => {
        const v = e.target.value;
        setBlockField(block.id, path, v === '' ? null : v);
      }}
    >
      <option value="">(varsayılan)</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

function getByDotPath(obj, path) {

  if (!obj || !path) return undefined;
  return path.split('.').reduce((acc, seg) => (acc == null ? acc : acc[seg]), obj);
}

function ColorInput({ block, path }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const value = getByDotPath(block, path) || '';
  return (
    <div className="props-color-wrap">
      <input
        type="color"
        className="props-color"
        value={value || '#1e293b'}
        onChange={(e) => setBlockField(block.id, path, e.target.value)}
      />
      <input
        type="text"
        className="props-input props-input--hex"
        placeholder="#1e293b"
        value={value}
        onChange={(e) => setBlockField(block.id, path, e.target.value || null)}
      />
      {value && (
        <button
          type="button"
          className="props-btn props-btn--ghost props-color-clear"
          onClick={() => setBlockField(block.id, path, null)}
          title="Varsayılana dön"
        >×</button>
      )}
    </div>
  );
}


/* ── Type-specific control groups ───────────────────────────────────────── */

function SectionHeaderControls({ block }) {
  return (
    <Section title="Başlık Stili">
      <Row label="Yazı boyutu (px)">
        <NumberInput block={block} path="config.font_size" min={12} max={64} step={1} suffix="px" />
      </Row>
      <Row label="Renk">
        <ColorInput block={block} path="config.color" />
      </Row>
      <Row label="Kalınlık">
        <SelectInput block={block} path="config.weight" options={[
          { value: 'normal',  label: 'İnce' },
          { value: '500',     label: 'Orta' },
          { value: '600',     label: 'Yarı kalın' },
          { value: '700',     label: 'Kalın' },
        ]} />
      </Row>
      <Row label="Hizalama">
        <SelectInput block={block} path="config.text_align" options={[
          { value: 'left',   label: 'Sol' },
          { value: 'center', label: 'Orta' },
          { value: 'right',  label: 'Sağ' },
        ]} />
      </Row>
    </Section>
  );
}

function KpiControls({ block }) {
  return (
    <Section title="KPI Ayarları">
      <Row label="Birim"><TextInput block={block} path="config.unit" placeholder="örn. B TRY" /></Row>
      <Row label="Dönem"><TextInput block={block} path="config.period" placeholder="örn. Q4 2025" /></Row>
      <Row label="Delta etiketi"><TextInput block={block} path="config.delta_label" placeholder="Q3'25'e karşı" /></Row>
    </Section>
  );
}

function BarChartControls({ block }) {
  return (
    <Section title="Çubuk Grafik Ayarları">
      <Row label="Yatay"><ToggleInput block={block} path="config.horizontal" /></Row>
      <Row label="Yığılmış (stacked)"><ToggleInput block={block} path="config.stacked" /></Row>
      <Row label="Çoklu renk (distributed)"
           hint="Tek seri için her bar farklı renkte"><ToggleInput block={block} path="config.distributed" /></Row>
      <Row label="Veri etiketleri"><ToggleInput block={block} path="config.show_data_labels" /></Row>
      <Row label="Köşe yuvarlama"><NumberInput block={block} path="config.border_radius" min={0} max={20} step={1} suffix="px" /></Row>
    </Section>
  );
}

function LineChartControls({ block, type }) {
  return (
    <Section title={type === 'area_chart' ? 'Alan Grafiği Ayarları' : 'Çizgi Grafik Ayarları'}>
      <Row label="Eğri tipi">
        <SelectInput block={block} path="config.curve" options={[
          { value: 'smooth', label: 'Yumuşak' },
          { value: 'straight', label: 'Düz' },
          { value: 'stepline', label: 'Basamaklı' },
        ]} />
      </Row>
      <Row label="Çizgi kalınlığı"><NumberInput block={block} path="config.stroke_width" min={1} max={6} step={1} suffix="px" /></Row>
      <Row label="Noktalar görünsün"><ToggleInput block={block} path="config.show_markers" /></Row>
      {type === 'area_chart' && (
        <Row label="Dolgu opaklığı" hint="0-1 arası">
          <NumberInput block={block} path="config.fill_opacity" min={0} max={1} step={0.1} />
        </Row>
      )}
    </Section>
  );
}

function ComboChartControls({ block }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const series = (block.config && block.config.series) || [];
  return (
    <Section title="Combo Grafik Ayarları">
      {series.length === 0 ? (
        <div className="props-form-hint">
          SQL'i çalıştır → seriler gelince her birini Çubuk/Çizgi ve Sol/Sağ eksen
          olarak ayarla. (1. kolon = kategori, sonraki kolonlar = seriler.)
        </div>
      ) : (
        <>
          <div className="props-form-hint" style={{ paddingBottom: 4 }}>
            Her seri için tip ve eksen seç:
          </div>
          {series.map((s, i) => (
            <div key={i} className="combo-series-row"
                 style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
              <span title={s.name || `Seri ${i + 1}`}
                    style={{ flex: '1 1 0', minWidth: 0, fontSize: 12,
                             overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {s.name || `Seri ${i + 1}`}
              </span>
              <select className="props-input props-select" style={{ flex: '0 0 84px' }}
                value={s.kind === 'line' || s.kind === 'bar' ? s.kind : comboSeriesDefaults(i).kind}
                onChange={(e) => setBlockField(block.id, `config.series.${i}.kind`, e.target.value)}>
                <option value="bar">Çubuk</option>
                <option value="line">Çizgi</option>
              </select>
              <select className="props-input props-select" style={{ flex: '0 0 72px' }}
                value={s.axis === 'right' || s.axis === 'left' ? s.axis : comboSeriesDefaults(i).axis}
                onChange={(e) => setBlockField(block.id, `config.series.${i}.axis`, e.target.value)}>
                <option value="left">Sol</option>
                <option value="right">Sağ</option>
              </select>
            </div>
          ))}
        </>
      )}
      <Row label="Sol eksen başlığı"><TextInput block={block} path="config.left_axis_title" placeholder="örn. Oran (%)" /></Row>
      <Row label="Sağ eksen başlığı"><TextInput block={block} path="config.right_axis_title" placeholder="örn. Hacim" /></Row>
      <Row label="Çizgi eğrisi">
        <SelectInput block={block} path="config.curve" options={[
          { value: 'smooth', label: 'Yumuşak' },
          { value: 'straight', label: 'Düz' },
          { value: 'stepline', label: 'Basamaklı' },
        ]} />
      </Row>
      <Row label="Çizgi noktaları"><ToggleInput block={block} path="config.show_markers" /></Row>
      <Row label="Çubuk veri etiketleri"><ToggleInput block={block} path="config.show_data_labels" /></Row>
    </Section>
  );
}

function PieChartControls({ block }) {
  return (
    <Section title="Pasta Grafik Ayarları">
      <Row label="Donut (halka)"><ToggleInput block={block} path="config.donut" /></Row>
      <Row label="Lejant konumu">
        <SelectInput block={block} path="config.legend_position" options={[
          { value: 'top', label: 'Üst' },
          { value: 'right', label: 'Sağ' },
          { value: 'bottom', label: 'Alt' },
          { value: 'left', label: 'Sol' },
        ]} />
      </Row>
      <Row label="Veri etiketleri"><ToggleInput block={block} path="config.show_data_labels" /></Row>
    </Section>
  );
}

function HeatmapControls({ block }) {
  return (
    <Section title="Isı Haritası Ayarları">
      <Row label="Değerler hücrede gözüksün"><ToggleInput block={block} path="config.show_values" /></Row>
    </Section>
  );
}

function RadialBarControls({ block }) {
  return (
    <Section title="Radyal Gösterge Ayarları">
      <Row label="Maks değer"><NumberInput block={block} path="config.max" /></Row>
      <Row label="Etiket"><TextInput block={block} path="config.label" /></Row>
    </Section>
  );
}

/* ── Referans çizgileri (yatay/dikey) — tüm kartezyen chart'larda ───────── */

const REF_LINE_TYPES = new Set([
  'bar_chart', 'line_chart', 'area_chart', 'combo_chart',
  'waterfall_chart', 'scatter_chart',
]);

function RefLinesControls({ block }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const lines = Array.isArray(block.config?.ref_lines) ? block.config.ref_lines : [];

  function addLine() {
    setBlockField(block.id, 'config.ref_lines',
      [...lines, { axis: 'y', value: 0, label: '' }]);
  }
  function removeLine(i) {
    setBlockField(block.id, 'config.ref_lines', lines.filter((_, j) => j !== i));
  }

  return (
    <Section title="Referans Çizgileri">
      <div className="props-form-hint" style={{ paddingBottom: 6 }}>
        Kesikli yatay (Y) / dikey (X) çizgi. X değeri sayısal eksende sayı,
        kategori ekseninde kategori etiketi olmalı.
      </div>
      {lines.map((l, i) => {
        const fromQuery = l.source === 'query';
        return (
          <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
            <select
              className="props-input props-select"
              style={{ flex: '0 0 56px' }}
              value={l.axis === 'x' ? 'x' : 'y'}
              disabled={fromQuery}
              onChange={(e) => setBlockField(block.id, `config.ref_lines.${i}.axis`, e.target.value)}
            >
              <option value="y">Y</option>
              <option value="x">X</option>
            </select>
            {fromQuery ? (
              <span
                title="Bu çizgi SQL sonucundan gelir (5. kolon) — değeri her koşuda güncellenir"
                style={{ flex: '1 1 0', minWidth: 0, fontSize: 12, overflow: 'hidden',
                         textOverflow: 'ellipsis', whiteSpace: 'nowrap', opacity: 0.8 }}
              >
                {typeof l.value === 'number' ? l.value.toLocaleString('tr-TR') : String(l.value)}
                {' · '}{l.label || 'SQL'} <em style={{ fontSize: 10 }}>(SQL)</em>
              </span>
            ) : (
              <>
                <TextInput block={block} path={`config.ref_lines.${i}.value`} placeholder="değer" />
                <TextInput block={block} path={`config.ref_lines.${i}.label`} placeholder="etiket" />
              </>
            )}
            {!fromQuery && (
              <button
                type="button"
                className="props-btn props-btn--icon props-btn--icon-danger"
                title="Çizgiyi kaldır"
                onClick={() => removeLine(i)}
                style={{ flex: '0 0 auto' }}
              >
                <X size={13} strokeWidth={2} />
              </button>
            )}
          </div>
        );
      })}
      <button type="button" className="props-btn props-btn--ghost" onClick={addLine}>
        <Plus size={13} strokeWidth={2} /> Çizgi ekle
      </button>
    </Section>
  );
}

function NarrativeControls({ block }) {
  const setBlockField = useStore((s) => s.setBlockField);
  const initial = block.config?.text || '';
  const [local, setLocal] = useState(initial);
  useEffect(() => { setLocal(initial); }, [block.id, initial]);

  function commit() {
    if (local !== initial) setBlockField(block.id, 'config.text', local);
  }

  return (
    <Section title="Metin">
      <Row label="İçerik" hint="Ctrl/⌘ + Enter ile kaydet">
        <textarea
          className="props-textarea"
          rows={6}
          value={local}
          onChange={(e) => setLocal(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            // Ctrl/Cmd + Enter → commit + blur
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              e.currentTarget.blur();
            } else if (e.key === 'Escape') {
              setLocal(initial);
              e.currentTarget.blur();
            }
          }}
        />
      </Row>
    </Section>
  );
}


/* ── SQL şekil rehberi (manuel SQL yazarken neyin beklendiğini gösterir) ── */

const SQL_SHAPE_HINTS = {
  kpi: {
    cols: 1,
    summary: '1 satır × 1 kolon → tek sayı',
    columns: [{ name: 'value', type: 'NUMBER', desc: 'KPI olarak gösterilecek sayı' }],
    example: 'SELECT SUM(BALANCE_TRY)/1e9 AS value FROM EDW.DEPOSITS_DAILY',
  },
  radial_bar: {
    cols: 1,
    summary: '1 satır × 1 kolon → yüzde / oran',
    columns: [{ name: 'value', type: 'NUMBER', desc: 'Tek sayı (radial bar değeri)' }],
    example: 'SELECT AVG(MUZAKERE_ORANI_PCT) AS value FROM upload__x__y',
  },
  bar_chart: {
    cols: '2+',
    summary: 'N satır × 2+ kolon → 1. kolon kategori, sonraki kolonlar seriler',
    columns: [
      { name: 'category', type: 'VARCHAR', desc: 'X ekseni etiketi (örn. şube adı)' },
      { name: 'value(s)', type: 'NUMBER', desc: 'Bar yükseklikleri — her ek kolon ayrı seri' },
    ],
    example: 'SELECT BRANCH_CODE, SUM(BALANCE_TRY)/1e9 FROM ... GROUP BY BRANCH_CODE ORDER BY 2 DESC',
  },
  line_chart: {
    cols: '2+',
    summary: 'N satır × 2+ kolon → 1. kolon x ekseni (tarih/kategori), sonraki kolonlar seriler',
    columns: [
      { name: 'x', type: 'DATE veya VARCHAR', desc: 'X ekseni (zaman veya kategori). ORDER BY ile sıralı gelmeli.' },
      { name: 'value(s)', type: 'NUMBER', desc: 'Çizgi seri(leri) — her ek kolon ayrı seri' },
    ],
    example: 'SELECT DATE_COL, SUM(VAL) FROM ... GROUP BY DATE_COL ORDER BY DATE_COL',
  },
  combo_chart: {
    cols: '2+',
    summary: 'N satır × 2+ kolon → 1. kolon kategori, sonraki kolonlar seriler. Her seriyi properties\'te Çubuk/Çizgi + Sol/Sağ eksen yap.',
    columns: [
      { name: 'category', type: 'VARCHAR/DATE', desc: 'Ortak x ekseni (örn. ay). Sıralı gelmeli.' },
      { name: 'value(s)', type: 'NUMBER', desc: 'Her ek kolon ayrı seri — bar mı çizgi mi olduğunu sen seçersin' },
    ],
    example: 'SELECT MONTH, REVENUE, MARGIN_PCT FROM ... GROUP BY MONTH ORDER BY MONTH',
  },
  area_chart: {
    cols: '2+',
    summary: 'N satır × 2+ kolon → 1. kolon x ekseni, sonraki kolonlar seriler (alan)',
    columns: [
      { name: 'x', type: 'DATE veya VARCHAR', desc: 'X ekseni. Sıralı gelmeli.' },
      { name: 'value(s)', type: 'NUMBER', desc: 'Alan seri(leri)' },
    ],
    example: 'SELECT MONTH, REVENUE FROM ... ORDER BY MONTH',
  },
  pie_chart: {
    cols: 2,
    summary: 'N satır × 2 kolon → 1. kolon dilim etiketi, 2. kolon değer',
    columns: [
      { name: 'label', type: 'VARCHAR', desc: 'Dilim adı (örn. segment)' },
      { name: 'value', type: 'NUMBER', desc: 'Dilim büyüklüğü' },
    ],
    example: 'SELECT SEGMENT, SUM(BALANCE_TRY) FROM ... GROUP BY SEGMENT',
  },
  heatmap: {
    cols: 3,
    summary: 'N satır × 3 kolon → 1. kolon x, 2. kolon y, 3. kolon değer (renk yoğunluğu)',
    columns: [
      { name: 'x', type: 'VARCHAR', desc: 'X ekseni etiketi' },
      { name: 'y', type: 'VARCHAR', desc: 'Y ekseni etiketi' },
      { name: 'value', type: 'NUMBER', desc: 'Hücre değeri — renk yoğunluğunu belirler' },
    ],
    example: 'SELECT BRANCH, MONTH, BALANCE FROM ... ORDER BY BRANCH, MONTH',
  },
  data_table: {
    cols: 'N',
    summary: 'N satır × N kolon → kolonlar olduğu gibi tabloya gelir',
    columns: [
      { name: 'col_1 … col_N', type: 'her tip', desc: 'Kolon adları başlık olarak kullanılır' },
    ],
    example: 'SELECT BRANCH_CODE, SEGMENT, BALANCE_TRY FROM ... FETCH FIRST 50 ROWS ONLY',
  },
};

function SqlShapeHelper({ blockType }) {
  const hint = SQL_SHAPE_HINTS[blockType];
  if (!hint) return null;
  return (
    <div className="props-sql-helper">
      <div className="props-sql-helper__summary">
        <span className="props-sql-helper__badge">{hint.cols} kolon</span>
        <span>{hint.summary}</span>
      </div>
      <table className="props-sql-helper__table">
        <thead>
          <tr>
            <th>#</th>
            <th>Kolon</th>
            <th>Tip</th>
            <th>Açıklama</th>
          </tr>
        </thead>
        <tbody>
          {hint.columns.map((c, i) => (
            <tr key={i}>
              <td>{i + 1}</td>
              <td><code>{c.name}</code></td>
              <td>{c.type}</td>
              <td>{c.desc}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details className="props-sql-helper__example">
        <summary>Örnek</summary>
        <code>{hint.example}</code>
      </details>
    </div>
  );
}


/* ── SQL editor + refresh ───────────────────────────────────────────────── */

function SqlEditor({ block }) {
  const refreshBlock = useStore((s) => s.refreshBlock);
  const initial = block.data_source?.original_sql || '';
  const [sql, setSql] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => { setSql(initial); setErr(null); }, [block.id, initial]);

  const dirty = sql !== initial;

  async function handleRefresh() {
    setBusy(true);
    setErr(null);
    try {
      // dirty değilse newSql null gönder → mevcut SQL re-execute
      await refreshBlock(block.id, dirty ? sql : null);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Veri Kaynağı (SQL)">
      <div className="props-sql-wrap">
        <textarea
          className="props-textarea props-textarea--sql"
          rows={8}
          spellCheck={false}
          value={sql}
          onChange={(e) => setSql(e.target.value)}
        />
        <div className="props-sql-footer">
          <button
            type="button"
            className="props-btn props-btn--primary"
            onClick={handleRefresh}
            disabled={busy}
          >
            <RefreshCw size={13} strokeWidth={2} className={busy ? 'spin' : ''} />
            <span>{dirty ? 'SQL\'i kaydet & tazele' : 'Veriyi tazele'}</span>
          </button>
          {dirty && (
            <button
              type="button"
              className="props-btn props-btn--ghost"
              onClick={() => { setSql(initial); setErr(null); }}
            >Geri al</button>
          )}
        </div>
        {err && <div className="props-sql-error">{err}</div>}
        <SqlShapeHelper blockType={block.type} />
      </div>
    </Section>
  );
}
