// Shared ApexCharts options for the Treasury presentations editor.
// Mirrors the look used in reference/static/js/competitor.js so charts feel
// native to the platform.
//
// Only the presentation *charts* use ApexCharts. The data_table block keeps
// using AG Grid (see DataTable.jsx) — AG Grid is the table engine, ApexCharts
// is the plotting engine.
import { theme } from '../theme.js';

export function formatNumber(v) {
  if (v == null || isNaN(v)) return '';
  const abs = Math.abs(v);
  if (abs >= 1e9)  return (v / 1e9).toFixed(1) + 'B';
  if (abs >= 1e6)  return (v / 1e6).toFixed(1) + 'M';
  if (abs >= 1e3)  return (v / 1e3).toFixed(1) + 'K';
  if (abs < 1 && v !== 0) return v.toFixed(2);
  return Math.round(v).toString();
}

// LLM sometimes emits objects like [{date: "2025-01-01"}, ...] instead of plain
// strings for x_axis / categories. Coerce defensively so the chart still
// renders something sensible. (The validator will also reject such patches
// going forward, but legacy manifests may already contain them.)
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

const COMMON_AXIS_STYLE = {
  style: { fontSize: '11px', colors: theme.chart.axisLabel },
};

const COMMON_GRID = {
  borderColor: theme.chart.gridBorder,
  strokeDashArray: 0,
  yaxis: { lines: { show: true } },
  xaxis: { lines: { show: false } },
  padding: { left: 8, right: 8, top: 0, bottom: 0 },
};

// Phase 11.dark-editor — shared chart-level config. `theme.mode: 'dark'`
// makes Apex's auto-derived colors (tooltips, dataLabels bg, etc.) match
// the dark palette. `foreColor` sets the default text color for legends
// and other global text bits.
const CHART_BASE = {
  toolbar:     { show: false },
  fontFamily:  'inherit',
  background:  'transparent',
  foreColor:   theme.chart.foreColor,
  animations:  { enabled: true, speed: 250 },
};
const CHART_THEME = { theme: { mode: 'dark' } };
const COMMON_TOOLTIP = {
  theme: 'dark',
  style: { fontSize: '12px' },
};

export function barChartOptions({
  categories, series, height = 260,
  stacked = false, horizontal = false,
  showDataLabels = false, borderRadius = 4,
  distributed = false, colors,
}) {
  return {
    chart: {
      type: 'bar',
      height,
      stacked,
      ...CHART_BASE,
    },
    ...CHART_THEME,
    plotOptions: {
      bar: {
        horizontal,
        columnWidth: '55%',
        barHeight:   '70%',
        borderRadius,
        borderRadiusApplication: stacked ? 'end' : 'around',
        distributed,
      },
    },
    dataLabels: {
      enabled: showDataLabels,
      formatter: showDataLabels ? formatNumber : undefined,
      style: { fontSize: '11px' },
    },
    stroke: { show: true, width: 2, colors: ['transparent'] },
    xaxis: {
      type: horizontal ? 'numeric' : 'category',
      categories,
      labels: horizontal ? { ...COMMON_AXIS_STYLE, formatter: formatNumber } : COMMON_AXIS_STYLE,
      axisBorder: { color: theme.chart.gridBorder },
      axisTicks:  { color: theme.chart.gridBorder },
    },
    yaxis: {
      labels: horizontal ? COMMON_AXIS_STYLE : { ...COMMON_AXIS_STYLE, formatter: formatNumber },
    },
    tooltip: { y: { formatter: formatNumber } },
    legend: { show: series.length > 1, position: 'top', fontSize: '12px' },
    colors: Array.isArray(colors) && colors.length > 0 ? colors : theme.chart.palette,
    grid: COMMON_GRID,
    noData: { text: 'Veri bulunamadı', style: { color: theme.chart.axisLabel } },
  };
}

