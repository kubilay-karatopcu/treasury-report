// AG Charts option builders for the Treasury presentations editor.
//
// Replaces the previous ApexCharts helpers. Each export returns a
// complete AgChartOptions object the block component passes straight to
// <AgCharts options={...}/> — no separate `series` prop, no `type` prop.
//
// AG Charts wants row-of-objects data + field-keyed series; the manifest
// keeps Apex's (categories + series-arrays) shape so the LLM/back-end
// stay unchanged. The seriesTo*Data helpers below do the conversion.
//
// Heatmap + radial-bar use ag-charts-enterprise; everything else is
// covered by ag-charts-community.
import 'ag-charts-enterprise';

import { theme } from '../theme.js';


// ── Formatters ────────────────────────────────────────────────────────

export function formatNumber(v) {
  if (v == null || isNaN(v)) return '';
  const abs = Math.abs(v);
  if (abs >= 1e9)  return (v / 1e9).toFixed(1) + 'B';
  if (abs >= 1e6)  return (v / 1e6).toFixed(1) + 'M';
  if (abs >= 1e3)  return (v / 1e3).toFixed(1) + 'K';
  if (abs < 1 && v !== 0) return v.toFixed(2);
  return Math.round(v).toString();
}

// LLM sometimes emits objects like [{date: "2025-01-01"}, ...] instead of
// plain strings. Coerce defensively so the chart still renders.
export function normalizeLabels(arr) {
  return (arr || []).map((c) => {
    if (c == null) return '';
    if (typeof c === 'string' || typeof c === 'number') return c;
    if (typeof c === 'object') {
      return (
        c.label ?? c.x ?? c.name ?? c.date ?? c.period ?? c.title ??
        Object.values(c).find((v) => typeof v === 'string' || typeof v === 'number') ??
        ''
      );
    }
    return String(c);
  });
}


// ── Data conversion ───────────────────────────────────────────────────
// Manifest shape (Apex-style): categories: [str], series: [{name, values}]
// AG Charts shape:             data: [{__x: str, <name1>: v, <name2>: v}]
//
// We use ``__x`` as the x-axis field key so it can't collide with any
// user-chosen series name (the LLM may produce series called "x" or
// "category"). Series names become object keys directly.

function uniqSeriesNames(series) {
  const seen = new Map();
  return series.map((s, i) => {
    let name = (s.name ?? `Seri ${i + 1}`).toString().trim() || `Seri ${i + 1}`;
    const count = (seen.get(name) || 0) + 1;
    seen.set(name, count);
    return count === 1 ? name : `${name} (${count})`;
  });
}

// AG Charts treats yKey as a data-row field name; series names with '%' or
// parens like "Maks. Oran (%)" trip the internal format-string scanner and
// throw "Invalid property path" warnings. We use a safe per-index key for
// data access + keep the human name for display via yName.
function safeYKey(i) {
  return `__y${i}`;
}

function seriesToRowData(categories, series) {
  const names = uniqSeriesNames(series);
  const keys = names.map((_, i) => safeYKey(i));
  const rows = categories.map((cat, i) => {
    const row = { __x: cat };
    series.forEach((s, j) => {
      row[keys[j]] = (s.values || [])[i] ?? null;
    });
    return row;
  });
  return { rows, names, keys };
}


// ── Theme overrides ───────────────────────────────────────────────────
// We hand-roll the dark theme rather than rely on ag-default-dark so we
// can pin axis label colors, grid lines, tooltip background to the same
// tokens the rest of the editor uses (kept in theme.js).

function makeTheme() {
  const t = theme.chart;
  return {
    baseTheme: 'ag-default-dark',
    palette: { fills: t.palette, strokes: t.palette },
    overrides: {
      common: {
        background:    { fill: 'transparent' },
        padding:       { top: 8, right: 12, bottom: 8, left: 12 },
        legend: {
          item:     { label: { color: t.foreColor, fontSize: 12 } },
          spacing:  16,
        },
        axes: {
          category: {
            label: { color: t.axisLabel, fontSize: 11 },
            line:  { stroke: t.gridBorder },
            tick:  { stroke: t.gridBorder },
            gridLine: { style: [{ stroke: 'transparent', lineDash: [0] }] },
          },
          number: {
            label: { color: t.axisLabel, fontSize: 11, formatter: ({ value }) => formatNumber(value) },
            line:  { stroke: t.gridBorder },
            tick:  { stroke: t.gridBorder },
            gridLine: { style: [{ stroke: t.gridBorder, lineDash: [0] }] },
          },
          time: {
            label: { color: t.axisLabel, fontSize: 11 },
            line:  { stroke: t.gridBorder },
            tick:  { stroke: t.gridBorder },
          },
        },
        title:    { color: t.foreColor },
        subtitle: { color: t.axisLabel },
        // Tooltip styling — AG renders tooltips via DOM, not canvas; the
        // dark CSS class would override anyway, but these defaults
        // already match the editor's gold-on-dark look.
        tooltip: { delay: 50 },
      },
    },
  };
}


