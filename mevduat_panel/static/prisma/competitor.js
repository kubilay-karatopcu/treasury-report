/* Rakip Faiz Analizi — PRISMA-native sayfa mantığı (Faz S1).
   Veri: /api/reservations/competitor → {banks, rows}. Tek çekim, filtreler
   client-side.

   Not: bu sayfadaki oranlar KOTASYONDUR (bankaların ilan ettiği faiz), bizim
   kitabımızdaki bakiye değil — ağırlıklandıracak tutar yoktur. Bu yüzden
   "ortalama" burada düz ortalamadır; tutar-ağırlıklı wavg kuralı kendi
   stok/rezervasyon sayfalarımıza (oranlar/miktarlar/tarihsel) özgüdür. */
(function () {
  'use strict';
  var M = window.MVP;
  var EP = JSON.parse(document.getElementById('mvp-endpoints').textContent);

  var el = {
    date: document.getElementById('fDate'),
    ccy: document.getElementById('fCcy'),
    vadeMin: document.getElementById('fVadeMin'),
    vadeMax: document.getElementById('fVadeMax'),
    bank: document.getElementById('fBank'),
    apply: document.getElementById('mvpApply'),
    status: document.getElementById('mvpStatus'),
    meta: document.getElementById('mvpMeta'),
    kpis: document.getElementById('kpis'),
    byBank: document.getElementById('chByBank'),
    curve: document.getElementById('chCurve'),
    trend: document.getElementById('chTrend'),
    tblRows: document.getElementById('tblRows'),
  };

  var raw = [];
  var bankChips = null;

  el.apply.addEventListener('click', draw);
  [el.date, el.ccy, el.vadeMin, el.vadeMax].forEach(function (s) {
    s.addEventListener('change', draw);
  });

  function mean(list, key) {
    var t = 0, n = 0;
    list.forEach(function (r) {
      var v = M.ratePct(r[key]);
      if (v != null && !isNaN(v)) { t += v; n++; }
    });
    return n ? t / n : null;
  }
  function maxOf(list, key) {
    var best = null;
    list.forEach(function (r) {
      var v = M.ratePct(r[key]);
      if (v != null && !isNaN(v) && (best == null || v > best)) best = v;
    });
    return best;
  }

  /** Tarih filtresi HARİÇ süzülmüş satırlar — zaman serisi bunu kullanır. */
  function filteredNoDate() {
    var rows = raw;
    var vMin = el.vadeMin.value === '' ? null : Number(el.vadeMin.value);
    var vMax = el.vadeMax.value === '' ? null : Number(el.vadeMax.value);
    if (vMin != null) rows = rows.filter(function (r) { return Number(r.VADE_MAX) >= vMin; });
    if (vMax != null) rows = rows.filter(function (r) { return Number(r.VADE_MIN) <= vMax; });
    return M.applyFilters(rows, {
      DOVIZ_CINSI: el.ccy.value ? [el.ccy.value] : [],
      BANKA_ADI: bankChips ? bankChips.get() : [],
    });
  }

  function filtered() {
    var rows = filteredNoDate();
    if (el.date.value) {
      rows = rows.filter(function (r) { return r.DATE_STR === el.date.value; });
    }
    return rows;
  }

  function draw() {
    var rows = filtered();
    el.meta.textContent = (el.date.value || 'tüm tarihler') + ' · ' + rows.length + ' kotasyon';
    drawKpis(rows);
    drawTrend(filteredNoDate());
    if (!rows.length) {
      [el.byBank, el.curve, el.tblRows].forEach(M.showEmpty);
      return;
    }
    drawByBank(rows);
    drawCurve(rows);
    drawTable(rows);
  }

  function drawKpis(rows) {
    el.kpis.innerHTML = '';
    var banks = M.distinct(rows, 'BANKA_ADI');
    var mx = maxOf(rows, 'FAIZ');
    var av = mean(rows, 'FAIZ');
    // En yüksek faizi veren banka
    var top = null;
    rows.forEach(function (r) {
      var v = M.ratePct(r.FAIZ);
      if (v != null && (top == null || v > top.v)) top = { v: v, bank: r.BANKA_ADI };
    });
    M.kpi(el.kpis, 'Banka', String(banks.length), '', 'kotasyon veren');
    M.kpi(el.kpis, 'Kotasyon', String(rows.length), 'adet', 'seçili filtrelerde');
    M.kpi(el.kpis, 'Piyasa En Yüksek', mx == null ? '—' : mx.toFixed(2) + '%', '',
          top ? top.bank : '');
    M.kpi(el.kpis, 'Piyasa Ortalaması', av == null ? '—' : av.toFixed(2) + '%', '',
          'düz ortalama (kotasyon)');
    M.kpi(el.kpis, 'En Yüksek − Ortalama',
          (mx == null || av == null) ? '—' : (mx - av).toFixed(2), 'puan', 'yayılım');
  }

  function drawByBank(rows) {
    var g = M.groupBy(rows, 'BANKA_ADI');
    var data = [];
    g.forEach(function (list, name) {
      var v = maxOf(list, 'FAIZ');
      if (v != null) data.push({ name: name, v: Number(v.toFixed(2)) });
    });
    data.sort(function (a, b) { return b.v - a.v; });
    M.renderChart(el.byBank, function () {
      return {
        series: [{ name: 'En yüksek faiz', data: data.map(function (d) { return d.v; }) }],
        chart: { type: 'bar', height: Math.max(300, data.length * 26) },
        plotOptions: { bar: { horizontal: true, barHeight: '68%', borderRadius: 0,
                              distributed: true } },
        legend: { show: false },
        dataLabels: {
          enabled: true,
          formatter: function (v) { return v == null ? '' : v.toFixed(2) + '%'; },
          style: { fontSize: '10px' }, offsetX: 26,
        },
        xaxis: { categories: data.map(function (d) { return d.name; }),
                 labels: { formatter: function (v) { return Number(v).toFixed(1) + '%'; } } },
        tooltip: { y: { formatter: function (v) { return v == null ? '—' : v.toFixed(2) + '%'; } } },
      };
    });
  }

  function drawCurve(rows) {
    // Vade bandı etiketleri sayısal alt sınıra göre sıralanır (platform kuralı).
    var cats = M.distinct(rows, 'VADE').sort(function (a, b) {
      var ra = rows.find(function (r) { return String(r.VADE) === a; });
      var rb = rows.find(function (r) { return String(r.VADE) === b; });
      return (Number(ra && ra.VADE_MIN) || 0) - (Number(rb && rb.VADE_MIN) || 0);
    });
    var g = M.groupBy(rows, 'VADE');
    var avg = cats.map(function (c) {
      var v = mean(g.get(c) || [], 'FAIZ');
      return v == null ? null : Number(v.toFixed(2));
    });
    var mx = cats.map(function (c) {
      var v = maxOf(g.get(c) || [], 'FAIZ');
      return v == null ? null : Number(v.toFixed(2));
    });
    M.renderChart(el.curve, function () {
      return {
        series: [
          { name: 'Ortalama', data: avg },
          { name: 'En yüksek', data: mx },
        ],
        chart: { type: 'line', height: 300 },
        stroke: { width: 2.5, curve: 'straight', dashArray: [0, 4] },
        markers: { size: 4 },
        xaxis: { categories: cats },
        yaxis: { labels: { formatter: function (v) { return v == null ? '' : v.toFixed(1) + '%'; } } },
        tooltip: { shared: true,
                   y: { formatter: function (v) { return v == null ? '—' : v.toFixed(2) + '%'; } } },
      };
    });
  }

  function drawTrend(rows) {
    var days = M.distinct(rows, 'DATE_STR');
    if (!days.length) { M.showEmpty(el.trend); return; }
    var g = M.groupBy(rows, 'DATE_STR');
    var mx = days.map(function (d) {
      var v = maxOf(g.get(d) || [], 'FAIZ');
      return v == null ? null : Number(v.toFixed(2));
    });
    var av = days.map(function (d) {
      var v = mean(g.get(d) || [], 'FAIZ');
      return v == null ? null : Number(v.toFixed(2));
    });
    M.renderChart(el.trend, function () {
      return {
        series: [
          { name: 'Piyasa en yüksek', data: mx },
          { name: 'Piyasa ortalaması', data: av },
        ],
        chart: { type: 'line', height: 300 },
        stroke: { width: 2.5, curve: 'smooth' },
        markers: { size: 0 },
        xaxis: { categories: days, labels: { rotate: -45, hideOverlappingLabels: true } },
        yaxis: { labels: { formatter: function (v) { return v == null ? '' : v.toFixed(1) + '%'; } } },
        tooltip: { shared: true,
                   y: { formatter: function (v) { return v == null ? '—' : v.toFixed(2) + '%'; } } },
      };
    });
  }

  function drawTable(rows) {
    var data = rows.slice().sort(function (a, b) {
      return (M.ratePct(b.FAIZ) || 0) - (M.ratePct(a.FAIZ) || 0);
    }).slice(0, 300);
    M.renderTable(el.tblRows, [
      { header: 'Banka', value: function (r) { return r.BANKA_ADI; } },
      { header: 'Tarih', value: function (r) { return r.DATE_STR; } },
      { header: 'Vade', value: function (r) { return r.VADE; } },
      { header: 'Tutar', value: function (r) { return r.TUTAR; } },
      { header: 'Döviz', value: function (r) { return r.DOVIZ_CINSI; } },
      { header: 'Faiz', value: function (r) { return M.formatRate(r.FAIZ); } },
    ], data);
  }

  // ── Başlangıç ───────────────────────────────────────────────────────────
  M.setStatus(el.status, 'Yükleniyor…');
  M.fetchJson(EP.competitor)
    .then(function (payload) {
      raw = (payload && payload.rows) || [];
      var banks = (payload && payload.banks) || M.distinct(raw, 'BANKA_ADI');
      if (!raw.length) {
        M.setStatus(el.status, 'Veri yok', true);
        M.showEmpty(el.kpis, 'Rakip faiz verisi bulunamadı.');
        return;
      }
      var days = M.distinct(raw, 'DATE_STR');
      el.date.min = days[0];
      el.date.max = days[days.length - 1];
      el.date.value = days[days.length - 1];
      bankChips = M.chipGroup(el.bank, banks, draw);
      M.fillSelect(el.ccy, M.distinct(raw, 'DOVIZ_CINSI'));
      M.setStatus(el.status, raw.length + ' kayıt');
      draw();
    })
    .catch(function (e) {
      M.setStatus(el.status, 'Hata: ' + e.message, true);
      M.showError(el.kpis, 'Veri alınamadı — ' + e.message);
    });
})();
