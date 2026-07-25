/* Rezervasyon Miktarları — PRISMA-native sayfa mantığı (Faz S1).
   Veri: /api/reservations/miktarlar/<date>. Tutar kalemleri toplanır;
   oran agregasyonu bu sayfada yok. */
(function () {
  'use strict';
  var M = window.MVP;
  var EP = JSON.parse(document.getElementById('mvp-endpoints').textContent);

  var el = {
    date: document.getElementById('fDate'),
    src: document.getElementById('fSrc'),
    ccy: document.getElementById('fCcy'),
    cust: document.getElementById('fCust'),
    revMax: document.getElementById('fRevMax'),
    apply: document.getElementById('mvpApply'),
    status: document.getElementById('mvpStatus'),
    meta: document.getElementById('mvpMeta'),
    kpis: document.getElementById('kpis'),
    byTenor: document.getElementById('chByTenor'),
    bySegment: document.getElementById('chBySegment'),
    bySource: document.getElementById('chBySource'),
    tblTenor: document.getElementById('tblTenor'),
  };

  var raw = [];
  var srcChips = null;
  var onlyMax = false;

  el.revMax.addEventListener('click', function () {
    onlyMax = !onlyMax;
    el.revMax.classList.toggle('is-on', onlyMax);
    draw();
  });
  el.apply.addEventListener('click', load);
  [el.ccy, el.cust].forEach(function (s) { s.addEventListener('change', draw); });

  function filtered() {
    var rows = raw;
    if (onlyMax) rows = rows.filter(function (r) { return r.IS_MAX_REVIZE; });
    return M.applyFilters(rows, {
      DATA_SRC: srcChips ? srcChips.get() : [],
      CCY_CODE: el.ccy.value ? [el.ccy.value] : [],
      CUST_TP: el.cust.value ? [el.cust.value] : [],
    });
  }

  function load() {
    var d = el.date.value;
    if (!d) return;
    M.setStatus(el.status, 'Yükleniyor…');
    M.fetchJson(EP.amounts + d)
      .then(function (rows) {
        raw = rows || [];
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
  }

  function draw() {
    var rows = filtered();
    el.meta.textContent = (el.date.value || '—') + ' · ' + rows.length + ' rezervasyon';
    drawKpis(rows);
    if (!rows.length) {
      [el.byTenor, el.bySegment, el.bySource, el.tblTenor].forEach(M.showEmpty);
      return;
    }
    drawByTenor(rows);
    drawDonut(el.bySegment, rows, 'CUST_TP');
    drawDonut(el.bySource, rows, 'DATA_SRC');
    drawTable(rows);
  }

  function drawKpis(rows) {
    el.kpis.innerHTML = '';
    var total = M.sum(rows, 'RESERVATION_AMT');
    var incoming = M.sum(rows, 'INCOMING_AMT');
    var current = M.sum(rows, 'CURRENTAMOUNT');
    var portfolio = M.sum(rows, 'PORTFOLIO_AMT');
    M.kpi(el.kpis, 'Rezervasyon', String(rows.length), 'adet',
          onlyMax ? 'yalnız son revizyon' : 'tüm revizyonlar');
    M.kpi(el.kpis, 'Toplam Rezervasyon', M.formatAmt(total), '', 'RESERVATION_AMT');
    M.kpi(el.kpis, 'Gelen Tutar', M.formatAmt(incoming), '', 'INCOMING_AMT');
    M.kpi(el.kpis, 'Mevcut Bakiye', M.formatAmt(current), '', 'CURRENTAMOUNT');
    M.kpi(el.kpis, 'Portföy', M.formatAmt(portfolio), '', 'PORTFOLIO_AMT');
  }

  function drawByTenor(rows) {
    var cats = M.bandSort(M.distinct(rows, 'VADE_BASLANGIC'));
    var g = M.groupBy(rows, 'VADE_BASLANGIC');
    function serie(key) {
      return cats.map(function (c) { return Math.round(M.sum(g.get(c) || [], key)); });
    }
    M.renderChart(el.byTenor, function () {
      return {
        series: [
          { name: 'Rezervasyon', data: serie('RESERVATION_AMT') },
          { name: 'Gelen', data: serie('INCOMING_AMT') },
        ],
        chart: { type: 'bar', height: 300 },
        plotOptions: { bar: { columnWidth: '62%', borderRadius: 0 } },
        stroke: { show: true, width: 1, colors: ['transparent'] },
        xaxis: { categories: cats },
        yaxis: { labels: { formatter: function (v) { return M.formatNumber(v); } } },
        tooltip: { y: { formatter: function (v) { return M.formatNumber(v); } } },
      };
    });
  }

  function drawDonut(node, rows, key) {
    var g = M.groupBy(rows, key);
    var labels = [], values = [];
    g.forEach(function (list, name) {
      labels.push(name);
      values.push(Math.round(M.sum(list, 'RESERVATION_AMT')));
    });
    M.renderChart(node, function () {
      return {
        series: values,
        labels: labels,
        chart: { type: 'donut', height: 300 },
        legend: { position: 'bottom' },
        plotOptions: { pie: { donut: { size: '62%' } } },
        tooltip: { y: { formatter: function (v) { return M.formatNumber(v); } } },
      };
    });
  }

  function drawTable(rows) {
    var cats = M.bandSort(M.distinct(rows, 'VADE_BASLANGIC'));
    var g = M.groupBy(rows, 'VADE_BASLANGIC');
    var data = cats.map(function (c) {
      var list = g.get(c) || [];
      return {
        vade: c,
        adet: list.length,
        rez: M.sum(list, 'RESERVATION_AMT'),
        gelen: M.sum(list, 'INCOMING_AMT'),
        mevcut: M.sum(list, 'CURRENTAMOUNT'),
        portfoy: M.sum(list, 'PORTFOLIO_AMT'),
      };
    });
    M.renderTable(el.tblTenor, [
      { header: 'Vade', value: function (r) { return r.vade; } },
      { header: 'Adet', value: function (r) { return r.adet; } },
      { header: 'Rezervasyon', value: function (r) { return M.formatAmt(r.rez); } },
      { header: 'Gelen', value: function (r) { return M.formatAmt(r.gelen); } },
      { header: 'Mevcut', value: function (r) { return M.formatAmt(r.mevcut); } },
      { header: 'Portföy', value: function (r) { return M.formatAmt(r.portfoy); } },
    ], data);
  }

  M.fetchJson(EP.dates)
    .then(function (d) {
      var dates = d.dates || [];
      if (!dates.length) {
        M.setStatus(el.status, 'Veri yok', true);
        M.showEmpty(el.kpis, 'Rezervasyon verisi bulunamadı.');
        return;
      }
      el.date.min = dates[0];
      el.date.max = dates[dates.length - 1];
      el.date.value = d.latest || dates[dates.length - 1];
      load();
    })
    .catch(function (e) {
      M.setStatus(el.status, 'Hata: ' + e.message, true);
      M.showError(el.kpis, 'Tarih listesi alınamadı — ' + e.message);
    });
})();
