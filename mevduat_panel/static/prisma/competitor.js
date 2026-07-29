/* ═══════════════════════════════════════════════════════════════════════════
   competitor.js — Rakip Faiz Analizi (R5: eski static/js/competitor.js paritesi).

   Hesap mantığı ESKİ SAYFADAN BİREBİR portlanmıştır: (gün, banka) bazında maks.
   oran, snapshot sıralaması + önceki güne göre değişim, banka bazlı tarihsel
   trend + sektör ortalaması, vade aralığı örtüşme testi ve kaynakça çıkarımı.

   Kaynak eşlemesi (static/js/competitor.js):
     rangesOverlap · bankColor · fmtDate · updateSourceLinks ·
     processAndDisplay (maxByDayBank / snapshot / trend) · buildGroupedGrid

   Tablo AG-Grid ENTERPRISE ile kurulur — banka > vade satır gruplaması
   Community build'de yoktur.

   Piyasa Özeti: kaynak `POST /competitor/summary` LLM ucunu çağırıyordu. O uç
   bu modüle PORTLANMADI (masa modu sıfır-LLM sözleşmesi + modül izolasyonu
   kararı gerektirir). Panel yalnız uç yapılandırılmışsa görünür; aksi halde
   gizlenir — layout paritesi korunur, LLM bağımlılığı dayatılmaz.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var MVP = window.MVP;
  var EP = JSON.parse(document.getElementById('mvp-endpoints').textContent);

  //: Kaynaktaki vade aralıkları (min/max gün) — örtüşme testiyle uygulanır.
  var VADE_RANGES = ['1-31', '32-45', '46-60', '61-91', '92-180', '181-365', '366-10000'];
  var VADE_LABELS = {
    '1-31': '1 - 31 Gün', '32-45': '32 - 45 Gün', '46-60': '46 - 60 Gün',
    '61-91': '61 - 91 Gün', '92-180': '92 - 180 Gün', '181-365': '181 - 365 Gün',
    '366-10000': '366+ Gün'
  };
  var DIM = { VADE: 'Vade', BANK: 'Banka' };
  //: Kaynakta Litepicker varsayılanı son 30 gündü.
  var DEFAULT_RANGE_DAYS = 30;

  var meta = {}, state = {}, merges = {};
  var rawRows = [];
  var gridApi = null;
  var _fbMap = {}, _fbIdx = 0;

  var elStatus = document.getElementById('mvpStatus');
  var elMeta = document.getElementById('mvpMeta');
  var elFrom = document.getElementById('fFrom');
  var elTo = document.getElementById('fTo');
  var elSnapDate = document.getElementById('snapshot-date');
  var elSources = document.getElementById('source-links');
  var elSummaryPanel = document.getElementById('summary-panel');
  var elSummaryText = document.getElementById('summary-text');

  // ── Eski yardımcılar (birebir) ───────────────────────────────────────────

  /* R6 — Banka renkleri ESKİ SİSTEMDEN BİREBİR (kurumsal renkler önemli;
     static/js/competitor.js BANK_COLORS + FALLBACK_PALETTE). Haritada olmayan
     bankalara fallback paletinden deterministik atanır; eşleşme Türkçe
     karakter normalize edilerek büyük/küçük harf duyarsız yapılır. */
  var BANK_COLORS = {
    'DENİZBANK': 'rgb(0, 51, 160)',
    'DENIZBANK': 'rgb(0, 51, 160)',
    'Denizbank': 'rgb(0, 51, 160)',
    'AKBANK': 'rgb(220, 0, 5)',
    'Akbank': 'rgb(220, 0, 5)',
    'VAKIFBANK': 'rgb(253, 185, 19)',
    'VakıfBank': 'rgb(253, 185, 19)',
    'Vakıfbank': 'rgb(253, 185, 19)',
    'YAPI KREDİ': 'rgb(19, 102, 178)',
    'Yapı Kredi': 'rgb(19, 102, 178)',
    'YAPIKREDI': 'rgb(19, 102, 178)',
    'ENPARA': 'rgb(180, 78, 167)',
    'Enpara': 'rgb(180, 78, 167)'
  };
  var FALLBACK_PALETTE = [
    '#2fb344', '#f76707', '#ae3ec9', '#0ca678', '#d6336c',
    '#3bc9db', '#fcc419', '#868e96', '#4263eb', '#f06595',
    '#20c997', '#fab005', '#e64980', '#7048e8', '#15aabf'
  ];

  function bankColor(name) {
    if (!name) return '#868e96';
    if (BANK_COLORS[name]) return BANK_COLORS[name];
    var up = name.toUpperCase().replace(/İ/g, 'I').replace(/Ş/g, 'S');
    for (var k in BANK_COLORS) {
      if (k.toUpperCase().replace(/İ/g, 'I').replace(/Ş/g, 'S') === up) {
        return BANK_COLORS[k];
      }
    }
    if (!_fbMap[name]) {
      _fbMap[name] = FALLBACK_PALETTE[_fbIdx % FALLBACK_PALETTE.length];
      _fbIdx++;
    }
    return _fbMap[name];
  }

  /* Vade filtresi ARALIK ÖRTÜŞMESİdir (kaynakla birebir): satırın [min,max]
     aralığı seçili aralıklardan biriyle kesişiyorsa geçer. Hiç seçim yoksa
     hiçbir satır geçmez. */
  function rangesOverlap(ranges, rMin, rMax) {
    if (!ranges.length) return false;
    return ranges.some(function (r) {
      var p = String(r).split('-').map(Number);
      return rMin <= p[1] && rMax >= p[0];
    });
  }

  function fmtDate(str) {
    if (!str) return '';
    var dt = new Date(String(str).replace(/-/g, '/'));
    if (isNaN(dt.getTime())) return String(str);
    return dt.toLocaleDateString('tr-TR', { day: '2-digit', month: 'short' });
  }

  /* Kaynakça: filtrelenmiş satırların KAYNAK alanından tekil liste. URL ise
     doğrudan bağlanır, alan adı gibiyse https:// eklenir (kaynak davranışı). */
  function updateSourceLinks(rows) {
    if (!elSources) return;
    elSources.innerHTML = '';
    var sources = [];
    rows.forEach(function (row) {
      var k = row.KAYNAK;
      if (k && k !== '0' && k !== 0 && sources.indexOf(String(k)) < 0) {
        sources.push(String(k));
      }
    });
    if (!sources.length) {
      var span = document.createElement('span');
      span.style.cssText = 'font-size:12px;color:var(--text-muted);';
      span.textContent = 'Kaynak bilgisi bulunamadı.';
      elSources.appendChild(span);
      return;
    }
    sources.sort().forEach(function (src) {
      var a = document.createElement('a');
      if (src.indexOf('http://') === 0 || src.indexOf('https://') === 0) {
        a.href = src;
        try { a.textContent = new URL(src).hostname.replace('www.', ''); }
        catch (e) { a.textContent = src; }
      } else {
        a.href = src.indexOf('.') >= 0
          ? 'https://' + src.replace(/^https?:\/\//, '') : '#';
        a.textContent = src;
      }
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.style.cssText = 'display:inline-block;padding:4px 10px;font-size:12px;' +
        'border:1px solid var(--border-mid);border-radius:4px;' +
        'color:var(--text-secondary);text-decoration:none;';
      elSources.appendChild(a);
    });
  }

  // ── Filtre boyutları ─────────────────────────────────────────────────────

  function buildMeta(rows, banks) {
    var found = [];
    rows.forEach(function (r) {
      var b = String(r.BANKA_ADI || '').trim();
      if (b && found.indexOf(b) < 0) found.push(b);
    });
    meta = {};
    meta[DIM.VADE] = VADE_RANGES.slice();
    meta[DIM.BANK] = (banks && banks.length ? banks.slice() : found).sort();
    // Kaynakta tüm bankalar ve tüm vadeler seçili başlıyordu.
  }

  function labelOf(dim, v) {
    return dim === DIM.VADE ? (VADE_LABELS[v] || v) : v;
  }

  function renderFilters() {
    MVP.renderBubFilters('fDims', meta, state, merges, apply, {
      sort: function (dim, vals) {
        if (dim === DIM.VADE) {
          return VADE_RANGES.filter(function (v) { return vals.indexOf(v) >= 0; });
        }
        return vals.slice().sort(function (a, b) {
          return String(a).localeCompare(String(b), 'tr');
        });
      }
    });
    document.querySelectorAll('#fDims .bub-filter-dd').forEach(function (dd) {
      var dim = dd.dataset.dim;
      dd.querySelectorAll('.bub-filter-dd-opt').forEach(function (opt) {
        var raw = opt.textContent.trim();
        var pretty = labelOf(dim, raw);
        if (pretty !== raw) opt.lastChild.textContent = ' ' + pretty;
      });
    });
  }

  function selected(dim) { return MVP.bubSelected(dim, meta, state, merges); }

  function filterRows() {
    var vadeSel = selected(DIM.VADE);
    var bankSel = selected(DIM.BANK);
    var from = elFrom.value, to = elTo.value;

    return rawRows.filter(function (row) {
      if (from && row.DATE_STR < from) return false;
      if (to && row.DATE_STR > to) return false;
      var vMin = parseInt(row.VADE_MIN, 10) || 0;
      var vMax = parseInt(row.VADE_MAX, 10) || 0;
      if (!rangesOverlap(vadeSel, vMin, vMax)) return false;
      if (bankSel.indexOf(String(row.BANKA_ADI || '').trim()) < 0) return false;
      return true;
    });
  }

  // ── Chart kurucuları ─────────────────────────────────────────────────────

  function snapshotBars(el, categories, values, colors, yMin, yMax) {
    if (!el) return;
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'bar', height: '100%', toolbar: { show: false } },
        plotOptions: { bar: { horizontal: false, columnWidth: '55%',
                              distributed: true } },
        colors: colors,
        dataLabels: { enabled: true,
          formatter: function (v) { return v == null ? '' : v.toFixed(2) + '%'; } },
        series: [{ name: 'Maks. Oran (%)', data: values }],
        xaxis: { type: 'category', categories: categories },
        yaxis: { min: yMin, max: yMax,
                 labels: { formatter: function (v) {
                   return v == null ? '' : v.toFixed(1); } } },
        tooltip: { y: { formatter: function (v) {
          return v == null ? '-' : v.toFixed(2) + '%'; } } },
        legend: { show: false },
        noData: { text: 'Veri bulunamadı' }
      };
    });
  }

  function snapshotChange(el, categories, values, colors) {
    if (!el) return;
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'bar', height: '100%', toolbar: { show: false } },
        plotOptions: { bar: { horizontal: false, columnWidth: '55%',
                              distributed: true } },
        colors: colors,
        dataLabels: { enabled: true, formatter: function (v) {
          return v == null ? '' : (v > 0 ? '+' : '') + v.toFixed(2); } },
        series: [{ name: 'Değişim', data: values }],
        xaxis: { type: 'category', categories: categories },
        yaxis: { title: { text: 'Günlük Değişim (bps)' },
                 labels: { formatter: function (v) {
                   return v == null ? '' : v.toFixed(2); } } },
        tooltip: { y: { formatter: function (v) {
          return v == null ? '-' : (v > 0 ? '+' : '') + v.toFixed(2) + ' bps'; } } },
        legend: { show: false },
        noData: { text: 'Değişim verisi yok' }
      };
    });
  }

  /* Sektör ortalaması kalın kesikli, bankalar ince düz — kaynakla birebir. */
  function trendChart(el, categories, series, widths, dashes, colors) {
    if (!el) return;
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'line', height: '100%', zoom: { enabled: true } },
        colors: colors,
        stroke: { width: widths, dashArray: dashes, curve: 'smooth' },
        dataLabels: { enabled: false },
        series: series,
        xaxis: { type: 'category', categories: categories,
                 labels: { rotate: -45 } },
        yaxis: { labels: { formatter: function (v) {
          return v == null ? '' : v.toFixed(2); } } },
        legend: { position: 'top' },
        tooltip: { shared: true, intersect: false },
        noData: { text: 'Veri bulunamadı' }
      };
    });
  }

  // ── AG-Grid (Enterprise: banka > vade satır gruplaması) ──────────────────

  function buildGroupedGrid(rows) {
    var gridDiv = document.getElementById('competitor-grid');
    if (!gridDiv || typeof agGrid === 'undefined') return;

    var rowData = rows.map(function (row) {
      return {
        BANKA_ADI: row.BANKA_ADI || '',
        VADE: row.VADE || '',
        TUTAR: row.TUTAR || '',
        TARIH: row.DATE_STR || '',
        FAIZ: parseFloat(row.FAIZ) || null,
        DOVIZ: row.DOVIZ_CINSI || ''
      };
    });

    var columnDefs = [
      { field: 'BANKA_ADI', headerName: 'Banka', rowGroup: true, hide: true,
        enableRowGroup: true },
      { field: 'VADE', headerName: 'Vade', rowGroup: true, hide: true,
        enableRowGroup: true },
      { field: 'TUTAR', headerName: 'Tutar', enableRowGroup: true },
      { field: 'TARIH', headerName: 'Tarih', sort: 'desc',
        valueFormatter: function (p) { return p.value ? fmtDate(p.value) : ''; },
        enableRowGroup: true },
      { field: 'FAIZ', headerName: 'Faiz (%)', type: 'numericColumn',
        valueFormatter: function (p) {
          return p.value != null ? p.value.toFixed(2) + '%' : '-'; },
        aggFunc: 'max', enableValue: true },
      { field: 'DOVIZ', headerName: 'Döviz', enableRowGroup: true }
    ];

    if (gridApi) { gridApi.destroy(); gridApi = null; gridDiv.innerHTML = ''; }

    gridApi = agGrid.createGrid(gridDiv, {
      columnDefs: columnDefs,
      rowData: rowData,
      defaultColDef: { sortable: true, resizable: true, filter: true,
                       flex: 1, minWidth: 100 },
      autoGroupColumnDef: { headerName: 'Grup', minWidth: 250,
                            cellRendererParams: { suppressCount: false } },
      groupDefaultExpanded: 0,
      rowGroupPanelShow: 'always',
      animateRows: true,
      suppressAggFuncInHeader: true,
      domLayout: 'normal'
    });
  }

  // ── Çizim (eski processAndDisplay birebir) ───────────────────────────────

  function draw() {
    var rows = filterRows();
    var $ = function (id) { return document.getElementById(id); };

    updateSourceLinks(rows);

    // (gün, banka) → maks. oran
    var maxByDayBank = {};
    rows.forEach(function (row) {
      var d = row.DATE_STR, bank = row.BANKA_ADI, rate = parseFloat(row.FAIZ);
      if (!d || !bank || isNaN(rate) || rate <= 0) return;
      if (!maxByDayBank[d]) maxByDayBank[d] = {};
      if (!maxByDayBank[d][bank] || rate > maxByDayBank[d][bank]) {
        maxByDayBank[d][bank] = rate;
      }
    });

    var days = Object.keys(maxByDayBank).sort();
    var bankSet = {};
    days.forEach(function (d) {
      Object.keys(maxByDayBank[d]).forEach(function (b) { bankSet[b] = true; });
    });
    var bankList = Object.keys(bankSet).sort();

    // ── Snapshot: en son gün + önceki güne göre değişim ──
    var latest = days[days.length - 1] || null;
    var prev = days[days.length - 2] || null;
    if (elSnapDate) elSnapDate.textContent = latest ? fmtDate(latest) : '';

    if (latest && maxByDayBank[latest]) {
      var today = maxByDayBank[latest];
      var sorted = Object.keys(today).map(function (b) { return [b, today[b]]; })
        .sort(function (a, b) { return b[1] - a[1]; });

      var labels = sorted.map(function (e) { return e[0]; });
      var values = sorted.map(function (e) { return e[1]; });
      var colors = sorted.map(function (e) { return bankColor(e[0]); });

      var prevRates = prev ? (maxByDayBank[prev] || {}) : {};
      var changes = sorted.map(function (e) {
        var p = prevRates[e[0]];
        return p != null ? parseFloat((e[1] - p).toFixed(2)) : null;
      });

      // Eksen kaynakta ±1 puan pay bırakır — farklar görünür kalsın.
      var yMin = parseFloat((Math.min.apply(null, values) - 1).toFixed(1));
      var yMax = parseFloat((Math.max.apply(null, values) + 1).toFixed(1));

      snapshotBars($('chart-snapshot-bars'), labels, values, colors, yMin, yMax);
      snapshotChange($('chart-snapshot-change'), labels, changes, colors);
    } else {
      snapshotBars($('chart-snapshot-bars'), [], [], [], undefined, undefined);
      snapshotChange($('chart-snapshot-change'), [], [], []);
    }

    // ── Trend: banka serileri + sektör ortalaması ──
    var trendLabels = days.map(fmtDate);
    var series = bankList.map(function (bank) {
      return { name: bank, data: days.map(function (d) {
        return maxByDayBank[d][bank] || null; }) };
    });
    var avg = days.map(function (d) {
      var vals = Object.keys(maxByDayBank[d]).map(function (b) {
        return maxByDayBank[d][b]; });
      if (!vals.length) return null;
      return parseFloat((vals.reduce(function (a, b) { return a + b; }, 0) /
                         vals.length).toFixed(2));
    });
    series.unshift({ name: 'Sektör Ortalaması', data: avg, type: 'line' });

    var colors = [MVP.token('--ink', '#1d273b')].concat(
      bankList.map(function (b) { return bankColor(b); }));
    var widths = [3].concat(bankList.map(function () { return 2; }));
    var dashes = [5].concat(bankList.map(function () { return 0; }));

    trendChart($('chart-trend'), trendLabels, series, widths, dashes, colors);

    buildGroupedGrid(rows);

    if (elStatus) {
      elStatus.textContent = rows.length + ' kayıt' +
        (elFrom.value && elTo.value ? ' (' + elFrom.value + ' – ' + elTo.value + ')' : '');
    }
    if (elMeta) {
      elMeta.textContent = (elFrom.value || '—') + ' → ' + (elTo.value || '—');
    }

    // Piyasa Özeti: uç yapılandırılmamışsa panel gizli kalır (bkz. başlık notu).
    if (elSummaryPanel) elSummaryPanel.hidden = !EP.competitorSummary;
    if (EP.competitorSummary && latest) fetchSummary(latest);
  }

  function fetchSummary(day) {
    if (!elSummaryText) return;
    elSummaryText.textContent = 'Özet hazırlanıyor…';
    MVP.fetchJson(EP.competitorSummary + '?date=' + encodeURIComponent(day))
      .then(function (res) {
        elSummaryText.textContent = (res && res.summary) || 'Özet üretilemedi.';
      })
      .catch(function () {
        if (elSummaryPanel) elSummaryPanel.hidden = true;
      });
  }

  function apply() { draw(); }

  // ── Veri yükleme ─────────────────────────────────────────────────────────

  function defaultRange(rows) {
    var days = [];
    rows.forEach(function (r) {
      if (r.DATE_STR && days.indexOf(r.DATE_STR) < 0) days.push(r.DATE_STR);
    });
    days.sort();
    if (!days.length) return;
    elTo.value = days[days.length - 1];
    elFrom.value = days[Math.max(0, days.length - DEFAULT_RANGE_DAYS)];
  }

  if (elStatus) elStatus.textContent = 'Veri indiriliyor…';
  MVP.fetchJson(EP.competitor)
    .then(function (res) {
      rawRows = (res && res.rows) || [];
      defaultRange(rawRows);
      buildMeta(rawRows, (res && res.banks) || []);
      renderFilters();
      draw();
      MVP.initEmbed();
    })
    .catch(function (err) {
      if (elStatus) elStatus.textContent = 'Veri alınamadı: ' + err.message;
    });

  var elApply = document.getElementById('mvpApply');
  if (elApply) elApply.addEventListener('click', apply);
  elFrom.addEventListener('change', apply);
  elTo.addEventListener('change', apply);

  // ── R6 — Veri kaynağı uyarı popup'ı (eski sourceDisclaimerModal portu) ────
  (function () {
    var overlay = document.getElementById('source-disclaimer');
    var btnClose = document.getElementById('btn-close-disclaimer');
    var countdownSpan = document.getElementById('countdown-val');
    if (!overlay || !btnClose) return;
    var remaining = 3;
    var timer = setInterval(function () {
      remaining--;
      if (countdownSpan) countdownSpan.textContent = remaining;
      if (remaining <= 0) {
        clearInterval(timer);
        btnClose.disabled = false;
        btnClose.textContent = 'Anladım';
      }
    }, 1000);
    btnClose.addEventListener('click', function () { overlay.remove(); });
    // Kaynak linkleri veri gelince aynalanır (kaynaktaki 2 sn bekleme deseni).
    setTimeout(function () {
      var srcEl = document.getElementById('source-links');
      var popupEl = document.getElementById('popup-source-links');
      if (srcEl && popupEl) popupEl.innerHTML = srcEl.innerHTML;
    }, 2000);
  })();

  // LLM özeti disclaimer'ı (kaynak MutationObserver deseni): özet doluysa
  // sonuna açık-kaynak notu eklenir.
  if (elSummaryText) {
    new MutationObserver(function () {
      var text = elSummaryText.textContent || '';
      if (text.length > 30 && text.indexOf('açık kaynaklardan') < 0) {
        elSummaryText.textContent = text.replace(/\s+$/, '') +
          ' Tüm veriler açık kaynaklardan elde edilmiştir.';
      }
    }).observe(elSummaryText, { childList: true, characterData: true, subtree: true });
  }
})();
