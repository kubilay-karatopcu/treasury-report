/**
 * Shared relative-date resolution for filter widgets (Sunum FilterBar +
 * Hazırlık Filtreleme tab). Single source so both agree with the backend
 * parse_date_expr (presentations/variables/resolver.py).
 *
 * Grammar (spec §3.3):
 *   <ISO date>            e.g. 2026-01-01
 *   today
 *   today - Nd / Nw / Nm / Ny
 *   start_of_month | start_of_year | start_of_quarter
 *
 * Relative exprs are stored verbatim and re-resolved each run (dynamic) — the
 * backend compiles the actual SQL; this only resolves for display in the UI.
 */

function _pad2(n) { return String(n).padStart(2, '0'); }

export function _toIsoDate(d) {
  return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`;
}

/**
 * Resolve a stored filter value (ISO date OR relative expr) to a yyyy-MM-dd
 * string for the native <input type="date">. Returns '' when unparseable so
 * the input renders empty instead of throwing the HTML5 format error.
 */
export function _resolveDateExpr(v) {
  if (!v || typeof v !== 'string') return '';
  const s = v.trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);   // ISO (date or datetime)
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const rel = s.match(/^today(?:\s*-\s*(\d+)\s*([dwmy])?)?$/i);   // unit optional → days
  if (rel) {
    if (!rel[1]) return _toIsoDate(today);
    const n = parseInt(rel[1], 10);
    const u = (rel[2] || 'd').toLowerCase();
    const d = new Date(today);
    if (u === 'd') d.setDate(d.getDate() - n);
    else if (u === 'w') d.setDate(d.getDate() - n * 7);
    else if (u === 'm') d.setMonth(d.getMonth() - n);
    else if (u === 'y') d.setFullYear(d.getFullYear() - n);
    return _toIsoDate(d);
  }
  const anchor = s.match(/^start_of_(month|year|quarter)$/i);
  if (anchor) {
    const a = anchor[1].toLowerCase();
    const d = new Date(today);
    if (a === 'month') d.setDate(1);
    else if (a === 'year') { d.setMonth(0); d.setDate(1); }
    else { d.setMonth(Math.floor(d.getMonth() / 3) * 3); d.setDate(1); }
    return _toIsoDate(d);
  }
  return '';   // unparseable → empty input (no console error)
}

/** True when the value is a relative/anchor expression (not a plain ISO date). */
export function isRelativeDateExpr(v) {
  if (!v || typeof v !== 'string') return false;
  const s = v.trim();
  return /^today(\s*-\s*\d+\s*[dwmy]?)?$/i.test(s)
      || /^start_of_(month|year|quarter)$/i.test(s);
}
