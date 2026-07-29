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

    // R6 — accordion başlığı AÇ/KAPA DEĞİL, plot BÜYÜTME toggle'ı (eski
    // sitedeki davranış: başlığa basınca plot büyür). .plot-max kabı 72vh'ye
    // çeker (_page.html CSS'i); chart'lar height:100% olduğundan resize ile
    // kabı doldurur. İkinci tıklama normale döndürür.
    document.querySelectorAll('.accordion-header').forEach(function (h) {
      if (h.dataset.accBound) return;
      h.dataset.accBound = '1';
      h.addEventListener('click', function () {
        var acc = h.closest('.accordion');
        if (!acc) return;
        acc.classList.toggle('plot-max');
        window.requestAnimationFrame(function () {
          window.dispatchEvent(new Event('resize'));
        });
      });
    });
  }

  MVP.initCarousels = initCarousels;
  MVP.carouselGo = go;
})();
