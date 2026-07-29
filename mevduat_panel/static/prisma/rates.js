/* ═══════════════════════════════════════════════════════════════════════════
   rates.js — Rezervasyon Oranları (R5: eski static/js/rates.js paritesi).

   Hesap mantığı ESKİ SAYFADAN BİREBİR portlanmıştır; sayı üreten her fonksiyon
   (saat kovaları, kümülatif/saatlik ağırlıklı ortalama, spread, dağılım,
   Ekstrem Yetki mod çizgisi, metrik oranları) kaynaktaki karşılığıyla aynı
   formülü kullanır. Değişen tek şey kabuk: veri /api/reservations/oranlar/
   <date>'ten gelir, filtreler Outstanding kitiyle (bubfilter.js) çizilir,
   renkler PRISMA token'larından okunur.

   Kaynak eşlemesi (static/js/rates.js):
     groupRowsByHour · calculateDistribution · generateHourlyWeightedAvg ·
     generateCumulativeWeightedAvg · generateCumulativeSpread ·
     generateSpreadSeries · generateCumulativeCount · generateHourlyCount ·
     getFixedValue · calculateStats · updateChartsWithData

   Oran agregasyonları DAİMA tutar ağırlıklıdır — platform kuralı: rate asla
   düz mean() ile alınmaz (kaynak da ağırlıklı hesaplıyordu).
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
  //: Eski varsayılanlar: yalnız 32-35 vade ve yalnız Son Revize seçili.
  var VADE_DEFAULT = '32-35';

  var DIM = { SRC: 'Kaynak', AMT: 'Tutar', VADE: 'Vade', CUST: 'Müşteri Tipi',
              REV: 'Revize' };

  var meta = {};      // {boyut: [değer]}
  var state = {};     // {boyut: {değer: bool}}
  var merges = {};    // {boyut: [{name, members}]}

  var rawRows = [];
  var cache = {};     // {tarih: satırlar} — eski clientCache deseni
  var availableDates = [];

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

  function calculateDistribution(rows, rateKey) {
    var distMap = {};
    rows.forEach(function (row) {
      var rate = row[rateKey];
      if (rate === null || rate === undefined || rate === 0) return;
      var label = parseFloat(rate).toFixed(2);
      distMap[label] = (distMap[label] || 0) + 1;
    });
    var sorted = Object.keys(distMap).sort(function (a, b) {
      return parseFloat(a) - parseFloat(b);
    });
    return { labels: sorted, data: sorted.map(function (r) { return distMap[r]; }) };
  }

  function generateHourlyWeightedAvg(hours, buckets, valKey, weightKey) {
    return hours.map(function (h) {
      var rows = buckets[h] || [], num = 0, den = 0;
      rows.forEach(function (row) {
        var val = row[valKey] || 0, w = row[weightKey] || 0;
        if (val > 0) { num += val * w; den += w; }
      });
      return den > 0 ? parseFloat((num / den).toFixed(2)) : null;
    });
  }

  function generateCumulativeWeightedAvg(hours, buckets, valKey, weightKey) {
    var out = [], num = 0, den = 0;
    hours.forEach(function (h) {
      (buckets[h] || []).forEach(function (row) {
        var val = row[valKey] || 0, w = row[weightKey] || 0;
        if (val > 0) { num += val * w; den += w; }
      });
      out.push(den > 0 ? parseFloat((num / den).toFixed(2)) : 0);
    });
    return out;
  }

  /* Kümülatif spread: her saatte o saatin MARKET_MAX_RT'si referans alınır
     (kaynakta da kovanın ilk satırından okunur). */
  function generateCumulativeSpread(hours, buckets, valKey, weightKey) {
    var out = [], num = 0, den = 0;
    hours.forEach(function (h) {
      var rows = buckets[h] || [];
      var marketMax = rows.length ? (parseFloat(rows[0].MARKET_MAX_RT) || 0) : 0;
      rows.forEach(function (row) {
        var val = parseFloat(row[valKey]) || 0, w = parseFloat(row[weightKey]) || 0;
        if (val > 0 && w > 0) { num += val * w; den += w; }
      });
      out.push(den > 0 ? parseFloat((num / den - marketMax).toFixed(2)) : null);
    });
    return out;
  }

  /* Saatlik "spread" serisi — kaynakta marketMax okunur ama düşülmez; dönen
     değer ağırlıklı ortalamanın kendisidir. Davranış birebir korunmuştur. */
  function generateSpreadSeries(hours, buckets, weightKey) {
    return hours.map(function (h) {
      var rows = buckets[h] || [], num = 0, den = 0;
      rows.forEach(function (row) {
        var val = parseFloat(row.OFFERED_RATE) || 0;
        var w = parseFloat(row[weightKey]) || 0;
        if (val > 0 && w > 0) { num += val * w; den += w; }
      });
      return parseFloat((den > 0 ? num / den : 0).toFixed(2));
    });
  }

  function generateCumulativeCount(hours, buckets) {
    var out = [], running = 0;
    hours.forEach(function (h) {
      running += (buckets[h] || []).length;
      out.push(running);
    });
    return out;
  }

  function generateHourlyCount(hours, buckets) {
    return hours.map(function (h) { return (buckets[h] || []).length; });
  }

  /* Ekstrem Yetki: gün boyunca en sık görülen değer (mod), tüm saatlere düz
     çizgi olarak yayılır — kaynakla birebir. */
  function getFixedValue(hours, buckets, key) {
    var counts = {}, mode = null, maxCount = 0;
    hours.forEach(function (h) {
      (buckets[h] || []).forEach(function (r) {
        var val = parseFloat(r[key]);
        if (!isNaN(val) && val > 0) counts[val] = (counts[val] || 0) + 1;
      });
    });
    for (var val in counts) {
      if (counts[val] > maxCount) { maxCount = counts[val]; mode = parseFloat(val); }
    }
    return hours.map(function () { return mode; });
  }

  // ── Filtre boyutlarını veriden kur ───────────────────────────────────────

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
    // "MAX" = Son Revize (eski panelde de ayrı bir seçenekti).
    meta[DIM.REV] = ['MAX'].concat(revs);

    // Varsayılanlar yalnız İLK kurulumda yazılır; kullanıcının seçimi korunur.
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
    // Sabit listelerde (tutar/vade/revize) verilen sıra anlamlıdır — bant
    // sıralamasına bırakılmaz; diğer boyutlar Türkçe alfabetik.
    MVP.renderBubFilters('fDims', meta, state, merges, apply, {
      sort: function (dim, vals) {
        var fixed = null;
        if (dim === DIM.AMT) fixed = AMOUNT_BUCKETS;
        else if (dim === DIM.VADE) fixed = VADE_RANGES;
        else if (dim === DIM.REV) fixed = meta[DIM.REV];
        if (fixed) {
          return fixed.filter(function (v) { return vals.indexOf(v) >= 0; });
        }
        return vals.slice().sort(function (a, b) {
          return String(a).localeCompare(String(b), 'tr');
        });
      }
    });
    // Kutu etiketlerini okunur adlara çevir — state ham değerle çalışmaya
    // devam eder, yalnız görünen metin değişir.
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
      var match = false;
      if (revSel.indexOf('MAX') >= 0 &&
          (row.IS_MAX_REVIZE === true || row.IS_MAX_REVIZE === 1)) match = true;
      if (revSel.indexOf(String(row.TALEP_REVIZE_NO)) >= 0) match = true;
      return match;
    });
  }

  // ── İstatistikler (eski calculateStats birebir) ───────────────────────────

  function calculateStats(rows) {
    var buckets = groupRowsByHour(rows);
    var hours = Object.keys(buckets).sort();

    var safeRatio = function (num, den) { return den > 0 ? (num / den) * 100 : 0; };
    var validOff = 0, extYetkiOff = 0, extOff = 0;
    var validDema = 0, extYetkiDema = 0, extDema = 0;
    var validComp = 0, extYetkiComp = 0, extComp = 0;

    rows.forEach(function (row) {
      var offered = parseFloat(row.OFFERED_RATE);
      var demanded = parseFloat(row.PERCENTILE_DEMANDED_RTS);
      var comp = parseFloat(row.PERCENTILE_COMPETITOR_RTS);
      var ey = parseFloat(row.EKSTREM_YETKI);
      var ek = parseFloat(row.EKSTREM);

      if (!isNaN(offered) && offered > 0) {
        validOff++;
        if (!isNaN(ey) && offered > ey) extYetkiOff++;
        if (!isNaN(ek) && offered > ek) extOff++;
      }
      if (!isNaN(demanded) && demanded > 0) {
        validDema++;
        if (!isNaN(ey) && demanded > ey) extYetkiDema++;
        if (!isNaN(ek) && demanded > ek) extDema++;
      }
      if (!isNaN(comp) && comp > 0) {
        validComp++;
        if (!isNaN(ey) && comp > ey) extYetkiComp++;
        if (!isNaN(ek) && comp > ek) extComp++;
      }
    });

    return {
      labels: hours,
      dataEkstrem: getFixedValue(hours, buckets, 'EKSTREM_YETKI'),
      dataRez: generateCumulativeCount(hours, buckets),
      dataOff: generateCumulativeWeightedAvg(hours, buckets, 'OFFERED_RATE', 'RESERVATION_AMT'),
      dataDema: generateCumulativeWeightedAvg(hours, buckets, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT'),
      dataComp: generateCumulativeWeightedAvg(hours, buckets, 'PERCENTILE_COMPETITOR_RTS', 'RESERVATION_AMT'),
      dataRezHourly: generateHourlyCount(hours, buckets),
      dataOffHourly: generateHourlyWeightedAvg(hours, buckets, 'OFFERED_RATE', 'RESERVATION_AMT'),
      dataDemaHourly: generateHourlyWeightedAvg(hours, buckets, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT'),
      dataCompHourly: generateHourlyWeightedAvg(hours, buckets, 'PERCENTILE_COMPETITOR_RTS', 'RESERVATION_AMT'),
      distOff: calculateDistribution(rows, 'OFFERED_RATE'),
      distDema: calculateDistribution(rows, 'PERCENTILE_DEMANDED_RTS'),
      distComp: calculateDistribution(rows, 'PERCENTILE_COMPETITOR_RTS'),
      dataSpreadCurr: generateSpreadSeries(hours, buckets, 'CURRENTAMOUNT'),
      dataSpreadInc: generateSpreadSeries(hours, buckets, 'INCOMING_AMT'),
      dataSpreadCurrCumul: generateCumulativeSpread(hours, buckets, 'OFFERED_RATE', 'CURRENTAMOUNT'),
      dataSpreadIncCumul: generateCumulativeSpread(hours, buckets, 'OFFERED_RATE', 'INCOMING_AMT'),
      metricRatios: {
        extYetkiOff: safeRatio(extYetkiOff, validOff),
        extOff: safeRatio(extOff, validOff),
        extYetkiDema: safeRatio(extYetkiDema, validDema),
        extDema: safeRatio(extDema, validDema),
        extYetkiComp: safeRatio(extYetkiComp, validComp),
        extComp: safeRatio(extComp, validComp)
      }
    };
  }

  // ── Chart kurucuları ─────────────────────────────────────────────────────

  /* Combo: kolon = adet (sol eksen), çizgi = oran (sağ eksen), kesikli =
     Ekstrem Yetki. Eksen/seri yapısı kaynakla birebir; renkler PRISMA
     paletinden okunur (tema flip'inde renderChart yeniden kurar). */
  function comboChart(el, categories, series, rateTitle, rateColor) {
    if (!el) return;
    var countColor = MVP.palette()[0];
    var faint = MVP.token('--ink-faint', '#888888');
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'line', height: 350 },
        colors: [countColor, rateColor, faint],
        stroke: { width: [0, 3, 2], dashArray: [0, 0, 4], curve: 'smooth' },
        plotOptions: { bar: { columnWidth: '50%' } },
        fill: { opacity: [0.75, 1, 0.6] },
        dataLabels: { enabled: false },
        series: series,
        noData: { text: 'Veri yok' },
        xaxis: { type: 'category', categories: categories, tickAmount: 10,
                 labels: { rotate: -45 } },
        yaxis: [
          { seriesName: series[0].name,
            title: { text: 'Adet' },
            labels: { formatter: function (v) { return v == null ? '' : v.toFixed(0); } } },
          { seriesName: rateTitle, opposite: true,
            title: { text: rateTitle + ' (%)' },
            labels: { formatter: function (v) { return v == null ? '' : v.toFixed(2) + '%'; } } },
          { seriesName: rateTitle, opposite: true, show: false }
        ],
        legend: { position: 'top' },
        grid: { strokeDashArray: 4 }
      };
    });
  }

  function distChart(el, categories, data, color) {
    if (!el) return;
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'bar', height: 350 },
        colors: [color],
        plotOptions: { bar: { horizontal: false, columnWidth: '60%' } },
        dataLabels: { enabled: false },
        series: [{ name: 'İşlem Sayısı', data: data }],
        noData: { text: 'Veri yok' },
        xaxis: { categories: categories, title: { text: 'Oran (%)' } },
        yaxis: { title: { text: 'Tekrar Sayısı (Frekans)' } },
        tooltip: { y: { formatter: function (v) { return v + ' işlem'; } } }
      };
    });
  }

  function spreadChart(el, categories, series, title, curve) {
    if (!el) return;
    var pal = MVP.palette();
    MVP.renderChart(el, function () {
      return {
        chart: { type: 'line', height: 350 },
        colors: [pal[0], pal[2]],
        stroke: { width: 3, curve: curve },
        dataLabels: { enabled: false },
        series: series,
        noData: { text: 'Veri yok' },
        xaxis: { type: 'category', categories: categories,
                 labels: { rotate: -45 }, tooltip: { enabled: false } },
        yaxis: { title: { text: title },
                 labels: { formatter: function (v) { return v == null ? '' : v.toFixed(2); } } },
        grid: { strokeDashArray: 4 },
        tooltip: { shared: true, intersect: false,
                   y: { formatter: function (v) { return v == null ? '' : v + ' puan'; } } }
      };
    });
  }

  // ── Çizim (eski updateChartsWithData seri eşlemesi) ───────────────────────

  function draw() {
    var rows = filterRows();
    rows.sort(function (a, b) {
      return String(a.DATE_TIME_STR || '').localeCompare(String(b.DATE_TIME_STR || ''));
    });
    var d = calculateStats(rows);
    var pal = MVP.palette();
    var cOff = pal[5], cDem = pal[8], cComp = pal[2];
    var L = d.labels;
    var $ = function (id) { return document.getElementById(id); };

    comboChart($('chart-offered'), L, [
      { name: 'İşlem Sayısı', type: 'column', data: d.dataRez },
      { name: 'Verilen Oran', type: 'line', data: d.dataOff },
      { name: 'Ekstrem Yetki', type: 'line', data: d.dataEkstrem }
    ], 'Verilen Oran', cOff);

    comboChart($('chart-offered-hourly'), L, [
      { name: 'Saatlik Adet', type: 'column', data: d.dataRezHourly },
      { name: 'Verilen Oran (Saatlik)', type: 'line', data: d.dataOffHourly },
      { name: 'Ekstrem Yetki', type: 'line', data: d.dataEkstrem }
    ], 'Verilen Oran (Saatlik)', cOff);

    distChart($('chart-offered-dist'), d.distOff.labels, d.distOff.data, cOff);

    comboChart($('chart-demanded'), L, [
      { name: 'İşlem Sayısı', type: 'column', data: d.dataRez },
      { name: 'İstenen Oran', type: 'line', data: d.dataDema },
      { name: 'Ekstrem Yetki', type: 'line', data: d.dataEkstrem }
    ], 'İstenen Oran', cDem);

    comboChart($('chart-demanded-hourly'), L, [
      { name: 'Saatlik Adet', type: 'column', data: d.dataRezHourly },
      { name: 'İstenen Oran (Saatlik)', type: 'line', data: d.dataDemaHourly },
      { name: 'Ekstrem Yetki', type: 'line', data: d.dataEkstrem }
    ], 'İstenen Oran (Saatlik)', cDem);

    distChart($('chart-demanded-dist'), d.distDema.labels, d.distDema.data, cDem);

    comboChart($('chart-competitor'), L, [
      { name: 'İşlem Sayısı', type: 'column', data: d.dataRez },
      { name: 'Rakip Oranı', type: 'line', data: d.dataComp },
      { name: 'Ekstrem Yetki', type: 'line', data: d.dataEkstrem }
    ], 'Rakip Oranı', cComp);

    comboChart($('chart-competitor-hourly'), L, [
      { name: 'Saatlik Adet', type: 'column', data: d.dataRezHourly },
      { name: 'Rakip Oranı (Saatlik)', type: 'line', data: d.dataCompHourly },
      { name: 'Ekstrem Yetki', type: 'line', data: d.dataEkstrem }
    ], 'Rakip Oranı (Saatlik)', cComp);

    distChart($('chart-competitor-dist'), d.distComp.labels, d.distComp.data, cComp);

    spreadChart($('chart-spread'), L, [
      { name: 'Roll (Saatlik)', data: d.dataSpreadCurr },
      { name: 'Yeni (Saatlik)', data: d.dataSpreadInc }
    ], 'Saatlik Spread', 'straight');

    spreadChart($('chart-spread-cumulative'), L, [
      { name: 'Roll (Kümülatif)', data: d.dataSpreadCurrCumul },
      { name: 'Yeni (Kümülatif)', data: d.dataSpreadIncCumul }
    ], 'Kümülatif Spread', 'smooth');

    var setMetric = function (id, val) {
      var el = $(id);
      if (el) el.textContent = '%' + val.toFixed(2);
    };
    var m = d.metricRatios;
    setMetric('metric-ekstrem-yetki-off', m.extYetkiOff);
    setMetric('metric-ekstrem-off', m.extOff);
    setMetric('metric-ekstrem-yetki-dem', m.extYetkiDema);
    setMetric('metric-ekstrem-dem', m.extDema);
    setMetric('metric-ekstrem-yetki-comp', m.extYetkiComp);
    setMetric('metric-ekstrem-comp', m.extComp);

    if (elStatus) elStatus.textContent = rows.length + ' işlem listeleniyor.';
    if (elMeta) elMeta.textContent = elDate.value || '—';

    MVP.initCarousels();
  }

  function apply() { draw(); }

  // ── Veri yükleme (eski loadDataForDate + clientCache) ─────────────────────

  function loadDate(dateStr) {
    if (!dateStr) return;
    if (cache[dateStr]) {
      rawRows = cache[dateStr];
      buildMeta(rawRows); renderFilters(); draw();
      return;
    }
    if (elStatus) elStatus.textContent = 'Veri indiriliyor…';
    MVP.fetchJson(EP.rates + dateStr)
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
