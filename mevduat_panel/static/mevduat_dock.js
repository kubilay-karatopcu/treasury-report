/* mevduat_dock.js — sol navbar (sidebar) alt kontrol paneli (Faz P2 UX turu;
   2026-07-22 pivotu: sağ-alt fixed'ten sol-alt sidebar-docked'a taşındı ki
   plot'ların üstüne binmesin).

   PRISMA sunum modundaki sabit tarih göstergesi deseninin panel uyarlaması:
   aktif sayfanın üst-şerit kontrolleri (tarih + tekil boyut/görünüm
   seçicileri) sağ altta kullanıcıyla akan bir drawer'a CANLI TAŞINIR (klon
   değil — SPA'nın kanıtlı placeholder deseni: BSC/_bscMove ve chart-fs ile
   aynı; listener'lar ve getElementById hedefleri bozulmaz). Grup (chip)
   filtreleri ve boyut pill'leri sayfanın tepesindeki şeritte kalır;
   kart-içi/grafik-yerel seçiciler (accordion/card altındakiler) taşınmaz.
   Seçicilerde ◀ ▶ okları önceki/sonraki seçeneğe geçirir; başlık çubuğu
   tarih aralığını her zaman gösterir ve drawer'ı açıp kapar. Sayfa/alt-sekme
   değişiminde kontroller önce yerine iade edilir, aktifinkiler taşınır. */
