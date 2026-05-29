/**
 * FixedDateFilter — Sunum'un sağ-alt köşesinde sabit (fixed) tarih filtresi.
 *
 * Üst FilterBar'daki `date_range` filtreleri buraya taşınır (enum/sayı üstte
 * kalır). Her tarih filtresi bir "pill" olarak yığılır:
 *   - ◀ / ▶ ok butonları (ve pill odaktayken klavye ok tuşları) → ±1 gün.
 *       · tek tarih  (filter.single)  → günü kaydırır (from == to).
 *       · aralık                       → pencereyi (from+to) birlikte kaydırır.
 *   - Tarihe tıklayınca üstte özel bir ay-takvimi (CalendarPopover) açılır.
 *       · tek tarih → tek gün seçimi.
 *       · aralık     → iki uçlu seçim (hover ile aralık vurgusu).
 *
 * Değişiklikler ~300ms debounce ile otomatik uygulanır (applyFilters). Stored
 * değer her zaman {from, to} ISO dict'i — backend predicate (between) değişmez;
 * tek tarih sadece from == to demektir.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Calendar, X } from 'lucide-react';
import useStore from '../lib/store.js';
import { _resolveDateExpr } from './FilterBar.jsx';

const _MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
                 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];
const _DOW = ['Pt', 'Sa', 'Ça', 'Pe', 'Cu', 'Ct', 'Pz'];

const _pad2 = (n) => String(n).padStart(2, '0');
const _toIso = (d) => `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`;
const _fromIso = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
function _todayIso() { const d = new Date(); d.setHours(0, 0, 0, 0); return _toIso(d); }
function _addDays(iso, n) { const d = _fromIso(iso); d.setDate(d.getDate() + n); return _toIso(d); }
// Resolve a stored value (ISO or relative "today - 30d") to an ISO date, with
// a today fallback so the widget never shows an empty/invalid date.
const _iso = (v) => _resolveDateExpr(v) || _todayIso();


// ── Month calendar popover (custom) ─────────────────────────────────────────

function _monthMatrix(year, month) {
  const first = new Date(year, month, 1);
  const lead = (first.getDay() + 6) % 7;            // Monday-start blanks
  const days = new Date(year, month + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(null);
  for (let d = 1; d <= days; d++) cells.push(_toIso(new Date(year, month, d)));
  while (cells.length % 7 !== 0) cells.push(null);
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

function CalendarPopover({ mode, value, onPick, onClose }) {
  const seedIso = mode === 'single' ? _iso(value?.from) : _iso(value?.from);
  const seed = _fromIso(seedIso);
  const [vy, setVy] = useState(seed.getFullYear());
  const [vm, setVm] = useState(seed.getMonth());
  const [anchor, setAnchor] = useState(null);   // range: first click
  const [hover, setHover] = useState(null);
  const ref = useRef(null);

  useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) onClose(); }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [onClose]);

  const selFrom = mode === 'range' ? _iso(value?.from) : null;
  const selTo = mode === 'range' ? _iso(value?.to) : null;
  const selSingle = mode === 'single' ? _iso(value?.from) : null;

  function prevMonth() { const m = vm - 1; if (m < 0) { setVm(11); setVy(vy - 1); } else setVm(m); }
  function nextMonth() { const m = vm + 1; if (m > 11) { setVm(0); setVy(vy + 1); } else setVm(m); }

  function clickDay(iso) {
    if (!iso) return;
    if (mode === 'single') { onPick(iso); onClose(); return; }
    if (!anchor) { setAnchor(iso); setHover(iso); return; }
    let from = anchor, to = iso;
    if (to < from) [from, to] = [to, from];
    onPick({ from, to });
    setAnchor(null);
    onClose();
  }

  function cellState(iso) {
    if (!iso) return '';
    if (mode === 'single') return iso === selSingle ? 'is-end' : '';
    // range: live selection while anchoring, else the committed range
    let a, b;
    if (anchor) { a = anchor; b = hover || anchor; if (b < a) [a, b] = [b, a]; }
    else { a = selFrom; b = selTo; }
    if (!a || !b) return '';
    if (iso === a || iso === b) return 'is-end';
    if (iso > a && iso < b) return 'is-mid';
    return '';
  }

  const today = _todayIso();
  const weeks = useMemo(() => _monthMatrix(vy, vm), [vy, vm]);

  return (
    <div className="fdf-cal" ref={ref} role="dialog">
      <div className="fdf-cal__head">
        <button type="button" className="fdf-cal__nav" onClick={prevMonth} title="Önceki ay">«</button>
        <span className="fdf-cal__title">{_MONTHS[vm]} {vy}</span>
        <button type="button" className="fdf-cal__nav" onClick={nextMonth} title="Sonraki ay">»</button>
      </div>
      <div className="fdf-cal__dow">{_DOW.map((d) => <span key={d}>{d}</span>)}</div>
      <div className="fdf-cal__grid" onMouseLeave={() => anchor && setHover(anchor)}>
        {weeks.flat().map((iso, i) => (
          <button
            key={i}
            type="button"
            className={`fdf-cal__day ${iso ? cellState(iso) : 'is-blank'}${iso === today ? ' is-today' : ''}`}
            disabled={!iso}
            onMouseEnter={() => iso && anchor && setHover(iso)}
            onClick={() => clickDay(iso)}
          >
            {iso ? _fromIso(iso).getDate() : ''}
          </button>
        ))}
      </div>
      {mode === 'range' && (
        <div className="fdf-cal__hint">
          {anchor ? 'Bitiş tarihini seç' : 'Başlangıç tarihini seç'}
        </div>
      )}
    </div>
  );
}


// ── A single date filter pill ────────────────────────────────────────────────

function DatePill({ filter, editable, onRemove }) {
  const value = useStore((s) => s.filterState[filter.id]);
  const setFilterValue = useStore((s) => s.setFilterValue);
  const applyFilters = useStore((s) => s.applyFilters);
  const filterBusy = useStore((s) => s.filterBusy);
  const [calOpen, setCalOpen] = useState(false);
  const timer = useRef(null);

  const single = !!filter.single;
  const fromIso = _iso(value?.from);
  const toIso = _iso(value?.to);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  function commit(nextVal) {
    setFilterValue(filter.id, nextVal);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { applyFilters().catch(() => {}); }, 300);
  }

  function shift(n) {
    if (single) { const d = _addDays(fromIso, n); commit({ from: d, to: d }); }
    else commit({ from: _addDays(fromIso, n), to: _addDays(toIso, n) });
  }

  function onPick(picked) {
    if (single) commit({ from: picked, to: picked });
    else commit({ from: picked.from, to: picked.to });
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowLeft') { e.preventDefault(); shift(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); shift(1); }
  }

  return (
    <div className="fdf-pill">
      <div className="fdf-pill__head">
        <span className="fdf-pill__label">{filter.label}{single ? '' : ' · aralık'}</span>
        {editable && (
          <button type="button" className="fdf-pill__remove" onClick={onRemove} title="Filtreyi sil">
            <X size={12} strokeWidth={2.4} />
          </button>
        )}
      </div>
      <div className="fdf-pill__ctrl" tabIndex={0} onKeyDown={onKeyDown}>
        <button type="button" className="fdf-pill__arrow" onClick={() => shift(-1)}
                disabled={filterBusy} title="Önceki gün (←)">
          <ChevronLeft size={16} strokeWidth={2.4} />
        </button>
        <button type="button" className="fdf-pill__date" onClick={() => setCalOpen((o) => !o)}
                title="Takvimi aç">
          <Calendar size={13} strokeWidth={2} />
          {single ? <span>{fromIso}</span>
                  : <span>{fromIso} <span className="fdf-pill__arrowsep">→</span> {toIso}</span>}
        </button>
        <button type="button" className="fdf-pill__arrow" onClick={() => shift(1)}
                disabled={filterBusy} title="Sonraki gün (→)">
          <ChevronRight size={16} strokeWidth={2.4} />
        </button>
      </div>
      {calOpen && (
        <CalendarPopover
          mode={single ? 'single' : 'range'}
          value={value}
          onPick={onPick}
          onClose={() => setCalOpen(false)}
        />
      )}
    </div>
  );
}


// ── Fixed container ──────────────────────────────────────────────────────────

export default function FixedDateFilter() {
  const manifest = useStore((s) => s.manifest);
  const layoutEditMode = useStore((s) => s.layoutEditMode);
  const viewMode = useStore((s) => s.viewMode);
  const removeFilter = useStore((s) => s.removeDashboardFilter);
  const editable = layoutEditMode && viewMode === 'edit';
  const dateFilters = (manifest?.filters || []).filter((f) => f.type === 'date_range');
  if (dateFilters.length === 0) return null;
  return (
    <div className="fdf-fixed" role="region" aria-label="Tarih filtresi">
      {dateFilters.map((f) => (
        <DatePill key={f.id} filter={f} editable={editable} onRemove={() => removeFilter(f.id)} />
      ))}
    </div>
  );
}
