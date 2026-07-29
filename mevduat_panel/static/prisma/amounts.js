/* ═══════════════════════════════════════════════════════════════════════════
   amounts.js — Rezervasyon Miktarları (R5: eski static/js/amounts.js paritesi).

   Hesap mantığı ESKİ SAYFADAN BİREBİR portlanmıştır: saat kovaları, kümülatif
   ve saatlik hacim/adet serileri, tutar bandı dağılımları (hacim + adet) ve
   500k binli frekans histogramı. Değişen tek şey kabuk: veri
   /api/reservations/miktarlar/<date>'ten gelir, filtreler Outstanding kitiyle
   (bubfilter.js) çizilir, renkler PRISMA token'larından okunur.

   Kaynak eşlemesi (static/js/amounts.js):
     groupRowsByHour · getDistributionBucket · calculateDistributionStats ·
     calculateHistogramStats · formatCurrency · calculateAllStats ·
     updateTimeSeriesChart · updatePieChart · updateHistogram
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var MVP = window.MVP;
  var EP = JSON.parse(document.getElementById('mvp-endpoints').textContent);

  // ── Filtre boyutları — eski base.html filtre panelinin karşılığı ──────────
  var AMOUNT_BUCKETS = ['0-5', '5-10', '10-25', '25-100', '100-200',
                        '200-500', '500-1000', '1000+'];
  var AMOUNT_LABELS = {
    '0-5': '0-5 Mn', '5-10': '5-10 Mn', '10-25': '10-25 Mn',
    '25-100': '25-100 Mn', '100-200': '100-200 Mn', '200-500': '200-500 Mn',
    '500-1000': '500 Mn - 1 Mr', '1000+': '1 Mr +'
  };
  var VADE_RANGES = ['1-3', '4-31', '32-35', '36-45', '46-60', '61-91',
                     '92-365', '366-10000'];
  var VADE_LABELS = {
    '1-3': '1 - 3 Gün', '4-31': '4 - 31 Gün', '32-35': '32 - 35 Gün',
    '36-45': '36 - 45 Gün', '46-60': '46 - 60 Gün', '61-91': '61 - 91 Gün',
    '92-365': '92 - 365 Gün', '366-10000': '366+ Gün'
  };
  var VADE_DEFAULT = '32-35';

  var DIM = { SRC: 'Kaynak', AMT: 'Tutar', VADE: 'Vade', CUST: 'Müşteri Tipi',
              REV: 'Revize' };

  //: Dağılım bantları — kaynakla birebir (grafik ekseni bu sıraya bağlı).
  var DIST_LABELS = ['0-5M', '5-10M', '10-25M', '25-100M', '100-200M',
                     '200-500M', '500-1000M', '1000M+'];
  var HIST_BIN = 500000;

  var meta = {}, state = {}, merges = {};
  var rawRows = [], cache = {}, availableDates = [];

  var elStatus = document.getElementById('mvpStatus');
  var elMeta = document.getElementById('mvpMeta');
  var elDate = document.getElementById('fDate');
  var elCcy = document.getElementById('fCcy');

  // ── Eski yardımcılar (birebir) ───────────────────────────────────────────

  function getSafeDate(row) {
    return new Date(String(row.DATE_TIME_STR || '').replace(/-/g, '/'));
  }

  function getAmountBucketLabel(amt) {
    if (amt <= 5000000) return '0-5';
    if (amt <= 10000000) return '5-10';
    if (amt <= 25000000) return '10-25';
    if (amt <= 100000000) return '25-100';
    if (amt <= 200000000) return '100-200';
    if (amt <= 500000000) return '200-500';
    if (amt <= 1000000000) return '500-1000';
    return '1000+';
  }

  function getDistributionBucket(amt) {
    var val = parseFloat(amt || 0);
    if (val <= 5000000) return '0-5M';
    if (val <= 10000000) return '5-10M';
    if (val <= 25000000) return '10-25M';
    if (val <= 100000000) return '25-100M';
    if (val <= 200000000) return '100-200M';
    if (val <= 500000000) return '200-500M';
    if (val <= 1000000000) return '500-1000M';
    return '1000M+';
  }

  function formatCurrency(value) {
    if (value >= 1000000000) return (value / 1000000000).toFixed(1) + 'B';
    if (value >= 1000000) return (value / 1000000).toFixed(1) + 'M';
    if (value >= 1000) return (value / 1000).toFixed(0) + 'K';
    return value.toFixed(0);
  }

  function parseCurrencyStr(str) {
    if (str.indexOf('B') >= 0) return parseFloat(str) * 1000000000;
    if (str.indexOf('M') >= 0) return parseFloat(str) * 1000000;
    if (str.indexOf('K') >= 0) return parseFloat(str) * 1000;
    return parseFloat(str);
  }

  function groupRowsByHour(rows) {
    var buckets = {};
    rows.forEach(function (row) {
      var dt = getSafeDate(row);
      if (isNaN(dt.getTime())) return;
      var hour = dt.getHours();
      var label = (hour < 10 ? '0' + hour : hour) + ':00';
      if (!buckets[label]) buckets[label] = [];
      buckets[label].push(row);
    });
    return buckets;
  }

  /* Bant bazlı hacim + adet dağılımı. Sıfır tutarlı satırlar atlanır (kaynak
     davranışı: 0 bir banda düşmez). */
  function calculateDistributionStats(rows, colName) {
    var volMap = {}, countMap = {};
    DIST_LABELS.forEach(function (l) { volMap[l] = 0; countMap[l] = 0; });
    rows.forEach(function (r) {
      var val = parseFloat(r[colName] || 0);
      if (val === 0) return;
      var bucket = getDistributionBucket(val);
      volMap[bucket] += val;
      countMap[bucket] += 1;
    });
    return {
      labels: DIST_LABELS.slice(),
      volSeries: DIST_LABELS.map(function (l) { return volMap[l]; }),
      countSeries: DIST_LABELS.map(function (l) { return countMap[l]; })
    };
  }

  /* 500k binli frekans histogramı; etiketler "1.0M - 1.5M" biçiminde ve
     sayısal alt sınıra göre sıralanır (kaynakla birebir). */
  function calculateHistogramStats(rows, colName) {
    var freqMap = {};
    rows.forEach(function (r) {
      var val = parseFloat(r[colName] || 0);
      if (val === 0) return;
      var binIndex = Math.floor(val / HIST_BIN);
      var label = formatCurrency(binIndex * HIST_BIN) + ' - ' +
                  formatCurrency((binIndex + 1) * HIST_BIN);
      freqMap[label] = (freqMap[label] || 0) + 1;
    });
    var entries = Object.keys(freqMap).map(function (k) { return [k, freqMap[k]]; });
    entries.sort(function (a, b) {
      return parseCurrencyStr(a[0].split(' - ')[0]) -
             parseCurrencyStr(b[0].split(' - ')[0]);
    });
    return {
      categories: entries.map(function (e) { return e[0]; }),
      data: entries.map(function (e) { return e[1]; })
    };
  }

  // ── Filtre boyutları ─────────────────────────────────────────────────────

  function buildMeta(rows) {
    var srcs = [], custs = [], revs = [];
    rows.forEach(function (r) {
      var s = String(r.DATA_SRC || '').trim();
      if (s && srcs.indexOf(s) < 0) srcs.push(s);
      var c = String(r.CUST_TP || '').trim();
      if (c && custs.indexOf(c) < 0) custs.push(c);
      var rn = String(r.TALEP_REVIZE_NO);
      if (rn && rn !== 'null' && rn !== 'undefined' && revs.indexOf(rn) < 0) revs.push(rn);
    });
    revs.sort(function (a, b) { return parseFloat(a) - parseFloat(b); });

    meta = {};
    meta[DIM.SRC] = srcs.sort();
    meta[DIM.AMT] = AMOUNT_BUCKETS.slice();
    meta[DIM.VADE] = VADE_RANGES.slice();
    meta[DIM.CUST] = custs.sort();
    meta[DIM.REV] = ['MAX'].concat(revs);

    if (!state[DIM.VADE]) {
      state[DIM.VADE] = {};
      VADE_RANGES.forEach(function (v) { state[DIM.VADE][v] = (v === VADE_DEFAULT); });
    }
    if (!state[DIM.REV]) {
      state[DIM.REV] = {};
      meta[DIM.REV].forEach(function (v) { state[DIM.REV][v] = (v === 'MAX'); });
    }
  }

  function labelOf(dim, v) {
    if (dim === DIM.AMT) return AMOUNT_LABELS[v] || v;
    if (dim === DIM.VADE) return VADE_LABELS[v] || v;
    if (dim === DIM.REV) return v === 'MAX' ? 'Son Revize' : 'Revize ' + v;
    return v;
  }

  function renderFilters() {
    MVP.renderBubFilters('fDims', meta, state, merges, apply, {
      sort: function (dim, vals) {
        var fixed = null;
        if (dim === DIM.AMT) fixed = AMOUNT_BUCKETS;
        else if (dim === DIM.VADE) fixed = VADE_RANGES;
        else if (dim === DIM.REV) fixed = meta[DIM.REV];
        if (fixed) return fixed.filter(function (v) { return vals.indexOf(v) >= 0; });
        return vals.slice().sort(function (a, b) {
          return String(a).localeCompare(String(b), 'tr');
        });
      }
    });
    document.querySelectorAll('#fDims .pkf-dd').forEach(function (dd) {
      var dim = dd.dataset.dim;
      dd.querySelectorAll('.pkf-dd-opt').forEach(function (opt) {
        var raw = opt.textContent.trim();
        var pretty = labelOf(dim, raw);
        if (pretty !== raw) opt.lastChild.textContent = ' ' + pretty;
      });
    });
  }

  // ── Filtreleme (eski processAndDisplay birebir) ───────────────────────────

  function selected(dim) { return MVP.bubSelected(dim, meta, state, merges); }

  function filterRows() {
    var srcSel = selected(DIM.SRC);
    var amtSel = selected(DIM.AMT);
    var vadeSel = selected(DIM.VADE);
    var custSel = selected(DIM.CUST);
    var revSel = selected(DIM.REV);
    var ccyVal = elCcy.value;

    return rawRows.filter(function (row) {
      if (srcSel.indexOf(String(row.DATA_SRC || '').trim()) < 0) return false;
      if (ccyVal !== row.CCY_CODE) return false;
      // Kaynakta boş tutar seçimi "tümü" demekti — davranış korunur.
      if (amtSel.length &&
          amtSel.indexOf(getAmountBucketLabel(row.RESERVATION_AMT || 0)) < 0) return false;

      if (!vadeSel.length) return false;
      var rowVade = parseInt(row.VADE_BASLANGIC || 0, 10);
      var inRange = vadeSel.some(function (r) {
        var p = String(r).split('-').map(Number);
        return rowVade >= p[0] && rowVade <= p[1];
      });
      if (!inRange) return false;

      if (custSel.indexOf(String(row.CUST_TP || '').trim()) < 0) return false;

      if (!revSel.length) return false;
      var matchesMax = revSel.indexOf('MAX') >= 0 &&
                       (row.IS_MAX_REVIZE === true || row.IS_MAX_REVIZE === 1);
      var matchesRev = revSel.indexOf(String(row.TALEP_REVIZE_NO)) >= 0;
      return matchesMax || matchesRev;
    });
  }

  // ── İstatistikler (eski calculateAllStats birebir) ────────────────────────

  function calculateAllStats(rows) {
    var buckets = groupRowsByHour(rows);
    var hours = Object.keys(buckets).sort();

    var cumRoll = [], cumNew = [], cumCount = [];
    var hourlyRoll = [], hourlyNew = [], hourlyCount = [];
    var runRoll = 0, runNew = 0, runCount = 0;

    hours.forEach(function (h) {
      var hRoll = 0, hNew = 0, hCount = 0;
      (buckets[h] || []).forEach(function (r) {
        hRoll += parseFloat(r.CURRENTAMOUNT || 0);
        hNew += parseFloat(r.INCOMING_AMT || 0);
        hCount++;
      });
      hourlyRoll.push(hRoll); hourlyNew.push(hNew); hourlyCount.push(hCount);
      runRoll += hRoll; runNew += hNew; runCount += hCount;
      cumRoll.push(runRoll); cumNew.push(runNew); cumCount.push(runCount);
    });

    return {
      timeLabels: hours,
      cumRoll: cumRoll, cumNew: cumNew, cumCount: cumCount,
      hourlyRoll: hourlyRoll, hourlyNew: hourlyNew, hourlyCount: hourlyCount,
      distCurrent: calculateDistributionStats(rows, 'CURRENTAMOUNT'),
      distIncoming: calculateDistributionStats(rows, 'INCOMING_AMT'),
      distPortfolio: calculateDistributionStats(rows, 'PORTFOLIO_AMT'),
      histCurrent: calculateHistogramStats(rows, 'CURRENTAMOUNT'),
      histIncoming: calculateHistogramStats(rows, 'INCOMING_AMT'),
      histPortfolio: calculateHistogramStats(rows, 'PORTFOLIO_AMT')
    };
  }

  // ── Chart kurucuları ─────────────────────────────────────────────────────

  /* Yığılmış hacim kolonları (Roll + Yeni, aynı grup) + sağ eksende adet
     çizgisi. İki hacim ekseni ortak `max` ile hizalanır — kaynakla birebir. */
  function timeSeriesChart(el, labels, rollData, newData, countData) {
    if (!el) return;
    var pal = MVP.palette();
    var maxVol = 0;
    if (rollData.length) {
      maxVol = Math.max.apply(null, rollData.map(function (v, i) {
        return v + (newData[i] || 0);
      }));
    }
    var yMax = maxVol > 0 ? maxVol * 1.1 : 100;

    MVP.renderChart(el, function () {
      return {
        chart: { type: 'line', height: 400, stacked: true },
        colors: [pal[0], pal[1], pal[5]],
        stroke: { width: [0, 0, 4], curve: 'smooth' },
        plotOptions: { bar: { columnWidth: '70%' } },
        dataLabels: { enabled: false },
        series: [
          { name: 'Roll Hacim', type: 'column', group: 'vol', data: rollData },
          { name: 'Yeni Hacim', type: 'column', group: 'vol', data: newData },
          { name: 'İşlem Adedi', type: 'line', data: countData }
        ],
        xaxis: { type: 'category', categories: labels, tooltip: { enabled: false } },
        yaxis: [
          { seriesName: 'Roll Hacim', max: yMax, title: { text: 'Hacim' },
            labels: { formatter: function (v) { return formatCurrency(v); } } },
          { seriesName: 'Yeni Hacim', max: yMax, show: false },
          { opposite: true, seriesName: 'İşlem Adedi', title: { text: 'Adet' },
            labels: { formatter: function (v) { return v == null ? '' : v.toFixed(0); } } }
        ],
        legend: { position: 'top' },
        tooltip: { shared: true, intersect: false,
                   y: { formatter: function (v) { return formatCurrency(v); } } },
        noData: { text: 'Veri yok' }
      };
    });
  }

  function pieChart(el, labels, series) {
    if (!el) return;
    var pal = MVP.palette();
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'pie', height: 500 },
        colors: pal,
        labels: labels,
        series: series,
        legend: { position: 'bottom' },
        dataLabels: {
          enabled: true,
          formatter: function (val, opts) {
            return opts.w.globals.series[opts.seriesIndex] > 0
              ? val.toFixed(1) + '%' : '';
          }
        },
        tooltip: {
          y: {
            formatter: function (v) {
              return v > 1000 ? formatCurrency(v) : v.toFixed(0);
            }
          }
        },
        noData: { text: 'Veri yok' }
      };
    });
  }

  function histogramChart(el, categories, data) {
    if (!el) return;
    var pal = MVP.palette();
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'bar', height: 350 },
        colors: [pal[3]],
        plotOptions: { bar: { columnWidth: '90%' } },
        dataLabels: { enabled: false },
        series: [{ name: 'Frekans', data: data }],
        xaxis: { type: 'category', categories: categories,
                 labels: { rotate: -45 },
                 title: { text: 'Tutar Aralığı (500k)' } },
        yaxis: { title: { text: 'Frekans' } },
        tooltip: { y: { formatter: function (v) { return v + ' kez'; } } },
        noData: { text: 'Veri yok' }
      };
    });
  }

  // ── Çizim ────────────────────────────────────────────────────────────────

  function draw() {
    var rows = filterRows();
    rows.sort(function (a, b) {
      return String(a.DATE_TIME_STR || '').localeCompare(String(b.DATE_TIME_STR || ''));
    });
    var s = calculateAllStats(rows);
    var $ = function (id) { return document.getElementById(id); };

    timeSeriesChart($('chart-time-cumulative'), s.timeLabels,
                    s.cumRoll, s.cumNew, s.cumCount);
    timeSeriesChart($('chart-time-hourly'), s.timeLabels,
                    s.hourlyRoll, s.hourlyNew, s.hourlyCount);

    pieChart($('chart-curr-pie-vol'), s.distCurrent.labels, s.distCurrent.volSeries);
    pieChart($('chart-curr-pie-count'), s.distCurrent.labels, s.distCurrent.countSeries);
    histogramChart($('chart-curr-hist'), s.histCurrent.categories, s.histCurrent.data);

    pieChart($('chart-inc-pie-vol'), s.distIncoming.labels, s.distIncoming.volSeries);
    pieChart($('chart-inc-pie-count'), s.distIncoming.labels, s.distIncoming.countSeries);
    histogramChart($('chart-inc-hist'), s.histIncoming.categories, s.histIncoming.data);

    pieChart($('chart-port-pie-vol'), s.distPortfolio.labels, s.distPortfolio.volSeries);
    pieChart($('chart-port-pie-count'), s.distPortfolio.labels, s.distPortfolio.countSeries);
    histogramChart($('chart-port-hist'), s.histPortfolio.categories, s.histPortfolio.data);

    if (elStatus) elStatus.textContent = rows.length + ' işlem listeleniyor.';
    if (elMeta) elMeta.textContent = elDate.value || '—';

    MVP.initCarousels();
  }

  function apply() { draw(); }

  // ── Veri yükleme ─────────────────────────────────────────────────────────

  function loadDate(dateStr) {
    if (!dateStr) return;
    if (cache[dateStr]) {
      rawRows = cache[dateStr];
      buildMeta(rawRows); renderFilters(); draw();
      return;
    }
    if (elStatus) elStatus.textContent = 'Veri indiriliyor…';
    MVP.fetchJson(EP.amounts + dateStr)
      .then(function (rows) {
        cache[dateStr] = rows || [];
        rawRows = cache[dateStr];
        buildMeta(rawRows); renderFilters(); draw();
        _embedOnce();
      })
      .catch(function (err) {
        if (elStatus) elStatus.textContent = 'Veri alınamadı: ' + err.message;
      });
  }

  function shiftDate(delta) {
    if (!availableDates.length) return;
    var idx = availableDates.indexOf(elDate.value);
    if (idx < 0) idx = availableDates.length - 1;
    var next = idx + delta;
    if (next < 0 || next >= availableDates.length) return;
    elDate.value = availableDates[next];
    loadDate(elDate.value);
  }

  var _embedDone = false;
  function _embedOnce() {
    if (_embedDone) return;
    _embedDone = true;
    MVP.initEmbed();
  }

  // ── Başlangıç ────────────────────────────────────────────────────────────

  document.getElementById('mvpApply').addEventListener('click', function () {
    loadDate(elDate.value);
  });
  elDate.addEventListener('change', function () { loadDate(elDate.value); });
  elCcy.addEventListener('change', apply);
  document.getElementById('fDatePrev').addEventListener('click', function () { shiftDate(-1); });
  document.getElementById('fDateNext').addEventListener('click', function () { shiftDate(1); });

  MVP.fetchJson(EP.dates)
    .then(function (res) {
      availableDates = (res && res.dates) || [];
      var latest = (res && res.latest) || availableDates[availableDates.length - 1];
      if (latest) { elDate.value = latest; loadDate(latest); }
      else if (elStatus) elStatus.textContent = 'Veri yok.';
    })
    .catch(function (err) {
      if (elStatus) elStatus.textContent = 'Tarih listesi alınamadı: ' + err.message;
    });
})();
