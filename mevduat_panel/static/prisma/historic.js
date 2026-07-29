/* ═══════════════════════════════════════════════════════════════════════════
   historic.js — Tarihsel Görünüm (R5: eski static/js/historic.js paritesi).

   Hesap mantığı ESKİ SAYFADAN BİREBİR portlanmıştır: gün/saat kovaları, günlük
   ağırlıklı ortalamalar, gün-içi kümülatif ağırlıklı ortalama, gün-içi
   kümülatif persentiller (P90/P75/P50), günlük mod (Market Max / Ekstrem
   Yetki, boş günde son bilinen değere taşınır) ve günlük toplamlar.

   Kaynak eşlemesi (static/js/historic.js):
     groupRowsByDay · groupRowsByHourFull · generateWeightedAvg ·
     generateIntradayCumulativeWeightedAvg · generateSimpleCumulativePercentile ·
     getDailyFixedValue · generateDailySum · generateHourlyCount ·
     calculateStats · updateChartsWithData

   Veri /api/reservations/historic'ten tek çekimde gelir (son 30 gün); dönem
   filtresi client-side'dır — kaynakta da veri sunucudan gömülüydü.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var MVP = window.MVP;
  var EP = JSON.parse(document.getElementById('mvp-endpoints').textContent);

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
  //: 3. grafik yalnız yeterli işlem gören saatleri gösterir (kaynak eşiği).
  var MIN_HOURLY_COUNT = 10;
  //: Kaynakta Litepicker varsayılanı son 7 gündü.
  var DEFAULT_RANGE_DAYS = 7;

  var DIM = { SRC: 'Kaynak', AMT: 'Tutar', VADE: 'Vade', CUST: 'Müşteri Tipi',
              REV: 'Revize' };

  var meta = {}, state = {}, merges = {};
  var rawRows = [];

  var elStatus = document.getElementById('mvpStatus');
  var elMeta = document.getElementById('mvpMeta');
  var elFrom = document.getElementById('fFrom');
  var elTo = document.getElementById('fTo');
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

  function groupRowsByDay(rows) {
    var buckets = {};
    rows.forEach(function (row) {
      var dateStr = String(row.DATE_TIME_STR || '').split(' ')[0];
      if (!dateStr) return;
      if (!buckets[dateStr]) buckets[dateStr] = [];
      buckets[dateStr].push(row);
    });
    return buckets;
  }

  function groupRowsByHourFull(rows) {
    var buckets = {};
    rows.forEach(function (row) {
      var dt = getSafeDate(row);
      if (isNaN(dt.getTime())) return;
      var pad = function (n) { return String(n).padStart(2, '0'); };
      var key = dt.getFullYear() + '-' + pad(dt.getMonth() + 1) + '-' +
                pad(dt.getDate()) + ' ' + pad(dt.getHours()) + ':00';
      if (!buckets[key]) buckets[key] = [];
      buckets[key].push(row);
    });
    return buckets;
  }

  function generateHourlyCount(keys, buckets) {
    return keys.map(function (h) { return (buckets[h] || []).length; });
  }

  /* Günlük tutar ağırlıklı ortalama. Kaynakta sonuç 0 ise null yazılır
     (grafikte boşluk) — davranış korunur. */
  function generateWeightedAvg(dates, buckets, valKey, weightKey) {
    return dates.map(function (date) {
      var num = 0, den = 0;
      (buckets[date] || []).forEach(function (row) {
        var val = parseFloat(row[valKey]) || 0;
        var w = parseFloat(row[weightKey]) || 0;
        if (val > 0 && w > 0) { num += val * w; den += w; }
      });
      var avg = (den > 0 && num > 0) ? parseFloat((num / den).toFixed(2)) : 0;
      return avg > 0 ? avg : null;
    });
  }

  /* Gün-içi kümülatif ağırlıklı ortalama: gün değişince birikim sıfırlanır. */
  function generateIntradayCumulativeWeightedAvg(keys, buckets, valKey, weightKey) {
    var out = [], currentDay = null, num = 0, den = 0;
    keys.forEach(function (key) {
      var datePart = key.split(' ')[0];
      if (currentDay !== datePart) { currentDay = datePart; num = 0; den = 0; }
      (buckets[key] || []).forEach(function (row) {
        var val = parseFloat(row[valKey]) || 0;
        var w = parseFloat(row[weightKey]) || 0;
        if (val !== 0 && w !== 0) { num += val * w; den += w; }
      });
      out.push(den > 0 ? parseFloat((num / den).toFixed(2)) : null);
    });
    return out;
  }

  /* Gün-içi kümülatif persentil: gün başında liste sıfırlanır, saat ilerledikçe
     birikir; indeks floor((n-1)*p) — kaynakla birebir. */
  function generateSimpleCumulativePercentile(keys, buckets, valKey, percentile) {
    var out = [], currentDay = null, dailyRates = [];
    keys.forEach(function (key) {
      var datePart = key.split(' ')[0];
      if (currentDay !== datePart) { currentDay = datePart; dailyRates = []; }
      (buckets[key] || []).forEach(function (r) {
        var val = parseFloat(r[valKey]);
        if (!isNaN(val) && val > 0) dailyRates.push(val);
      });
      if (!dailyRates.length) { out.push(null); return; }
      dailyRates.sort(function (a, b) { return a - b; });
      out.push(dailyRates[Math.floor((dailyRates.length - 1) * percentile)]);
    });
    return out;
  }

  /* Günlük mod (en sık değer). Modu olmayan anahtarda son bilinen mod taşınır
     — kaynak davranışı (çizgi kopmasın). */
  function getDailyFixedValue(keys, buckets, key) {
    var dailyCounts = {};
    keys.forEach(function (k) {
      var datePart = k.split(' ')[0];
      if (!dailyCounts[datePart]) dailyCounts[datePart] = {};
      (buckets[k] || []).forEach(function (r) {
        var val = parseFloat(r[key]);
        if (!isNaN(val) && val > 0) {
          dailyCounts[datePart][val] = (dailyCounts[datePart][val] || 0) + 1;
        }
      });
    });

    var dailyModes = {};
    Object.keys(dailyCounts).forEach(function (date) {
      var counts = dailyCounts[date], winner = null, maxCount = 0;
      for (var val in counts) {
        if (counts[val] > maxCount) { maxCount = counts[val]; winner = parseFloat(val); }
      }
      dailyModes[date] = winner;
    });

    var out = [], lastKnown = null;
    keys.forEach(function (k) {
      var mode = dailyModes[k.split(' ')[0]];
      if (mode !== undefined && mode !== null) { out.push(mode); lastKnown = mode; }
      else if (lastKnown !== null) out.push(lastKnown);
      else out.push(null);
    });
    return out;
  }

  function generateDailySum(dates, buckets, key) {
    return dates.map(function (date) {
      var sum = 0;
      (buckets[date] || []).forEach(function (row) {
        sum += parseFloat(row[key]) || 0;
      });
      return sum;
    });
  }

  function formatCurrency(value) {
    if (value >= 1000000000) return (value / 1000000000).toFixed(1) + 'B';
    if (value >= 1000000) return (value / 1000000).toFixed(1) + 'M';
    if (value >= 1000) return (value / 1000).toFixed(0) + 'K';
    return value.toFixed(0);
  }

  function formatDailyLabel(str) {
    if (!str) return '';
    var dt = new Date(String(str).replace(/-/g, '/'));
    return dt.toLocaleDateString('tr-TR', { day: '2-digit', month: 'short' });
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
    document.querySelectorAll('#fDims .bub-filter-dd').forEach(function (dd) {
      var dim = dd.dataset.dim;
      dd.querySelectorAll('.bub-filter-dd-opt').forEach(function (opt) {
        var raw = opt.textContent.trim();
        var pretty = labelOf(dim, raw);
        if (pretty !== raw) opt.lastChild.textContent = ' ' + pretty;
      });
    });
  }

  // ── Filtreleme ───────────────────────────────────────────────────────────

  function selected(dim) { return MVP.bubSelected(dim, meta, state, merges); }

  function filterRows() {
    var srcSel = selected(DIM.SRC);
    var amtSel = selected(DIM.AMT);
    var vadeSel = selected(DIM.VADE);
    var custSel = selected(DIM.CUST);
    var revSel = selected(DIM.REV);
    var ccyVal = elCcy.value;
    var from = elFrom.value, to = elTo.value;

    return rawRows.filter(function (row) {
      var day = String(row.DATE_TIME_STR || '').split(' ')[0];
      if (from && day < from) return false;
      if (to && day > to) return false;

      if (srcSel.indexOf(String(row.DATA_SRC || '').trim()) < 0) return false;
      if (ccyVal !== row.CCY_CODE) return false;
      if (amtSel.indexOf(getAmountBucketLabel(row.RESERVATION_AMT || 0)) < 0) return false;

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

  // ── İstatistikler (eski calculateStats birebir) ───────────────────────────

  function calculateStats(rows) {
    var buckets = groupRowsByDay(rows);
    var dates = Object.keys(buckets).sort();
    var hourBuckets = groupRowsByHourFull(rows);
    var hours = Object.keys(hourBuckets).sort();

    return {
      labels: dates,
      labelsHourly: hours,
      marketMax: getDailyFixedValue(dates, buckets, 'MARKET_MAX_RT'),
      ekstrem: getDailyFixedValue(dates, buckets, 'EKSTREM_YETKI'),
      offeredW: generateWeightedAvg(dates, buckets, 'OFFERED_RATE', 'RESERVATION_AMT'),
      compW: generateWeightedAvg(dates, buckets, 'PERCENTILE_COMPETITOR_RTS', 'RESERVATION_AMT'),
      demandW: generateWeightedAvg(dates, buckets, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT'),
      currAmt: generateDailySum(dates, buckets, 'CURRENTAMOUNT'),
      incAmt: generateDailySum(dates, buckets, 'INCOMING_AMT'),
      hourlyEkstrem: getDailyFixedValue(hours, hourBuckets, 'EKSTREM_YETKI'),
      hourlyDemandW: generateIntradayCumulativeWeightedAvg(
        hours, hourBuckets, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT'),
      hourlyP90: generateSimpleCumulativePercentile(
        hours, hourBuckets, 'PERCENTILE_DEMANDED_RTS', 0.90),
      hourlyP75: generateSimpleCumulativePercentile(
        hours, hourBuckets, 'PERCENTILE_DEMANDED_RTS', 0.75),
      hourlyP50: generateSimpleCumulativePercentile(
        hours, hourBuckets, 'PERCENTILE_DEMANDED_RTS', 0.50),
      rezHourly: generateHourlyCount(hours, hourBuckets)
    };
  }

  // ── Chart kurucuları ─────────────────────────────────────────────────────

  function trendLineChart(el, categories, series, dashed) {
    if (!el) return;
    var pal = MVP.palette();
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'line', height: '100%' },
        colors: pal,
        stroke: { width: 2.5, curve: 'smooth',
                  dashArray: dashed || series.map(function () { return 0; }) },
        dataLabels: { enabled: false },
        series: series,
        noData: { text: 'Veri yok' },
        xaxis: { type: 'category', categories: categories,
                 labels: { rotate: -45 } },
        yaxis: { labels: { formatter: function (v) {
          return v == null ? '' : v.toFixed(2) + '%'; } } },
        legend: { position: 'top' },
        grid: { strokeDashArray: 4 },
        tooltip: { shared: true, intersect: false }
      };
    });
  }

  /* Yığılmış hacim kolonları + sağ eksende Market Max çizgisi. */
  function volumeComboChart(el, categories, series) {
    if (!el) return;
    var pal = MVP.palette();
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'line', height: '100%', stacked: true },
        colors: [pal[0], pal[1], pal[5]],
        stroke: { width: [0, 0, 3], curve: 'smooth' },
        plotOptions: { bar: { columnWidth: '70%' } },
        dataLabels: { enabled: false },
        series: series,
        noData: { text: 'Veri yok' },
        xaxis: { type: 'category', categories: categories,
                 labels: { rotate: -45 } },
        yaxis: [
          { seriesName: 'Current Amount', title: { text: 'Hacim' },
            labels: { formatter: function (v) { return formatCurrency(v); } } },
          { seriesName: 'Incoming Amount', show: false },
          { opposite: true, seriesName: 'Market Max', title: { text: 'Market Max (%)' },
            labels: { formatter: function (v) {
              return v == null ? '' : v.toFixed(2) + '%'; } } }
        ],
        legend: { position: 'top' },
        grid: { strokeDashArray: 4 },
        tooltip: { shared: true, intersect: false }
      };
    });
  }

  // ── Çizim (eski updateChartsWithData birebir) ─────────────────────────────

  function draw() {
    var rows = filterRows();
    var d = calculateStats(rows);
    var $ = function (id) { return document.getElementById(id); };
    var dailyLabels = d.labels.map(formatDailyLabel);

    trendLineChart($('chart-trend-rates'), dailyLabels, [
      { name: 'Market Max', data: d.marketMax },
      { name: 'Ekstrem Yetki', data: d.ekstrem },
      { name: 'Offered (W.Avg)', data: d.offeredW },
      { name: 'Competitor (W.Avg)', data: d.compW },
      { name: 'Demanded (W.Avg)', data: d.demandW }
    ]);

    volumeComboChart($('chart-trend-volume'), dailyLabels, [
      { name: 'Current Amount', type: 'column', data: d.currAmt },
      { name: 'Incoming Amount', type: 'column', data: d.incAmt },
      { name: 'Market Max', type: 'line', data: d.marketMax }
    ]);

    // 3. grafik: yalnız ≥10 işlem gören saatler (kaynak eşiği).
    var validLabels = [], fEkstrem = [], fDemand = [], fP90 = [], fP75 = [], fP50 = [];
    d.labelsHourly.forEach(function (label, i) {
      if (d.rezHourly[i] >= MIN_HOURLY_COUNT) {
        validLabels.push(label);
        fEkstrem.push(d.hourlyEkstrem[i]);
        fDemand.push(d.hourlyDemandW[i]);
        fP90.push(d.hourlyP90[i]);
        fP75.push(d.hourlyP75[i]);
        fP50.push(d.hourlyP50[i]);
      }
    });

    trendLineChart($('chart-trend-auth'), validLabels, [
      { name: 'Ekstrem Yetki', data: fEkstrem },
      { name: 'Demanded (W.Avg)', data: fDemand },
      { name: 'P90 (Rate)', data: fP90 },
      { name: 'P75 (Rate)', data: fP75 },
      { name: 'P50 (Median)', data: fP50 }
    ], [0, 0, 5, 5, 5]);

    if (elStatus) elStatus.textContent = rows.length + ' işlem listeleniyor.';
    if (elMeta) {
      elMeta.textContent = (elFrom.value || '—') + ' → ' + (elTo.value || '—');
    }
  }

  function apply() { draw(); }

  // ── Veri yükleme ─────────────────────────────────────────────────────────

  function defaultRange(rows) {
    var days = [];
    rows.forEach(function (r) {
      var d = String(r.DATE_TIME_STR || '').split(' ')[0];
      if (d && days.indexOf(d) < 0) days.push(d);
    });
    days.sort();
    if (!days.length) return;
    MVP.setDateVal(elTo, days[days.length - 1]);
    MVP.setDateVal(elFrom, days[Math.max(0, days.length - DEFAULT_RANGE_DAYS)]);
  }

  if (elStatus) elStatus.textContent = 'Veri indiriliyor…';
  MVP.fetchJson(EP.historic)
    .then(function (rows) {
      rawRows = rows || [];
      defaultRange(rawRows);
      buildMeta(rawRows);
      renderFilters();
      draw();
      MVP.initEmbed();
    })
    .catch(function (err) {
      if (elStatus) elStatus.textContent = 'Veri alınamadı: ' + err.message;
    });

  var elApply = document.getElementById('mvpApply');
  if (elApply) elApply.addEventListener('click', apply);
  MVP.initDatePicker(elFrom);   // R7 — GG.AA.YYYY
  MVP.initDatePicker(elTo);
  elFrom.addEventListener('change', apply);
  elTo.addEventListener('change', apply);
  elCcy.addEventListener('change', apply);
})();