export function lineChartOptions({
  categories, series, height = 260,
  curve = 'smooth', strokeWidth = 2, showMarkers = false,
}) {
  return {
    chart: {
      type: 'line',
      height,
      zoom: { enabled: false },
      ...CHART_BASE,
    },
    ...CHART_THEME,
    stroke: { width: strokeWidth, curve },
    dataLabels: { enabled: false },
    markers: { size: showMarkers ? 4 : 0, hover: { size: 6 } },
    xaxis: {
      type: 'category',
      categories,
      labels: { ...COMMON_AXIS_STYLE, rotate: 0 },
      axisBorder: { color: theme.chart.gridBorder },
      axisTicks:  { color: theme.chart.gridBorder },
    },
    yaxis: {
      labels: { ...COMMON_AXIS_STYLE, formatter: formatNumber },
    },
    tooltip: { shared: true, intersect: false, y: { formatter: formatNumber } },
    legend: { show: series.length > 1, position: 'top', fontSize: '12px' },
    colors: theme.chart.palette,
    grid: COMMON_GRID,
    noData: { text: 'Veri bulunamadı', style: { color: theme.chart.axisLabel } },
  };
}

export function areaChartOptions({
  categories, series, height = 260,
  curve = 'smooth', strokeWidth = 2, showMarkers = false,
  fillOpacity = 0.45,
}) {
  const opts = lineChartOptions({
    categories, series, height, curve, strokeWidth, showMarkers,
  });
  opts.chart.type = 'area';
  opts.fill = {
    type: 'gradient',
    gradient: {
      opacityFrom: fillOpacity,
      opacityTo:   Math.max(0, fillOpacity - 0.4),
      shadeIntensity: 0.3,
    },
  };
  return opts;
}

// ── Combo chart (dual-axis: bars + lines) ─────────────────────────────
// Single query, column-split. Each series carries kind ('bar'|'line') and
// axis ('left'|'right'). We emit an ApexCharts mixed chart: the data series
// each set their own `type`, and we build (at most) two y-axes — one per
// side. Each side's axis binds ALL of its series via an array `seriesName`
// so they share a single scale (callers MUST pass unique, non-empty series
// names so the binding resolves — see ComboChart.jsx).
export function comboChartOptions({
  categories, series, height = 260,
  leftTitle = '', rightTitle = '',
  curve = 'smooth', strokeWidth = 2, showMarkers = false,
  stacked = false, showDataLabels = false,
}) {
  const leftNames  = series.filter((s) => s.axis !== 'right').map((s) => s.name);
  const rightNames = series.filter((s) => s.axis === 'right').map((s) => s.name);

  // ApexCharts reads `yaxis[i].title.text` unguarded — `title` MUST stay an
  // object (never undefined), or it throws "Cannot read properties of
  // undefined (reading 'text')". Empty text just renders no title.
  const makeYAxis = (names, opposite, titleText) => ({
    seriesName: names,
    opposite,
    labels: { ...COMMON_AXIS_STYLE, formatter: formatNumber },
    title: { text: titleText || '', style: { color: theme.chart.axisLabel, fontSize: '11px', fontWeight: 500 } },
  });

  const yaxis = [];
  if (leftNames.length)  yaxis.push(makeYAxis(leftNames,  false, leftTitle));
  if (rightNames.length) yaxis.push(makeYAxis(rightNames, true,  rightTitle));
  if (!yaxis.length) {
    yaxis.push({ labels: { ...COMMON_AXIS_STYLE, formatter: formatNumber }, title: { text: '' } });
  }

  // Per-series stroke: line series get the configured width, bars get 0.
  const strokeWidths = series.map((s) => (s.kind === 'line' ? strokeWidth : 0));
  // Data labels only make sense on bars in a combo — enable per bar-series.
  const barIndexes = series
    .map((s, i) => (s.kind === 'line' ? -1 : i))
    .filter((i) => i >= 0);

  return {
    chart: {
      type: 'line',          // base type; each series overrides via its own `type`
      height,
      stacked,
      ...CHART_BASE,
    },
    ...CHART_THEME,
    plotOptions: {
      bar: {
        columnWidth: '55%',
        borderRadius: 4,
        borderRadiusApplication: stacked ? 'end' : 'around',
      },
    },
    stroke: { width: strokeWidths, curve },
    markers: { size: showMarkers ? 4 : 0, hover: { size: 6 } },
    dataLabels: {
      enabled: showDataLabels,
      enabledOnSeries: showDataLabels ? barIndexes : undefined,
      formatter: showDataLabels ? formatNumber : undefined,
      style: { fontSize: '11px' },
    },
    xaxis: {
      type: 'category',
      categories,
      labels: COMMON_AXIS_STYLE,
      axisBorder: { color: theme.chart.gridBorder },
      axisTicks:  { color: theme.chart.gridBorder },
    },
    yaxis,
    tooltip: { shared: true, intersect: false, y: { formatter: formatNumber } },
    legend: { show: series.length > 1, position: 'top', fontSize: '12px' },
    colors: theme.chart.palette,
    grid: COMMON_GRID,
    noData: { text: 'Veri bulunamadı', style: { color: theme.chart.axisLabel } },
  };
}