// ── Bar chart ─────────────────────────────────────────────────────────

export function barChartOptions({
  categories, series, height = 260,
  stacked = false, horizontal = false,
  showDataLabels = false,
  distributed = false, colors,
}) {
  const { rows, names, keys } = seriesToRowData(categories, series);
  const palette = (Array.isArray(colors) && colors.length > 0) ? colors : theme.chart.palette;

  // "distributed" mimics ApexCharts: a single series whose bars each pick
  // a different palette color. AG doesn't have a flag for it — we replicate
  // by giving each bar a different fill via itemStyler.
  const opts = {
    data: rows,
    series: names.map((name, i) => ({
      type: 'bar',
      direction: horizontal ? 'horizontal' : 'vertical',
      xKey: '__x',
      yKey: keys[i],
      yName: name,
      stacked,
      cornerRadius: 4,
      label: showDataLabels ? {
        enabled: true,
        color: theme.chart.foreColor,
        fontSize: 11,
        formatter: ({ value }) => formatNumber(value),
      } : { enabled: false },
      ...(distributed && i === 0
        ? { itemStyler: ({ datum }) => ({
              fill:   palette[rows.indexOf(datum) % palette.length],
              stroke: palette[rows.indexOf(datum) % palette.length],
            }) }
        : {}),
      tooltip: {
        renderer: ({ datum, xKey, yKey, yName }) => ({
          heading: String(datum[xKey] ?? ''),
          title:   yName,
          content: formatNumber(datum[yKey]),
        }),
      },
    })),
    axes: [
      { type: horizontal ? 'number' : 'category', position: 'bottom' },
      { type: horizontal ? 'category' : 'number', position: 'left'   },
    ],
    legend: { enabled: names.length > 1, position: 'top' },
    theme: makeTheme(),
    height,
  };
  return opts;
}


// ── Line chart ────────────────────────────────────────────────────────

export function lineChartOptions({
  categories, series, height = 260,
  curve = 'smooth', strokeWidth = 2, showMarkers = false,
}) {
  const { rows, names, keys } = seriesToRowData(categories, series);
  return {
    data: rows,
    series: names.map((name, i) => ({
      type: 'line',
      xKey: '__x',
      yKey: keys[i],
      yName: name,
      interpolation: { type: curve === 'smooth' ? 'smooth' : 'linear' },
      strokeWidth,
      marker: { enabled: showMarkers, size: 6 },
      tooltip: {
        renderer: ({ datum, xKey, yKey, yName }) => ({
          heading: String(datum[xKey] ?? ''),
          title:   yName,
          content: formatNumber(datum[yKey]),
        }),
      },
    })),
    axes: [
      { type: 'category', position: 'bottom' },
      { type: 'number',   position: 'left'   },
    ],
    legend: { enabled: names.length > 1, position: 'top' },
    theme: makeTheme(),
    height,
  };
}


// ── Area chart ────────────────────────────────────────────────────────

export function areaChartOptions({
  categories, series, height = 260,
  curve = 'smooth', strokeWidth = 2, showMarkers = false,
  fillOpacity = 0.45,
}) {
  const { rows, names, keys } = seriesToRowData(categories, series);
  return {
    data: rows,
    series: names.map((name, i) => ({
      type: 'area',
      xKey: '__x',
      yKey: keys[i],
      yName: name,
      interpolation: { type: curve === 'smooth' ? 'smooth' : 'linear' },
      strokeWidth,
      fillOpacity,
      marker: { enabled: showMarkers, size: 6 },
      tooltip: {
        renderer: ({ datum, xKey, yKey, yName }) => ({
          heading: String(datum[xKey] ?? ''),
          title:   yName,
          content: formatNumber(datum[yKey]),
        }),
      },
    })),
    axes: [
      { type: 'category', position: 'bottom' },
      { type: 'number',   position: 'left'   },
    ],
    legend: { enabled: names.length > 1, position: 'top' },
    theme: makeTheme(),
    height,
  };
}


