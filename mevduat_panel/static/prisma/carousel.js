/* ═══════════════════════════════════════════════════════════════════════════
   carousel.js — Outstanding tarzı karosel (R5, homojen UI revizyonu).

   Görsel dil SPA'nın waterfall karoselinden (index.html): kontroller
   `.wf-carousel-nav` içinde `.wf-slide-label` + `.wf-nav-btn` (◀ ▶),
   slaytlar `hidden` sınıfıyla gizlenir (mevduat_panel.css:
   `.hidden{display:none!important}`). Bu dosya stil TANIMLAMAZ — markup
   SPA sınıflarını kullanır, stiller mevduat_panel.css'ten gelir.

   Kritik: ApexCharts gizli (display:none) kapta 0 genişlikle çizilir. Slayt
   değişiminde rAF içinde `resize` yayınlanır — Apex yeni görünür slaytı doğru
   genişlikte yeniden ölçer.

   Markup sözleşmesi:
     <div class="mv-carousel" id="X">
       <div class="mvc-slide">…</div>
       <div class="mvc-slide hidden">…</div>
     </div>
   Kontroller (SPA deseni):
     <div class="wf-carousel-nav">
       <span class="wf-slide-label" data-car-ind="X">1 / N</span>
       <button class="wf-nav-btn" data-car-prev="X">&#9664;</button>
       <button class="wf-nav-btn" data-car-next="X">&#9654;</button>
     </div>

   Global: window.MVP.initCarousels / MVP.carouselGo
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var MVP = window.MVP;
  if (!MVP) { return; }

  function slidesOf(root) {
    // Doğrudan çocuklar — iç içe yapılarda yabancı slayt toplanmasın.
    return Array.prototype.filter.call(root.children, function (el) {
      return el.classList && el.classList.contains('mvc-slide');
    });
  }

  function activeIndex(slides) {
    for (var i = 0; i < slides.length; i++) {
      if (!slides[i].classList.contains('hidden')) return i;
    }
    return 0;
  }

  function updateIndicator(id, idx, total) {
    document.querySelectorAll('[data-car-ind="' + id + '"]').forEach(function (el) {
      el.textContent = (idx + 1) + ' / ' + total;
    });
  }

  /** Karoseli `delta` kadar kaydırır (sarmalı). */
  function go(id, delta) {
    var root = document.getElementById(id);
    if (!root) return;
    var slides = slidesOf(root);
    if (slides.length < 2) return;

    var cur = activeIndex(slides);
    var next = (cur + delta + slides.length) % slides.length;
    slides.forEach(function (s, i) {
      s.classList.toggle('hidden', i !== next);
    });
    updateIndicator(id, next, slides.length);

    // Apex'in yeni görünür slaytı doğru ölçmesi için — slayt görünür olduktan
    // sonraki kareye ertele.
    window.requestAnimationFrame(function () {
      window.dispatchEvent(new Event('resize'));
    });
  }

  /** Sayfadaki tüm karoselleri kurar (idempotent — iki kez çağrılabilir). */
  function initCarousels() {
    document.querySelectorAll('.mv-carousel').forEach(function (root) {
      if (!root.id) return;
      var slides = slidesOf(root);
      if (!slides.length) return;
      var act = activeIndex(slides);
      slides.forEach(function (s, i) {
        s.classList.toggle('hidden', i !== act);
      });
      updateIndicator(root.id, act, slides.length);
    });

    document.querySelectorAll('[data-car-prev]').forEach(function (btn) {
      if (btn.dataset.carBound) return;
      btn.dataset.carBound = '1';
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        go(btn.dataset.carPrev, -1);
      });
    });
    document.querySelectorAll('[data-car-next]').forEach(function (btn) {
      if (btn.dataset.carBound) return;
      btn.dataset.carBound = '1';
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        go(btn.dataset.carNext, 1);
      });
    });

    // R7 — accordion başlığı SPA'daki gibi TAM EKRAN overlay açar (_open
    // deseninin sadeleşmiş aynası, mevduat_panel.js:~6400): gövde placeholder
    // bırakılarak body'ye eklenen .chart-fs-overlay'e TAŞINIR (chart örnekleri
    // yaşamaya devam eder), ✕ / Esc / boşluğa tıklama geri koyar. Kenar
    // boşlukları ve stiller mevduat_panel.css'in chart-fs-* sınıflarından.
    document.querySelectorAll('.accordion-header').forEach(function (h) {
      if (h.dataset.accBound) return;
      h.dataset.accBound = '1';
      h.addEventListener('click', function () {
        var body = h.nextElementSibling;
        if (body) openFullscreen(body);
      });
    });
  }

  // ── R7: tam-ekran overlay (SPA _open aynası) ─────────────────────────────

  var _fs = null;   // {overlay, el, ph, prevCss} — aynı anda tek overlay

  function _fsResize() {
    window.requestAnimationFrame(function () {
      window.dispatchEvent(new Event('resize'));
    });
  }

  function closeFullscreen() {
    if (!_fs) return;
    var f = _fs; _fs = null;
    f.ph.parentNode.insertBefore(f.el, f.ph);
    f.ph.remove();
    f.el.style.cssText = f.prevCss;
    f.overlay.remove();
    _fsResize();
  }

  function openFullscreen(el) {
    if (_fs || !el) return;
    var ph = document.createElement('div');
    ph.style.display = 'none';
    el.parentNode.insertBefore(ph, el);
    var prevCss = el.style.cssText;

    var overlay = document.createElement('div');
    overlay.className = 'chart-fs-overlay';
    var topbar = document.createElement('div');
    topbar.className = 'chart-fs-topbar';
    var btn = document.createElement('button');
    btn.className = 'chart-fs-close';
    btn.type = 'button';
    btn.innerHTML = '&#10005;';
    btn.title = 'Kapat (Esc)';
    btn.addEventListener('click', closeFullscreen);
    topbar.appendChild(btn);
    overlay.appendChild(topbar);

    // İçerik kenarları boş bir İÇ KUTUDA durur; kutunun DIŞINA (overlay
    // padding alanı) tıklama kapatır — eski sitedeki modal davranışı.
    var inner = document.createElement('div');
    inner.className = 'chart-fs-body chart-fs-inner';
    inner.appendChild(el);
    overlay.appendChild(inner);
    overlay.addEventListener('click', function (ev) {
      if (!ev.target.closest('.chart-fs-inner') &&
          !ev.target.closest('.chart-fs-close')) closeFullscreen();
    });
    document.body.appendChild(overlay);

    _fs = { overlay: overlay, el: el, ph: ph, prevCss: prevCss };
    _fsResize();
  }

  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') closeFullscreen();
  });

  MVP.initCarousels = initCarousels;
  MVP.carouselGo = go;
  MVP.openFullscreen = openFullscreen;
  MVP.closeFullscreen = closeFullscreen;
})();