export function pieChartOptions({
  labels, donut = false, height = 260,
  legendPosition = 'right', showDataLabels = true,
}) {
  return {
    chart: {
      type: donut ? 'donut' : 'pie',
      height,
      ...CHART_BASE,
    },
    ...CHART_THEME,
    labels,
    colors: theme.chart.palette,
    legend: { position: legendPosition, fontSize: '12px' },
    dataLabels: {
      enabled: showDataLabels,
      style: { fontSize: '11px', fontWeight: 500 },
      dropShadow: { enabled: false },
    },
    tooltip: { y: { formatter: formatNumber } },
    plotOptions: donut
      ? { pie: { donut: { size: '60%', labels: { show: true, total: { show: true, label: 'Toplam', formatter: formatNumber } } } } }
      : {},
    stroke: { width: 1, colors: [theme.colors.bgCard] },
    noData: { text: 'Veri bulunamadı', style: { color: theme.chart.axisLabel } },
  };
}

// Delta (±) içeren heatmap'ler için ıraksak renk skalası: negatif = adaçayı
// (maliyet düşüşü), pozitif = terracotta (artış). Tek işaretli veri eski
// tek-ton gölgelemede kalır — bakiye/adet heatmap'leri değişmez.
const HEAT_NEG = ['#93B297', '#7A9B7E', '#5F8265'];
const HEAT_POS = ['#CBA490', '#B8826B', '#9E6B52'];

function _divergingRanges(series) {
  const vals = [];
  for (const s of series || []) {
    for (const d of s.data || []) {
      const y = Number(d && d.y);
      if (Number.isFinite(y)) vals.push(y);
    }
  }
  if (!vals.length) return null;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  if (!(min < 0 && max > 0)) return null;   // tek işaret → varsayılan gölgeleme
  const ranges = [];
  const negStep = min / HEAT_NEG.length;    // negatif: min→0, koyudan açığa
  for (let i = 0; i < HEAT_NEG.length; i++) {
    ranges.push({
      from: min - negStep * i,
      to: i === HEAT_NEG.length - 1 ? 0 : min - negStep * (i + 1),
      color: HEAT_NEG[HEAT_NEG.length - 1 - i],
    });
  }
  const posStep = max / HEAT_POS.length;
  for (let i = 0; i < HEAT_POS.length; i++) {
    ranges.push({
      from: i === 0 ? 1e-9 : posStep * i,
      to: posStep * (i + 1),
      color: HEAT_POS[i],
    });
  }
  return ranges;
}

