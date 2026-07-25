/* Tarihsel Görünüm — PRISMA-native sayfa mantığı (Faz S1).
   Veri: /api/reservations/historic (son 30 gün, tek çekim). Dönem filtresi
   client-side; oran serileri tutar ağırlıklı (MVP.wavg). */
(function () {
  'use strict';
  var M = window.MVP;
  var EP = JSON.parse(document.getElementById('mvp-endpoints').textContent);

  var el = {
    from: document.getElementById('fFrom'),
    to: document.getElementById('fTo'),
    src: document.getElementById('fSrc'),
    ccy: document.getElementById('fCcy'),
    cust: document.getElementById('fCust'),
    revMax: document.getElementById('fRevMax'),
    apply: document.getElementById('mvpApply'),
    status: document.getElementById('mvpStatus'),
    meta: document.getElementById('mvpMeta'),
    kpis: document.getElementById('kpis'),
    combo: document.getElementById('chCombo'),
    bands: document.getElementById('chBands'),
    bySource: document.getElementById('chBySource'),
    tblDaily: document.getElementById('tblDaily'),
  };

  var raw = [];
  var srcChips = null;
  var onlyMax = false;

  el.revMax.addEventListener('click', function () {
    onlyMax = !onlyMax;
    el.revMax.classList.toggle('is-on', onlyMax);
    draw();
  });
  el.apply.addEventListener('click', draw);
  [el.ccy, el.cust, el.from, el.to].forEach(function (s) {
    s.addEventListener('change', draw);
  });

  function dayOf(r) { return String(r.DATE_TIME_STR || '').slice(0, 10); }

  function filtered() {
    var rows = raw;
    if (onlyMax) rows = rows.filter(function (r) { return r.IS_MAX_REVIZE; });
    var from = el.from.value, to = el.to.value;
    if (from) rows = rows.filter(function (r) { return dayOf(r) >= from; });
    if (to) rows = rows.filter(function (r) { return dayOf(r) <= to; });
    return M.applyFilters(rows, {
      DATA_SRC: srcChips ? srcChips.get() : [],
      CCY_CODE: el.ccy.value ? [el.ccy.value] : [],
      CUST_TP: el.cust.value ? [el.cust.value] : [],
    });
  }

  /** Gün bazında agregasyon — oranlar tutar ağırlıklı. */
  function daily(rows) {
    var byDay = new Map();
    rows.forEach(function (r) {
      var d = dayOf(r);
      if (!d) return;
      if (!byDay.has(d)) byDay.set(d, []);
      byDay.get(d).push(r);
    });
    return Array.from(byDay.keys()).sort().map(function (d) {
      var list = byDay.get(d);
      return {
        day: d,
        adet: list.length,
        tutar: M.sum(list, 'RESERVATION_AMT'),
        teklif: M.wavg(list, 'OFFERED_RATE', 'RESERVATION_AMT'),
        talep: M.wavg(list, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT'),
        rakip: M.wavg(list, 'PERCENTILE_COMPETITOR_RTS', 'RESERVATION_AMT'),
        piyasa: M.wavg(list, 'MARKET_MAX_RT', 'RESERVATION_AMT'),
        rows: list,
      };
    });
  }

  function draw() {
    var rows = filtered();
    var d = daily(rows);
    el.meta.textContent = d.length + ' gün · ' + rows.length + ' rezervasyon';
    drawKpis(rows, d);
    if (!d.length) {
      [el.combo, el.bands, el.bySource, el.tblDaily].forEach(M.showEmpty);
      return;
    }
    drawCombo(d);
    drawBands(d);
    drawBySource(rows);
    drawTable(d);
  }

  function drawKpis(rows, d) {
    el.kpis.innerHTML = '';
    var total = M.sum(rows, 'RESERVATION_AMT');
    var wOffer = M.wavg(rows, 'OFFERED_RATE', 'RESERVATION_AMT');
    var last = d.length ? d[d.length - 1] : null;
    var first = d.length ? d[0] : null;
    var delta = (last && first && last.teklif != null && first.teklif != null)
      ? (M.ratePct(last.teklif) - M.ratePct(first.teklif)) : null;
    M.kpi(el.kpis, 'Gün', String(d.length), '', 'seçili dönem');
    M.kpi(el.kpis, 'Rezervasyon', String(rows.length), 'adet',
          onlyMax ? 'yalnız son revizyon' : 'tüm revizyonlar');
    M.kpi(el.kpis, 'Toplam Tutar', M.formatAmt(total), '', 'dönem toplamı');
    M.kpi(el.kpis, 'Dönem Ağırlıklı Teklif', M.formatRate(wOffer), '', 'Σ(tutar×oran)/Σtutar');
    M.kpi(el.kpis, 'Dönem İçi Değişim',
          delta == null ? '—' : (delta > 0 ? '+' : '') + delta.toFixed(2), 'puan',
          'son gün − ilk gün');
  }

  function drawCombo(d) {
    var cats = d.map(function (x) { return x.day; });
    var vol = d.map(function (x) { return Math.round(x.tutar); });
    var rate = d.map(function (x) {
      var p = M.ratePct(x.teklif);
      return p == null ? null : Number(p.toFixed(2));
    });
    M.renderChart(el.combo, function () {
      return {
        series: [
          { name: 'Tutar', type: 'column', data: vol },
          { name: 'Ağırlıklı Teklif', type: 'line', data: rate },
        ],
        chart: { type: 'line', height: 320 },
        plotOptions: { bar: { columnWidth: '55%', borderRadius: 0 } },
        stroke: { width: [0, 2.5], curve: 'smooth' },
        markers: { size: 0 },
        xaxis: { categories: cats, labels: { rotate: -45, hideOverlappingLabels: true } },
        yaxis: [
          { seriesName: 'Tutar',
            labels: { formatter: function (v) { return M.formatNumber(v); } } },
          { opposite: true, seriesName: 'Ağırlıklı Teklif',
            labels: { formatter: function (v) { return v == null ? '' : v.toFixed(1) + '%'; } } },
        ],
        tooltip: {
          shared: true, intersect: false,
          y: {
            formatter: function (v, o) {
              if (v == null) return '—';
              return o && o.seriesIndex === 1 ? v.toFixed(2) + '%' : M.formatNumber(v);
            },
          },
        },
      };
    });
  }

  function drawBands(d) {
    var cats = d.map(function (x) { return x.day; });
    function serie(key) {
      return d.map(function (x) {
        var p = M.ratePct(x[key]);
        return p == null ? null : Number(p.toFixed(2));
      });
    }
    M.renderChart(el.bands, function () {
      return {
        series: [
          { name: 'Teklif', data: serie('teklif') },
          { name: 'Talep', data: serie('talep') },
          { name: 'Piyasa Max', data: serie('piyasa') },
        ],
        chart: { type: 'line', height: 300 },
        stroke: { width: 2, curve: 'smooth', dashArray: [0, 0, 4] },
        markers: { size: 0 },
        xaxis: { categories: cats, labels: { rotate: -45, hideOverlappingLabels: true } },
        yaxis: { labels: { formatter: function (v) { return v == null ? '' : v.toFixed(1) + '%'; } } },
        tooltip: { shared: true,
                   y: { formatter: function (v) { return v == null ? '—' : v.toFixed(2) + '%'; } } },
      };
    });
  }

  function drawBySource(rows) {
    var days = Array.from(new Set(rows.map(dayOf))).filter(Boolean).sort();
    var bySrc = M.groupBy(rows, 'DATA_SRC');
    var series = [];
    bySrc.forEach(function (list, name) {
      var counts = new Map();
      list.forEach(function (r) {
        var d = dayOf(r);
        counts.set(d, (counts.get(d) || 0) + 1);
      });
      series.push({ name: name, data: days.map(function (d) { return counts.get(d) || 0; }) });
    });
    M.renderChart(el.bySource, function () {
      return {
        series: series,
        chart: { type: 'area', height: 300, stacked: true },
        stroke: { width: 1.5, curve: 'smooth' },
        fill: { type: 'solid', opacity: 0.25 },
        markers: { size: 0 },
        xaxis: { categories: days, labels: { rotate: -45, hideOverlappingLabels: true } },
        yaxis: { labels: { formatter: function (v) { return Math.round(v); } } },
      };
    });
  }

  function drawTable(d) {
    M.renderTable(el.tblDaily, [
      { header: 'Gün', value: function (r) { return r.day; } },
      { header: 'Adet', value: function (r) { return r.adet; } },
      { header: 'Tutar', value: function (r) { return M.formatAmt(r.tutar); } },
      { header: 'Teklif', value: function (r) { return M.formatRate(r.teklif); } },
      { header: 'Talep', value: function (r) { return M.formatRate(r.talep); } },
      { header: 'Rakip', value: function (r) { return M.formatRate(r.rakip); } },
      { header: 'Piyasa Max', value: function (r) { return M.formatRate(r.piyasa); } },
    ], d.slice().reverse());
  }

  // ── Başlangıç ───────────────────────────────────────────────────────────
  M.setStatus(el.status, 'Yükleniyor…');
  M.fetchJson(EP.historic)
    .then(function (rows) {
      raw = rows || [];
      if (!raw.length) {
        M.setStatus(el.status, 'Veri yok', true);
        M.showEmpty(el.kpis, 'Son 30 günde rezervasyon verisi bulunamadı.');
        return;
      }
      var days = Array.from(new Set(raw.map(dayOf))).filter(Boolean).sort();
      el.from.min = el.to.min = days[0];
      el.from.max = el.to.max = days[days.length - 1];
      el.from.value = days[0];
      el.to.value = days[days.length - 1];
      srcChips = M.chipGroup(el.src, M.distinct(raw, 'DATA_SRC'), draw);
      M.fillSelect(el.ccy, M.distinct(raw, 'CCY_CODE'));
      M.fillSelect(el.cust, M.distinct(raw, 'CUST_TP'));
      M.setStatus(el.status, raw.length + ' kayıt');
      draw();
    })
    .catch(function (e) {
      M.setStatus(el.status, 'Hata: ' + e.message, true);
      M.showError(el.kpis, 'Veri alınamadı — ' + e.message);
    });
})();
