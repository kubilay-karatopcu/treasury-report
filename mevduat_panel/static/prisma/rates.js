/* Rezervasyon Oranları — PRISMA-native sayfa mantığı (Faz S1).
   Veri: /api/reservations/oranlar/<date>. Oran agregasyonları DAİMA tutar
   ağırlıklı (MVP.wavg) — platform kuralı: rate asla mean() ile alınmaz. */
(function () {
  'use strict';
  var M = window.MVP;
  var EP = JSON.parse(document.getElementById('mvp-endpoints').textContent);

  var el = {
    date: document.getElementById('fDate'),
    src: document.getElementById('fSrc'),
    ccy: document.getElementById('fCcy'),
    cust: document.getElementById('fCust'),
    vade: document.getElementById('fVade'),
    revMax: document.getElementById('fRevMax'),
    apply: document.getElementById('mvpApply'),
    status: document.getElementById('mvpStatus'),
    meta: document.getElementById('mvpMeta'),
    kpis: document.getElementById('kpis'),
    intraday: document.getElementById('chIntraday'),
    byTenor: document.getElementById('chByTenor'),
    tblTenor: document.getElementById('tblTenor'),
  };

  var raw = [];          // seçili günün ham satırları
  var srcChips = null;
  var onlyMax = false;

  el.revMax.addEventListener('click', function () {
    onlyMax = !onlyMax;
    el.revMax.classList.toggle('is-on', onlyMax);
    draw();
  });
  el.apply.addEventListener('click', load);
  [el.ccy, el.cust, el.vade].forEach(function (s) {
    s.addEventListener('change', draw);
  });


  // Embed modu: veri yüklenip ilk çizim yapıldıktan sonra bir kez başlatılır
  // (kontrol select'leri ancak o zaman dolu olur).
  var _embedStarted = false;
  function _embedOnce() {
    if (_embedStarted) return;
    _embedStarted = true;
    M.initEmbed(draw);
  }

  function filtered() {
    var rows = raw;
    if (onlyMax) rows = rows.filter(function (r) { return r.IS_MAX_REVIZE; });
    return M.applyFilters(rows, {
      DATA_SRC: srcChips ? srcChips.get() : [],
      CCY_CODE: el.ccy.value ? [el.ccy.value] : [],
      CUST_TP: el.cust.value ? [el.cust.value] : [],
      VADE_BASLANGIC: el.vade.value ? [el.vade.value] : [],
    });
  }

  function load() {
    var d = el.date.value;
    if (!d) return;
    M.setStatus(el.status, 'Yükleniyor…');
    document.querySelectorAll('.mvp-card, .mvp-kpis').forEach(function (c) {
      c.classList.add('mvp-loading');
    });
    M.fetchJson(EP.rates + d)
      .then(function (rows) {
        raw = rows || [];
        srcChips = M.chipGroup(el.src, M.distinct(raw, 'DATA_SRC'), draw);
        M.fillSelect(el.ccy, M.distinct(raw, 'CCY_CODE'));
        M.fillSelect(el.cust, M.distinct(raw, 'CUST_TP'));
        M.fillSelect(el.vade, M.bandSort(M.distinct(raw, 'VADE_BASLANGIC')));
        M.setStatus(el.status, raw.length + ' kayıt');
        draw();
        _embedOnce();
      })
      .catch(function (e) {
        M.setStatus(el.status, 'Hata: ' + e.message, true);
        M.showError(el.kpis, 'Veri alınamadı — ' + e.message);
      })
      .finally(function () {
        document.querySelectorAll('.mvp-loading').forEach(function (c) {
          c.classList.remove('mvp-loading');
        });
      });
  }

  function draw() {
    var rows = filtered();
    el.meta.textContent = (el.date.value || '—') + ' · ' + rows.length + ' rezervasyon';
    drawKpis(rows);
    if (!rows.length) {
      M.showEmpty(el.intraday); M.showEmpty(el.byTenor); M.showEmpty(el.tblTenor);
      return;
    }
    drawIntraday(rows);
    drawByTenor(rows);
    drawTable(rows);
  }

  function drawKpis(rows) {
    el.kpis.innerHTML = '';
    var total = M.sum(rows, 'RESERVATION_AMT');
    var wOffer = M.wavg(rows, 'OFFERED_RATE', 'RESERVATION_AMT');
    var wDemand = M.wavg(rows, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT');
    var wMarket = M.wavg(rows, 'MARKET_MAX_RT', 'RESERVATION_AMT');
    var spread = (wOffer != null && wMarket != null)
      ? (M.ratePct(wOffer) - M.ratePct(wMarket)) : null;

    M.kpi(el.kpis, 'Rezervasyon', String(rows.length), 'adet',
          onlyMax ? 'yalnız son revizyon' : 'tüm revizyonlar');
    M.kpi(el.kpis, 'Toplam Tutar', M.formatAmt(total), '', 'seçili filtrelerde');
    M.kpi(el.kpis, 'Ağırlıklı Teklif', M.formatRate(wOffer), '', 'Σ(tutar×oran)/Σtutar');
    M.kpi(el.kpis, 'Ağırlıklı Talep', M.formatRate(wDemand), '', 'müşteri talebi');
    M.kpi(el.kpis, 'Piyasa Üst Sınırına Fark',
          spread == null ? '—' : (spread > 0 ? '+' : '') + spread.toFixed(2), 'puan',
          'teklif − piyasa max');
  }

  function drawIntraday(rows) {
    var bySrc = M.groupBy(rows, 'DATA_SRC');
    var series = [];
    bySrc.forEach(function (list, name) {
      var pts = [];
      list.forEach(function (r) {
        var t = Date.parse(String(r.DATE_TIME_STR || '').replace(' ', 'T'));
        var y = M.ratePct(r.OFFERED_RATE);
        if (!isNaN(t) && y != null) pts.push({ x: t, y: Number(y.toFixed(2)) });
      });
      pts.sort(function (a, b) { return a.x - b.x; });
      series.push({ name: name, data: pts });
    });
    M.renderChart(el.intraday, function () {
      return {
        series: series,
        chart: { type: 'scatter', height: 300 },
        markers: { size: 5, strokeWidth: 0, opacity: 0.75 },
        xaxis: { type: 'datetime', labels: { datetimeUTC: false, format: 'HH:mm' } },
        yaxis: { labels: { formatter: function (v) { return v == null ? '' : v.toFixed(1) + '%'; } } },
        tooltip: { x: { format: 'HH:mm:ss' },
                   y: { formatter: function (v) { return v == null ? '' : v.toFixed(2) + '%'; } } },
      };
    });
  }

  function drawByTenor(rows) {
    var cats = M.bandSort(M.distinct(rows, 'VADE_BASLANGIC'));
    var g = M.groupBy(rows, 'VADE_BASLANGIC');
    function serie(key) {
      return cats.map(function (c) {
        var v = M.wavg(g.get(c) || [], key, 'RESERVATION_AMT');
        var p = M.ratePct(v);
        return p == null ? null : Number(p.toFixed(2));
      });
    }
    M.renderChart(el.byTenor, function () {
      return {
        series: [
          { name: 'Teklif', data: serie('OFFERED_RATE') },
          { name: 'Talep', data: serie('PERCENTILE_DEMANDED_RTS') },
          { name: 'Rakip', data: serie('PERCENTILE_COMPETITOR_RTS') },
        ],
        chart: { type: 'bar', height: 300 },
        plotOptions: { bar: { columnWidth: '62%', borderRadius: 0 } },
        stroke: { show: true, width: 1, colors: ['transparent'] },
        xaxis: { categories: cats },
        yaxis: { labels: { formatter: function (v) { return v == null ? '' : v.toFixed(1) + '%'; } } },
        tooltip: { y: { formatter: function (v) { return v == null ? '—' : v.toFixed(2) + '%'; } } },
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
        tutar: M.sum(list, 'RESERVATION_AMT'),
        teklif: M.wavg(list, 'OFFERED_RATE', 'RESERVATION_AMT'),
        talep: M.wavg(list, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT'),
        rakip: M.wavg(list, 'PERCENTILE_COMPETITOR_RTS', 'RESERVATION_AMT'),
        piyasa: M.wavg(list, 'MARKET_MAX_RT', 'RESERVATION_AMT'),
      };
    });
    M.renderTable(el.tblTenor, [
      { header: 'Vade', value: function (r) { return r.vade; } },
      { header: 'Adet', value: function (r) { return r.adet; } },
      { header: 'Tutar', value: function (r) { return M.formatAmt(r.tutar); } },
      { header: 'Teklif', value: function (r) { return M.formatRate(r.teklif); } },
      { header: 'Talep', value: function (r) { return M.formatRate(r.talep); } },
      { header: 'Rakip', value: function (r) { return M.formatRate(r.rakip); } },
      { header: 'Piyasa Max', value: function (r) { return M.formatRate(r.piyasa); } },
    ], data);
  }

  // ── Başlangıç: tarih listesi → en son gün ───────────────────────────────
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
