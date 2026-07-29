/* ═══════════════════════════════════════════════════════════════════════════
   carousel.js — PRISMA sayfaları için hafif karosel (R5).

   Eski sayfalar Bootstrap/Tabler karoselini kullanıyordu (`data-bs-slide`);
   PRISMA kabuğunda Bootstrap JS YOK. Bu, aynı etkileşimi bağımlılıksız verir:
   kart başlığındaki ‹ › okları + "n / N" göstergesi.

   Kritik: ApexCharts gizli (display:none) kapta 0 genişlikle çizilir. Slayt
   değişiminde `resize` yayınlanır — Apex bunu dinler ve yeni görünür slaytı
   doğru genişlikte yeniden ölçer. Yayın rAF içinde yapılır, yani slayt görünür
   olduktan SONRA.

   Markup sözleşmesi:
     <div class="pk-carousel" id="X">
       <div class="pk-carousel__slide is-active">…</div>
       <div class="pk-carousel__slide">…</div>
     </div>
   Kontroller: <button data-car-prev="X">  <span data-car-ind="X">  <button data-car-next="X">

   Global: window.MVP.initCarousels / MVP.carouselGo
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var MVP = window.MVP;
  if (!MVP) { return; }

  function slidesOf(root) {
    // Doğrudan çocuklar — iç içe karosel olursa yabancı slayt toplanmasın.
    return Array.prototype.filter.call(root.children, function (el) {
      return el.classList && el.classList.contains('pk-carousel__slide');
    });
  }

  function activeIndex(slides) {
    for (var i = 0; i < slides.length; i++) {
      if (slides[i].classList.contains('is-active')) return i;
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
    slides[cur].classList.remove('is-active');
    slides[next].classList.add('is-active');
    updateIndicator(id, next, slides.length);

    // Apex'in yeni görünür slaytı doğru ölçmesi için — slayt görünür olduktan
    // sonraki kareye ertele.
    window.requestAnimationFrame(function () {
      window.dispatchEvent(new Event('resize'));
    });
  }

  /** Sayfadaki tüm karoselleri kurar (idempotent — iki kez çağrılabilir). */
  function initCarousels() {
    document.querySelectorAll('.pk-carousel').forEach(function (root) {
      if (!root.id) return;
      var slides = slidesOf(root);
      if (!slides.length) return;
      if (!slides.some(function (s) { return s.classList.contains('is-active'); })) {
        slides[0].classList.add('is-active');
      }
      updateIndicator(root.id, activeIndex(slides), slides.length);
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
  }

  MVP.initCarousels = initCarousels;
  MVP.carouselGo = go;
})();
