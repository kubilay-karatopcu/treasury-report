/* ═══════════════════════════════════════════════════════════════════════════
   common.js — PRISMA-native rezervasyon/rakip sayfalarının ortak katmanı (S1).

   Sorumluluklar:
   - PRISMA token'larından türeyen ApexCharts teması (chartHelpers.js
     konvansiyonlarının vanilla karşılığı: toolbar kapalı, transparent zemin,
     inherit font, ince grid). Tema <html data-theme> ile flip olunca
     MutationObserver chart'ları yeniden boyar (shell custom event yaymıyor).
   - Formatlayıcılar (formatNumber chartHelpers ile aynı eşikler).
   - wavg — PLATFORM KURALI: oranlar asla mean() ile alınmaz,
     ağırlıklı ortalama Σ(B×r)/ΣB kullanılır.
   - Filtre barı yardımcıları (chip grubu, select doldurma) — FilterBar.jsx
     semantiğinin vanilla karşılığı.

   Global: window.MVP
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var registry = [];   // {el, build} — tema değişiminde yeniden kurulur

  // ── Token okuma ─────────────────────────────────────────────────────────
  function token(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }

  function isDark() {
    return (document.documentElement.getAttribute('data-theme') || 'dark') !== 'light';
  }

  /** PRISMA paleti — kategori renkleri uzman/token renklerinden türer. */
  function palette() {
    return [
      token('--gold', '#D4A656'),
      token('--liq', '#6B8AFD'),
      token('--green', '#4A9B6E'),
      token('--red', '#B85450'),
      token('--gold-soft', '#E8C190'),
      token('--nii', '#B6BECD'),
    ];
  }

  // ── Formatlayıcılar ─────────────────────────────────────────────────────
  function formatNumber(v) {
    if (v == null || isNaN(v)) return '';
    var abs = Math.abs(v);
    if (abs >= 1e9) return (v / 1e9).toFixed(1) + 'B';
    if (abs >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (abs >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    if (abs < 1 && v !== 0) return v.toFixed(2);
    return Math.round(v).toString();
  }

  /** Faiz gösterimi. Ham veri ondalık (0.45) ya da yüzde (45) gelebilir;
      _ratePct() ondalıkları yüzdeye çevirir (0<r<=1.5 → ×100). */
  function ratePct(r) {
    if (r == null || isNaN(r)) return null;
    var v = Number(r);
    return (v > 0 && v <= 1.5) ? v * 100 : v;
  }
  function formatRate(r, digits) {
    var v = ratePct(r);
    return v == null ? '—' : v.toFixed(digits == null ? 2 : digits) + '%';
  }
  function formatAmt(v) { return v == null || isNaN(v) ? '—' : formatNumber(v); }

  /** Ağırlıklı ortalama — Σ(w×v)/Σw. Ağırlık yoksa/sıfırsa null (mean'e DÜŞMEZ). */
  function wavg(rows, valueKey, weightKey) {
    var num = 0, den = 0;
    for (var i = 0; i < rows.length; i++) {
      var v = Number(rows[i][valueKey]);
      var w = Number(rows[i][weightKey]);
      if (isNaN(v) || isNaN(w) || w <= 0) continue;
      num += v * w; den += w;
    }
    return den > 0 ? num / den : null;
  }

  function sum(rows, key) {
    var t = 0;
    for (var i = 0; i < rows.length; i++) {
      var v = Number(rows[i][key]);
      if (!isNaN(v)) t += v;
    }
    return t;
  }

  /** Benzersiz değerler (null/boş atılır, alfabetik). */
  function distinct(rows, key) {
    var seen = Object.create(null), out = [];
    for (var i = 0; i < rows.length; i++) {
      var v = rows[i][key];
      if (v == null || v === '') continue;
      var s = String(v);
      if (!seen[s]) { seen[s] = 1; out.push(s); }
    }
    return out.sort();
  }

  /**
   * Bant/vade etiketi sıralama anahtarı — PLATFORM KURALI: etiketler alfabetik
   * değil, sayısal alt sınıra göre sıralanır ("32-35" < "100-180"; "1M" < "2M").
   * Python `_band_sort_key` / SQL `band_order_expr` karşılığı.
   */
  function bandSortKey(label) {
    var s = String(label == null ? '' : label);
    var m = s.match(/(\d+(?:[.,]\d+)?)\s*([KMB])?/i);
    if (!m) return Number.MAX_SAFE_INTEGER;
    var n = parseFloat(m[1].replace(',', '.'));
    var mult = { K: 1e3, M: 1e6, B: 1e9 }[(m[2] || '').toUpperCase()] || 1;
    return n * mult;
  }
  function bandSort(labels) {
    return labels.slice().sort(function (a, b) {
      var d = bandSortKey(a) - bandSortKey(b);
      return d !== 0 ? d : String(a).localeCompare(String(b), 'tr');
    });
  }

  function groupBy(rows, key) {
    var m = new Map();
    for (var i = 0; i < rows.length; i++) {
      var k = rows[i][key];
      k = (k == null || k === '') ? '—' : String(k);
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(rows[i]);
    }
    return m;
  }

  // ── Apex ortak seçenekleri (chartHelpers.js karşılığı) ──────────────────
  function baseOptions() {
    var dark = isDark();
    var ink = token('--ink', '#E8ECF1');
    var faint = token('--ink-faint', '#6B768A');
    var border = token('--border-soft', '#1F2433');
    return {
      chart: {
        toolbar: { show: false },
        fontFamily: 'inherit',
        background: 'transparent',
        foreColor: ink,
        animations: { enabled: true, speed: 250 },
        zoom: { enabled: false },
      },
      theme: { mode: dark ? 'dark' : 'light' },
      colors: palette(),
      grid: {
        borderColor: border,
        strokeDashArray: 0,
        yaxis: { lines: { show: true } },
        xaxis: { lines: { show: false } },
        padding: { left: 8, right: 8, top: 0, bottom: 0 },
      },
      dataLabels: { enabled: false },
      tooltip: { theme: dark ? 'dark' : 'light', style: { fontSize: '12px' } },
      legend: {
        fontSize: '11px', labels: { colors: faint },
        markers: { width: 9, height: 9, radius: 0 },
        itemMargin: { horizontal: 10 },
      },
      xaxis: { labels: { style: { fontSize: '11px', colors: faint } },
               axisBorder: { color: border }, axisTicks: { color: border } },
      yaxis: { labels: { style: { fontSize: '11px', colors: faint } } },
    };
  }

  /** Derin birleştirme (Apex option ağaçları için yeterli). */
  function merge(a, b) {
    var out = Object.assign({}, a);
    Object.keys(b || {}).forEach(function (k) {
      var v = b[k];
      if (v && typeof v === 'object' && !Array.isArray(v) &&
          out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) {
        out[k] = merge(out[k], v);
      } else { out[k] = v; }
    });
    return out;
  }

  /**
   * Chart kur/güncelle. `build()` her çağrıda taze options döndürmeli —
   * tema değişiminde aynı build yeniden koşar (renkler token'lardan gelir).
   */
  function renderChart(el, build) {
    if (!el) return;
    var entry = registry.find(function (e) { return e.el === el; });
    if (!entry) { entry = { el: el, build: build, chart: null }; registry.push(entry); }
    entry.build = build;
    var opts = merge(baseOptions(), build());
    if (entry.chart) { entry.chart.destroy(); entry.chart = null; }
    el.innerHTML = '';
    entry.chart = new ApexCharts(el, opts);
    entry.chart.render();
  }

  function repaintAll() {
    registry.forEach(function (e) {
      if (!e.build) return;
      var opts = merge(baseOptions(), e.build());
      if (e.chart) { e.chart.destroy(); }
      e.el.innerHTML = '';
      e.chart = new ApexCharts(e.el, opts);
      e.chart.render();
    });
  }

  // Tema flip'i izle — shell custom event yaymıyor, attribute değişimi izlenir.
  new MutationObserver(function (muts) {
    for (var i = 0; i < muts.length; i++) {
      if (muts[i].attributeName === 'data-theme') { repaintAll(); return; }
    }
  }).observe(document.documentElement, { attributes: true });

  // ── Veri erişimi ────────────────────────────────────────────────────────
  function fetchJson(url) {
    return fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
  }

  // ── Filtre yardımcıları (FilterBar semantiği) ───────────────────────────
  /**
   * Çoklu seçim chip grubu. Seçim boş = "tümü" (filtre uygulanmaz) —
   * FilterBar enum_multi davranışının sadeleştirilmiş hali.
   */
  function chipGroup(el, values, onChange) {
    if (!el) return { get: function () { return []; } };
    var selected = new Set();
    function paint() {
      el.innerHTML = '';
      values.forEach(function (v) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'pk-chip' + (selected.has(v) ? ' is-on' : '');
        b.textContent = v;
        b.addEventListener('click', function () {
          if (selected.has(v)) selected.delete(v); else selected.add(v);
          paint();
          if (onChange) onChange(Array.from(selected));
        });
        el.appendChild(b);
      });
    }
    paint();
    return { get: function () { return Array.from(selected); } };
  }

  function fillSelect(el, values, opts) {
    if (!el) return;
    opts = opts || {};
    el.innerHTML = '';
    var all = document.createElement('option');
    all.value = ''; all.textContent = opts.allLabel || 'Tümü';
    el.appendChild(all);
    values.forEach(function (v) {
      var o = document.createElement('option');
      o.value = v; o.textContent = v;
      el.appendChild(o);
    });
    if (opts.value) el.value = opts.value;
  }

  /** Satırları aktif filtre setine göre süz. spec: {key: [values]} — boş = tümü. */
  function applyFilters(rows, spec) {
    var keys = Object.keys(spec || {});
    if (!keys.length) return rows;
    return rows.filter(function (r) {
      for (var i = 0; i < keys.length; i++) {
        var want = spec[keys[i]];
        if (!want || !want.length) continue;
        if (want.indexOf(String(r[keys[i]])) === -1) return false;
      }
      return true;
    });
  }

  function setStatus(el, text, isErr) {
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('is-err', !!isErr);
  }

  function showError(el, message) {
    if (!el) return;
    el.innerHTML = '';
    var d = document.createElement('div');
    d.className = 'pk-error';
    d.textContent = message;
    el.appendChild(d);
  }

  function showEmpty(el, message) {
    if (!el) return;
    el.innerHTML = '';
    var d = document.createElement('div');
    d.className = 'pk-empty';
    d.textContent = message || 'Seçilen filtrelerde veri yok.';
    el.appendChild(d);
  }

  /** Basit tablo render (AG Grid gerekmeyen özet tablolar için). */
  function renderTable(el, columns, rows) {
    if (!el) return;
    if (!rows.length) { showEmpty(el); return; }
    var wrap = document.createElement('div');
    wrap.className = 'pk-table-wrap';
    var t = document.createElement('table');
    t.className = 'pk-table';
    var thead = document.createElement('thead');
    var htr = document.createElement('tr');
    columns.forEach(function (c) {
      var th = document.createElement('th');
      th.textContent = c.header;
      htr.appendChild(th);
    });
    thead.appendChild(htr); t.appendChild(thead);
    var tb = document.createElement('tbody');
    rows.forEach(function (r) {
      var tr = document.createElement('tr');
      columns.forEach(function (c) {
        var td = document.createElement('td');
        var val = c.value(r);
        td.textContent = val == null ? '—' : val;
        if (c.tone) td.className = c.tone(r) || '';
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    t.appendChild(tb); wrap.appendChild(t);
    el.innerHTML = ''; el.appendChild(wrap);
  }

  function kpi(el, label, value, unit, sub) {
    var d = document.createElement('div');
    d.className = 'pk-kpi';
    d.innerHTML =
      '<div class="pk-kpi__label"></div>' +
      '<div class="pk-kpi__value"><span class="v"></span><span class="pk-kpi__unit"></span></div>' +
      '<div class="pk-kpi__sub"></div>';
    d.querySelector('.pk-kpi__label').textContent = label;
    d.querySelector('.v').textContent = value;
    d.querySelector('.pk-kpi__unit').textContent = unit || '';
    d.querySelector('.pk-kpi__sub').textContent = sub || '';
    el.appendChild(d);
    return d;
  }

  // ── Embed modu (?embed=1&anchor=<id,...>&state=<b64url>) ────────────────
  // SPA'nın (mevduat_panel.js:8949) sözleşmesiyle aynı: uzman brifingindeki
  // atıf modalı bu sayfayı iframe'de açar. Kabuk + filtre barı gizlenir,
  // anchor'lanan kart(lar) izole edilip vurgulanır, state paketindeki kontrol
  // değerleri yazılır (digest'in hesapladığı görünüm birebir açılsın).
  function initEmbed(onControlsApplied) {
    var qs = new URLSearchParams(window.location.search);
    if (qs.get("embed") !== "1") return false;
    document.body.classList.add("pk-embed");

    var raw = qs.get("anchor") || "";
    if (!/^[A-Za-z0-9_,-]*$/.test(raw)) raw = "";
    var anchors = raw.split(",").filter(Boolean);

    var controls = [];
    try {
      var s = qs.get("state") || "";
      if (s) {
        s = s.replace(/-/g, "+").replace(/_/g, "/");
        s += "===".slice(0, (4 - s.length % 4) % 4);
        var st = JSON.parse(atob(s));
        if (st && Array.isArray(st.controls)) controls = st.controls;
      }
    } catch (e) { controls = []; }   // bozuk state → varsayılan görünüm

    var applied = false;
    function applyControls() {
      var did = false;
      controls.forEach(function (c) {
        if (!c) return;
        if (c.click) {
          var t = document.querySelector(c.click);
          if (t) { t.click(); did = true; }
          return;
        }
        if (!c.id) return;
        var el = document.getElementById(c.id);
        if (!el) return;
        if (el.tagName === "SELECT" &&
            !Array.prototype.some.call(el.options, function (o) {
              return o.value === String(c.value);
            })) return;                      // option henüz yüklenmedi
        if (el.value === String(c.value)) return;
        el.value = String(c.value);
        el.dispatchEvent(new Event("change", { bubbles: true }));
        did = true;
      });
      return did;
    }

    function isolate() {
      if (!anchors.length) return;
      var wanted = {};
      anchors.forEach(function (a) { wanted[a] = 1; });
      var found = false;
      document.querySelectorAll(".pk-card").forEach(function (card) {
        if (wanted[card.id]) { card.classList.add("pk-embed-target"); found = true; }
        else { card.setAttribute("data-embed-hidden", "1"); }
      });
      if (found) {
        var first = document.getElementById(anchors[0]);
        if (first) first.scrollIntoView({ block: "start" });
      }
    }

    // Kontroller veri geldikten sonra dolar (select option'ları) — birkaç tur
    // dene, sonra izole et. Kontrol yoksa doğrudan izole edilir.
    var tries = 0;
    (function tick() {
      tries++;
      if (controls.length && !applied) {
        applied = applyControls();
        if (applied && onControlsApplied) onControlsApplied();
      }
      if ((controls.length && !applied) && tries < 25) {
        return setTimeout(tick, 200);
      }
      isolate();
    })();
    return true;
  }

  window.MVP = {
    initEmbed: initEmbed,
    token: token, isDark: isDark, palette: palette,
    formatNumber: formatNumber, formatRate: formatRate, formatAmt: formatAmt,
    ratePct: ratePct, wavg: wavg, sum: sum, distinct: distinct, groupBy: groupBy,
    bandSort: bandSort, bandSortKey: bandSortKey,
    renderChart: renderChart, repaintAll: repaintAll, merge: merge,
    fetchJson: fetchJson, chipGroup: chipGroup, fillSelect: fillSelect,
    applyFilters: applyFilters, setStatus: setStatus,
    showError: showError, showEmpty: showEmpty, renderTable: renderTable, kpi: kpi,
  };
})();