(function () {
  "use strict";

  var moved = [];      // { node, ph }
  var dock, body, dateLabel, toggleBtn;

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function isVisible(n) { return !!(n && n.offsetParent); }

  function buildDock() {
    dock = el("div", "mv-dock");
    dock.id = "mv-dock";
    body = el("div", "mv-dock-body");
    var head = el("div", "mv-dock-head");
    head.appendChild(el("span", "mv-dock-eyebrow", "Controls"));
    dateLabel = el("span", "mv-dock-dates", "—");
    head.appendChild(dateLabel);
    toggleBtn = el("button", "mv-dock-toggle", "▾");
    toggleBtn.type = "button";
    toggleBtn.title = "Toggle panel";
    head.appendChild(toggleBtn);
    dock.appendChild(body);
    dock.appendChild(head);
    // Sol navbarın (sidebar) altına yerleşir — plot'ların üstüne binmesin
    // (kullanıcı kararı 2026-07-22). Sidebar server-render'da hazır; yoksa
    // (beklenmedik) body'ye düşülür.
    var host = document.querySelector(".mevduat-mount .sidebar") || document.body;
    host.appendChild(dock);
    head.addEventListener("click", function () {
      dock.classList.toggle("is-collapsed");
      toggleBtn.textContent = dock.classList.contains("is-collapsed") ? "▴" : "▾";
    });
    dock.addEventListener("change", function () { setTimeout(updateDates, 0); });
  }

  // Kart/grafik-yerel olmayan, aktif görünümdeki üst-şerit kontrolleri.
  function collectControls() {
    var seen = new Set();
    var out = [];   // { nodes: [label?, ctrl], ctrl, isDate }
    document.querySelectorAll(
      ".mevduat-mount .main select, " +
      ".mevduat-mount .main input[type='date'], " +
      ".mevduat-mount .main input.flatpickr-input"
    ).forEach(function (c) {
      if (!isVisible(c) || seen.has(c)) return;
      // Kart/grafik-yerel kontroller taşınmaz — istisna: weekly'nin kontrol
      // şeridi bir .card içinde yaşar, tarihleri yine de dock'a gelir.
      var inCard = c.closest(".accordion, .card, .bub-filter-panel, .bub-filter-dd-popup, .mv-dock, #bsc-pres");
      if (inCard && !/^wr-date-/.test(c.id || "")) return;
      seen.add(c);
      var wrapLab = c.closest("label");
      var nodes;
      if (wrapLab) {
        nodes = [wrapLab];
      } else {
        var lab = c.previousElementSibling;
        nodes = (lab && lab.tagName === "LABEL") ? [lab, c] : [c];
      }
      var isDate = c.matches("input[type='date'], input.flatpickr-input") ||
                   /(-date0|-date1|-vade-date|date-start|date-end)$/.test(c.id || "");
      out.push({ nodes: nodes, ctrl: c, isDate: isDate });
    });
    return out;
  }

  function stepSelect(sel, dir) {
    if (!sel || !sel.options || !sel.options.length) return;
    var i = sel.selectedIndex;
    for (var k = 0; k < sel.options.length; k++) {
      i = (i + dir + sel.options.length) % sel.options.length;
      if (!sel.options[i].disabled) break;
    }
    if (i === sel.selectedIndex) return;
    sel.selectedIndex = i;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Label'ın baştaki metin düğümünü span.mv-dock-key'e sarar (idempotent) —
  // tarih satırlarında sabit genişlikle hizalama, dim satırlarında gizleme
  // için gerekir. Span sayfaya dönünce de kalır ama görünümü değiştirmez
  // (inline, stiller yalnız .mv-dock kapsamında).
  function ensureKeySpan(lab) {
    if (lab.querySelector(":scope > .mv-dock-key")) return;
    for (var i = 0; i < lab.childNodes.length; i++) {
      var n = lab.childNodes[i];
      if (n.nodeType === 3 && n.textContent.trim()) {
        var span = el("span", "mv-dock-key", null);
        span.textContent = n.textContent.trim();
        lab.replaceChild(span, n);
        return;
      }
      // NP deseni: metin doğrudan text-node değil, <span id=...-lbl> içinde.
      if (n.nodeType === 1 && n.tagName === "SPAN" &&
          !n.querySelector("select, input") && n.textContent.trim()) {
        n.classList.add("mv-dock-key");
        return;
      }
    }
  }

  function titleOf(item) {
    // YALNIZ etiket metni — label.textContent değil (sarmalayan label'larda
    // select option metinleri de textContent'e sızıp başlığı çöplüyordu).
    for (var i = 0; i < item.nodes.length; i++) {
      var n = item.nodes[i];
      if (n.tagName !== "LABEL") continue;
      var key = n.querySelector(".mv-dock-key");
      if (key) return key.textContent.trim().replace(/:\s*$/, "");
    }
    var sib = item.nodes[0];
    if (sib && sib.tagName === "LABEL" && !sib.querySelector("select, input")) {
      return sib.textContent.trim().replace(/:\s*$/, "");
    }
    return (item.ctrl.id || "").replace(/[-_]/g, " ");
  }

  function moveNodes(item, into) {
    item.nodes.forEach(function (n) {
      var ph = el("span", "");
      ph.style.display = "none";
      n.parentNode.insertBefore(ph, n);
      moved.push({ node: n, ph: ph });
      into.appendChild(n);
    });
  }

  // Native <input type=date> her zaman tarayıcı/OS yereline göre gösterir
  // (en-US ofis makinesinde ay/gün/yıl) ve bu format ne attribute ne CSS ile
  // değişir. Çözüm: gerçek input'un metnini şeffaflaştırıp üstüne gün.ay.yıl
  // yazan bir overlay span koyarız. Takvim ikonu ve picker aynen çalışır;
  // input.value hâlâ ISO (SPA'nın .value= atamaları bozulmaz). updateDates
  // overlay metnini tazeler. Sarmalayıcı input'un YANINDA (kimi zaman sarmalayan
  // label'ın içinde) yaşadığından restoreAll onu ÖNCE söker — yoksa label sayfaya
  // geri taşınırken overlay'i de götürür.
  var dateWraps = [];   // { wrap, input }
  function wrapDateInput(input) {
    if (!input || input.type !== "date" || !input.parentNode) return;
    if (input.parentNode.classList &&
        input.parentNode.classList.contains("mv-dock-datewrap")) return;  // çift sarma
    var wrap = el("span", "mv-dock-datewrap");
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var txt = el("span", "mv-dock-datetext", null);
    txt.textContent = input.value ? _fmtDmy(input.value) : "gg.aa.yyyy";
    wrap.appendChild(txt);
    dateWraps.push({ wrap: wrap, input: input });
  }

  // Sarmalayıcıları söker: input'u tam eski yerine (wrap'ın olduğu yere) koyar,
  // wrap+overlay'i siler. moved[] node taşımasından ÖNCE çağrılır.
  function unwrapDates() {
    dateWraps.forEach(function (d) {
      if (d.input && d.wrap && d.wrap.parentNode) {
        d.wrap.parentNode.insertBefore(d.input, d.wrap);
        d.wrap.remove();
      }
    });
    dateWraps = [];
  }

  function makeRow(item) {
    item.nodes.forEach(function (n) { if (n.tagName === "LABEL") ensureKeySpan(n); });
    if (item.isDate) {
      var row = el("div", "mv-dock-row mv-dock-row--date");
      moveNodes(item, row);
      if (item.ctrl && item.ctrl.tagName === "INPUT" && item.ctrl.type === "date") {
        wrapDateInput(item.ctrl);
      }
      return row;
    }
    // Boyut/görünüm satırı: üstte ortalı başlık, altta ◀ [seçici-pill] ▶.
    var wrap = el("div", "mv-dock-row mv-dock-row--dim");
    wrap.appendChild(el("div", "mv-dock-dimhead", null)).textContent = titleOf(item);
    var line = el("div", "mv-dock-dimline");
    var sel = item.ctrl.tagName === "SELECT" ? item.ctrl : null;
    if (sel) {
      var prev = el("button", "mv-dock-arrow", "◀");
      prev.type = "button";
      prev.addEventListener("click", function (e) { e.stopPropagation(); stepSelect(sel, -1); });
      line.appendChild(prev);
    }
    moveNodes(item, line);
    if (sel) {
      var next = el("button", "mv-dock-arrow", "▶");
      next.type = "button";
      next.addEventListener("click", function (e) { e.stopPropagation(); stepSelect(sel, 1); });
      line.appendChild(next);
    }
    wrap.appendChild(line);
    return wrap;
  }

  function restoreAll() {
    unwrapDates();   // önce overlay sarmalayıcıları sök — sonra düğümleri geri taşı
    moved.forEach(function (m) {
      if (m.node && m.ph && m.ph.parentNode) {
        m.ph.parentNode.insertBefore(m.node, m.ph);
        m.ph.remove();
      }
    });
    moved = [];
    body.innerHTML = "";
  }

  // ISO (yyyy-mm-dd) → gün.ay.yıl (kullanıcı kararı 2026-07-22: kontrol panelinde
  // tarih gün-ay-yıl gösterilsin). Diğer formatlar dokunulmadan geçer.
  function _fmtDmy(v) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || "");
    return m ? m[3] + "." + m[2] + "." + m[1] : v;
  }
  function updateDates() {
    // Native date input overlay'lerini tazele (gün.ay.yıl).
    body.querySelectorAll(".mv-dock-datewrap").forEach(function (w) {
      var inp = w.querySelector("input[type='date']");
      var txt = w.querySelector(".mv-dock-datetext");
      if (inp && txt) txt.textContent = inp.value ? _fmtDmy(inp.value) : "gg.aa.yyyy";
    });
    var vals = [];
    body.querySelectorAll(".mv-dock-row--date select, .mv-dock-row--date input").forEach(function (c) {
      if (c.value) {
        var opt = c.tagName === "SELECT" && c.selectedOptions[0];
        vals.push(opt ? opt.textContent.trim() : _fmtDmy(c.value));
      }
    });
    dateLabel.textContent = vals.length ? vals.join(" → ") : "—";
  }

  function updateDock() {
    if (!dock) return;
    // Titreme önleyici: sayfada YENİ aday yoksa ve dock'takilerin yuvası
    // (placeholder'ın kabı) hâlâ görünür sayfadaysa yeniden kurma —
    // ikinci geçiş kontrolleri bir anlığına şeride geri koyup titretiyordu.
    var probe = collectControls();
    var stale = moved.some(function (m) {
      return !m.ph.parentNode || !m.ph.parentNode.offsetParent;
    });
    if (!probe.length && moved.length && !stale) { updateDates(); return; }
    restoreAll();
    var items = collectControls();
    var dates = items.filter(function (i) { return i.isDate; });
    var dims = items.filter(function (i) { return !i.isDate; });
    if (dates.length) {
      body.appendChild(el("div", "mv-dock-label", "Date Range"));
      dates.forEach(function (i) { body.appendChild(makeRow(i)); });
    }
    if (dims.length) {
      body.appendChild(el("div", "mv-dock-label", "Dimensions & View"));
      dims.forEach(function (i) { body.appendChild(makeRow(i)); });
    }
    dock.style.display = items.length ? "" : "none";
    updateDates();
  }
  window._mvDockRefresh = updateDock;

  document.addEventListener("DOMContentLoaded", function () {
    buildDock();
    // Sayfa ve alt-sekme geçişlerinde yeniden kur (SPA render'ı beklenir).
    document.addEventListener("click", function (ev) {
      var t = ev.target;
      if (!t || !t.closest) return;
      if (t.closest(".sidebar-nav a[data-page], .nim-tab-btn, #bsc-back")) {
        // Hemen kur (setPage senkron — kontroller tepede görünüp kaybolmasın),
        // geç render olan şeritler için imza-korumalı ikinci geçiş.
        setTimeout(updateDock, 0);
        setTimeout(updateDock, 450);
      }
    });
    setTimeout(updateDock, 0);
    setTimeout(updateDock, 700);
  });
})();