export function heatmapOptions({ categories, series, height = 280, showValues = true }) {
  const ranges = _divergingRanges(series);
  return {
    chart: {
      type: 'heatmap',
      height,
      ...CHART_BASE,
    },
    ...CHART_THEME,
    dataLabels: { enabled: showValues, style: { fontSize: '10px' } },
    colors: [theme.chart.palette[0]],
    xaxis: {
      type: 'category',
      categories,
      labels: COMMON_AXIS_STYLE,
    },
    yaxis: { labels: COMMON_AXIS_STYLE },
    plotOptions: {
      heatmap: {
        radius: 4,
        enableShades: !ranges,
        shadeIntensity: 0.5,
        useFillColorAsStroke: false,
        ...(ranges ? { colorScale: { ranges } } : {}),
      },
    },
    tooltip: { y: { formatter: formatNumber } },
    legend: { show: false },
    grid: COMMON_GRID,
    noData: { text: 'Veri bulunamadı', style: { color: theme.chart.axisLabel } },
  };
}

export function radialBarOptions({ value, max = 100, label = '', height = 260 }) {
  // ApexCharts radialBar takes percentages; we normalize against `max`.
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return {
    chart: {
      type: 'radialBar',
      height,
      ...CHART_BASE,
    },
    ...CHART_THEME,
    series: [pct],
    plotOptions: {
      radialBar: {
        startAngle: -135,
        endAngle: 135,
        track: { background: theme.chart.gridBorder, strokeWidth: '100%' },
        dataLabels: {
          name: { fontSize: '12px', color: theme.chart.axisLabel, offsetY: -10 },
          value: {
            fontSize: '28px',
            fontWeight: 700,
            color: theme.chart.foreColor,
            formatter: () => formatNumber(value),
          },
        },
        hollow: { size: '58%' },
      },
    },
    fill: {
      type: 'gradient',
      gradient: { shade: 'light', shadeIntensity: 0.4, gradientToColors: [theme.chart.palette[1]], stops: [0, 100] },
    },
    colors: [theme.chart.palette[0]],
    stroke: { lineCap: 'round' },
    labels: [label || `Hedefin %${pct}`],
    noData: { text: 'Veri bulunamadı', style: { color: theme.chart.axisLabel } },
  };
}

// ── Waterfall (Apex rangeBar) ────────────────────────────────────────────
// Girdi: categories + values (adım deltaları) + totals (bar kümülatiften
// bağımsız 0'dan çizilir). Kümülatif burada hesaplanır; config'te yalnız
// deltalar durur (NIM dashboard'unun renderWaterfall sözleşmesiyle aynı ruh).
export function waterfallSeries({ categories, values, totals }) {
  const data = [];
  let run = 0;
  for (let i = 0; i < categories.length; i++) {
    const v = Number(values[i] ?? 0);
    const isTotal = !!(totals && totals[i]);
    let y0;
    let y1;
    if (isTotal) {
      y0 = 0; y1 = v; run = v;
    } else {
      y0 = run; y1 = run + v; run = y1;
    }
    data.push({
      x: String(categories[i]),
      y: [Math.min(y0, y1), Math.max(y0, y1)],
      _delta: v,
      _total: isTotal,
      fillColor: isTotal
        ? theme.chart.waterfallTotal ?? '#d4a574'
        : v >= 0
          ? theme.chart.waterfallUp ?? '#5b8a72'
          : theme.chart.waterfallDown ?? '#b05c5c',
    });
  }
  return [{ name: 'Δ', data }];
}

