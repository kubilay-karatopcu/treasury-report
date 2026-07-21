/* mevduat_dock.js — sağ-alt sabit kontrol paneli (Faz P2 UX turu).

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
    head.appendChild(el("span", "mv-dock-eyebrow", "Kontroller"));
    dateLabel = el("span", "mv-dock-dates", "—");
    head.appendChild(dateLabel);
    toggleBtn = el("button", "mv-dock-toggle", "▾");
    toggleBtn.type = "button";
    toggleBtn.title = "Paneli aç/kapat";
    head.appendChild(toggleBtn);
    dock.appendChild(body);
    dock.appendChild(head);
    document.body.appendChild(dock);
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

  function makeRow(item) {
    var row = el("div", "mv-dock-row" + (item.isDate ? " mv-dock-row--date" : ""));
    var sel = (item.ctrl.tagName === "SELECT" && !item.isDate) ? item.ctrl : null;
    if (sel) {
      var prev = el("button", "mv-dock-arrow", "◀");
      prev.type = "button";
      prev.addEventListener("click", function (e) { e.stopPropagation(); stepSelect(sel, -1); });
      row.appendChild(prev);
    }
    item.nodes.forEach(function (n) {
      var ph = el("span", "");
      ph.style.display = "none";
      n.parentNode.insertBefore(ph, n);
      moved.push({ node: n, ph: ph });
      row.appendChild(n);
    });
    if (sel) {
      var next = el("button", "mv-dock-arrow", "▶");
      next.type = "button";
      next.addEventListener("click", function (e) { e.stopPropagation(); stepSelect(sel, 1); });
      row.appendChild(next);
    }
    return row;
  }

  function restoreAll() {
    moved.forEach(function (m) {
      if (m.node && m.ph && m.ph.parentNode) {
        m.ph.parentNode.insertBefore(m.node, m.ph);
        m.ph.remove();
      }
    });
    moved = [];
    body.innerHTML = "";
  }

  function updateDates() {
    var vals = [];
    body.querySelectorAll(".mv-dock-row--date select, .mv-dock-row--date input").forEach(function (c) {
      if (c.value) {
        var opt = c.tagName === "SELECT" && c.selectedOptions[0];
        vals.push(opt ? opt.textContent.trim() : c.value);
      }
    });
    dateLabel.textContent = vals.length ? vals.join(" → ") : "—";
  }

  function updateDock() {
    if (!dock) return;
    restoreAll();
    var items = collectControls();
    var dates = items.filter(function (i) { return i.isDate; });
    var dims = items.filter(function (i) { return !i.isDate; });
    if (dates.length) {
      body.appendChild(el("div", "mv-dock-label", "Tarih Aralığı"));
      dates.forEach(function (i) { body.appendChild(makeRow(i)); });
    }
    if (dims.length) {
      body.appendChild(el("div", "mv-dock-label", "Boyutlar & Görünüm"));
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
        setTimeout(updateDock, 420);
      }
    });
    setTimeout(updateDock, 700);
  });
})();