// ── Pie / Donut chart ─────────────────────────────────────────────────

export function pieChartOptions({
  labels, values, donut = false, height = 260,
  legendPosition = 'right', showDataLabels = true,
}) {
  const safeLabels = normalizeLabels(labels);
  const data = safeLabels.map((lab, i) => ({
    label: String(lab ?? ''),
    value: (values || [])[i] ?? 0,
  }));

  const total = data.reduce((acc, r) => acc + (Number(r.value) || 0), 0);

  return {
    data,
    series: [{
      type: donut ? 'donut' : 'pie',
      angleKey: 'value',
      legendItemKey: 'label',
      calloutLabelKey: showDataLabels ? 'label' : undefined,
      sectorLabelKey: showDataLabels ? 'value' : undefined,
      sectorLabel: showDataLabels ? {
        color: theme.chart.foreColor,
        fontSize: 11,
        fontWeight: 500,
        formatter: ({ value }) => formatNumber(value),
      } : { enabled: false },
      calloutLabel: showDataLabels ? {
        color: theme.chart.axisLabel,
        fontSize: 11,
      } : { enabled: false },
      ...(donut ? {
        innerRadiusRatio: 0.6,
        innerLabels: total > 0 ? [
          { text: 'Toplam', fontSize: 11, color: theme.chart.axisLabel },
          { text: formatNumber(total), fontSize: 20, fontWeight: 700, color: theme.chart.foreColor },
        ] : [],
      } : {}),
      strokes: [theme.colors.bgCard],
      strokeWidth: 1,
      tooltip: {
        renderer: ({ datum }) => ({
          heading: String(datum.label ?? ''),
          content: formatNumber(datum.value),
        }),
      },
    }],
    legend: { enabled: true, position: legendPosition },
    theme: makeTheme(),
    height,
  };
}


// ── Heatmap (enterprise) ──────────────────────────────────────────────
// Input mirrors the Apex shape:
//   categories: x-axis labels, e.g. ['Mon', 'Tue', ...]
//   series:     [{name: 'AM', values: [...]}, {name: 'PM', values: [...]}]
// AG Charts wants long-format data (one row per cell), so we melt.

export function heatmapOptions({ categories, series, height = 280, showValues = true }) {
  const x = normalizeLabels(categories);
  const ySeries = series.map((s) => s.name || '');
  const data = [];
  series.forEach((s, j) => {
    (s.values || []).forEach((v, i) => {
      data.push({ x: x[i], y: ySeries[j], value: v ?? null });
    });
  });

  return {
    data,
    series: [{
      type: 'heatmap',
      xKey: 'x',
      yKey: 'y',
      colorKey: 'value',
      label: {
        enabled: showValues,
        color: theme.chart.foreColor,
        fontSize: 10,
        formatter: ({ value }) => formatNumber(value),
      },
      tooltip: {
        renderer: ({ datum }) => ({
          heading: `${datum.x} · ${datum.y}`,
          content: formatNumber(datum.value),
        }),
      },
    }],
    gradientLegend: { enabled: true, position: 'right' },
    axes: [
      { type: 'category', position: 'bottom' },
      { type: 'category', position: 'left'   },
    ],
    theme: makeTheme(),
    height,
  };
}


// ── Radial bar / gauge (enterprise) ───────────────────────────────────
// We use AG Charts' radial-gauge here. It naturally shows a single value
// against a scale and supports gradient fills + center labels — a good
// match for the previous Apex radialBar (which was a gauge too).

export function radialBarOptions({ value, max = 100, label = '', height = 260 }) {
  const pct = max > 0 ? Math.round((Number(value) / max) * 100) : 0;
  const palette = theme.chart.palette;
  return {
    type: 'radial-gauge',
    value: Number(value) || 0,
    scale: {
      min: 0,
      max,
      label: { enabled: false },
    },
    bar: {
      enabled: true,
      fill: palette[0],
      strokeWidth: 0,
    },
    secondaryLabel: {
      text: label || `Hedefin %${pct}`,
      color: theme.chart.axisLabel,
      fontSize: 12,
    },
    label: {
      formatter: ({ value: v }) => formatNumber(v),
      color: theme.chart.foreColor,
      fontSize: 26,
      fontWeight: 700,
    },
    theme: makeTheme(),
    height,
  };
}