export function waterfallOptions({ height = 280, unit = '', showDataLabels = true, data = null }) {
  // Kaynak dashboard'un y_floor davranışı: köprü yüksek bir taban değer
  // (ör. ~4500 bps) etrafında ±küçük deltalarla oynuyorsa, 0'dan başlayan
  // eksen deltaları görünmez kılar. Kümülatif aralık tabandan kopuksa ekseni
  // aralığa kırp; 0'a yakın köprülerde (bakiye köprüsü gibi) dokunma.
  let yMin;
  let yMax;
  if (Array.isArray(data) && data.length) {
    let lo = Infinity;
    let hi = -Infinity;
    for (const p of data) {
      if (!p || !Array.isArray(p.y)) continue;
      lo = Math.min(lo, p.y[0], p.y[1]);
      hi = Math.max(hi, p.y[0], p.y[1]);
    }
    const span = hi - lo;
    if (Number.isFinite(span) && lo > 0 && span < lo) {
      const pad = Math.max(span * 0.15, 1e-9);
      yMin = lo - pad;
      yMax = hi + pad;
    }
  }
  return {
    chart: { ...CHART_BASE, type: 'rangeBar', height },
    ...CHART_THEME,
    plotOptions: { bar: { horizontal: false, borderRadius: 3, rangeBarOverlap: true } },
    dataLabels: {
      enabled: showDataLabels,
      formatter: (_v, { w, seriesIndex, dataPointIndex }) => {
        const p = w.config.series[seriesIndex].data[dataPointIndex];
        const d = p && p._delta;
        return typeof d === 'number' ? formatNumber(d) : '';
      },
      style: { fontSize: '10px' },
    },
    xaxis: { labels: { ...COMMON_AXIS_STYLE, rotate: -35, rotateAlways: false } },
    yaxis: { min: yMin, max: yMax, labels: { ...COMMON_AXIS_STYLE, formatter: formatNumber } },
    grid: COMMON_GRID,
    legend: { show: false },
    tooltip: {
      ...COMMON_TOOLTIP,
      custom: ({ w, seriesIndex, dataPointIndex }) => {
        const p = w.config.series[seriesIndex].data[dataPointIndex];
        if (!p) return '';
        const kind = p._total ? 'Toplam' : 'Δ';
        return `<div style="padding:6px 10px">${p.x}<br/><b>${kind}: ${formatNumber(p._delta)}${unit ? ' ' + unit : ''}</b></div>`;
      },
    },
  };
}

// ── Scatter / Bubble ─────────────────────────────────────────────────────
// Her nokta kendi serisi → legend + ayrık renk (dashboard bubble'ları gibi).
export function scatterSeries(points) {
  return (points || []).map((p, i) => ({
    name: p.name || `Nokta ${i + 1}`,
    data: [[Number(p.x) || 0, Number(p.y) || 0,
            Math.max(0.01, Number(p.size) || 1)]],
  }));
}

// Eksen aralığını veriden pay bırakarak sabitle: Apex bubble, yarıçapı eksen
// aralığına katmaz — uçlardaki büyük baloncuklar plot alanının dışına
// taşıyordu. Yarıçap ayrıca maxBubbleRadius ile sınırlanır, böylece boyut
// kolonu ham ₺M değeri de olsa (ör. 70.000) baloncuk plot'a sığar.
function _paddedRange(values) {
  if (!values.length) return {};
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const span = Math.max(hi - lo, Math.max(Math.abs(hi), Math.abs(lo)) * 0.1, 1e-6);
  return { min: lo - span * 0.18, max: hi + span * 0.18 };
}

export function scatterOptions({ height = 300, xTitle = '', yTitle = '',
                                 showDataLabels = false, points = [] }) {
  const xr = _paddedRange(points.map((p) => Number(p.x) || 0));
  const yr = _paddedRange(points.map((p) => Number(p.y) || 0));
  return {
    chart: { ...CHART_BASE, type: 'bubble', height, zoom: { enabled: false } },
    ...CHART_THEME,
    dataLabels: { enabled: showDataLabels },
    fill: { opacity: 0.75 },
    plotOptions: { bubble: { minBubbleRadius: 4, maxBubbleRadius: 24 } },
    xaxis: {
      min: xr.min,
      max: xr.max,
      tickAmount: 6,
      labels: { ...COMMON_AXIS_STYLE, formatter: formatNumber },
      title: { text: xTitle, style: { fontSize: '11px' } },
    },
    yaxis: {
      min: yr.min,
      max: yr.max,
      labels: { ...COMMON_AXIS_STYLE, formatter: formatNumber },
      title: { text: yTitle, style: { fontSize: '11px' } },
    },
    grid: COMMON_GRID,
    // Derin gruplamada nokta sayısı büyür — legend liste plot'u ezmesin.
    legend: { show: points.length <= 14, position: 'bottom', fontSize: '11px' },
    tooltip: { ...COMMON_TOOLTIP },
  };
}
