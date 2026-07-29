/* ═══════════════════════════════════════════════════════════════════════════
   bubfilter.js — Outstanding filtre kiti, PRISMA-native sayfalar için (R4).

   Outstanding SPA'sındaki `_renderBubFilters` bileşeninin (mevduat_panel.js)
   sadık portu: boyut başına açılır liste, üstte **All | None**, seçilenleri
   tek satırda birleştirme ("＋ Group Selected") ve grup bozma.

   SINIF ADLARI SPA İLE AYNI (`bub-filter-*`): sayfalar artık Outstanding
   kabuğunu (mevduat_panel.css) yüklüyor, stiller oradan birebir gelir — ayrı
   bir kit stili YOK (kullanıcı kararı 2026-07-29: tam homojenlik).

   SPA'ya özel olduğu için PORT EDİLMEYENLER: AUM ↔ AUM_BAND grup aynası
   (`_mirrorAumMergeAcross`) ve sayfa-ailesi bazlı boyut sıralaması. Sıralama
   varsayılanı repo genel kuralıdır: bant etiketleri sayısal alt sınıra göre
   (common.js `bandSort`), diğerleri Türkçe alfabetik.

   Bağımlılık: common.js (window.MVP) önce yüklenmeli.
   Global: window.MVP.renderBubFilters
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var MVP = window.MVP;
  if (!MVP) { return; }   // common.js yüklenmemiş — sessizce devre dışı.

  /* Varsayılan sıralama: bant etiketleri (AUM/vade) sayısal alt sınıra göre,
     diğerleri Türkçe locale ile alfabetik. bandSort zaten bant olmayan
     etiketlerde alfabetiğe düşer. */
  function defaultSort(dim, vals) {
    return MVP.bandSort ? MVP.bandSort(vals)
                        : vals.slice().sort(function (a, b) {
                            return String(a).localeCompare(String(b), 'tr');
                          });
  }

  /* Varsayılan grup kurucu: seçilenleri virgülle birleştirir. En az 2 değer
     gerekir — tek değerlik "grup" anlamsızdır. */
  function defaultBuildGroup(dim, availableVals, selected) {
    if (selected.length < 2) return null;
    return { name: selected.join(','), members: selected.slice() };
  }

  /**
   * Boyut açılır listelerini `panelId` içine çizer.
   *
   * @param {string}   panelId  hedef div id'si
   * @param {Object}   meta     {boyut: [değer, ...]}
   * @param {Object}   state    {boyut: {değer: true|false}} — MUTE EDİLİR
   * @param {Object}   merges   {boyut: [{name, members}]}   — MUTE EDİLİR
   * @param {Function} onChange her state/merge değişiminde çağrılır
   * @param {Object}   [opts]   {sort(dim,vals), buildGroup(dim,avail,sel),
   *                             canGroup(dim,selected), label(dim)}
   */
  function renderBubFilters(panelId, meta, state, merges, onChange, opts) {
    opts = opts || {};
    var sortFn = opts.sort || defaultSort;
    var buildGroup = opts.buildGroup || defaultBuildGroup;
    var labelFor = opts.label || function (dim) { return dim; };

    var panel = document.getElementById(panelId);
    if (!panel) return;
    panel.innerHTML = '';
    var dims = Object.keys(meta || {});
    if (!dims.length) return;

    dims.forEach(function (dim) {
      var allVals = meta[dim] || [];
      if (!allVals.length) return;

      // Boş/null değerler listeden tamamen çıkar (kaynak bileşenle aynı karar).
      var rawVals = allVals.filter(function (v) { return v !== '' && v != null; });

      if (!merges[dim]) merges[dim] = [];
      var mergedGroups = merges[dim];
      var absorbed = {};
      var availableVals = [];

      if (!state[dim]) state[dim] = {};

      function recomputeRows() {
        absorbed = {};
        mergedGroups.forEach(function (g) {
          (g.members || []).forEach(function (m) { absorbed[m] = true; });
        });
        availableVals = sortFn(dim, rawVals.filter(function (v) {
          return !absorbed[v];
        }));
        // Varsayılan: her satır seçili. Grup adları da birer satırdır.
        availableVals.forEach(function (v) {
          if (state[dim][v] === undefined) state[dim][v] = true;
        });
        mergedGroups.forEach(function (g) {
          if (state[dim][g.name] === undefined) state[dim][g.name] = true;
        });
      }
      recomputeRows();

      var wrap = document.createElement('div');
      wrap.className = 'bub-filter-dd';
      wrap.dataset.dim = dim;

      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'bub-filter-dd-btn';

      /* Görünür "satır" kimlikleri = emilmemiş ham değerler + grup adları. */
      function effectiveEntries() {
        var rows = availableVals.slice();
        mergedGroups.forEach(function (g) { rows.push(g.name); });
        return rows;
      }

      function updateBtnLabel() {
        var rows = effectiveEntries();
        var checked = rows.filter(function (v) {
          return state[dim][v] !== false;
        }).length;
        var sub;
        if (rows.length === 0) sub = '—';
        else if (checked === rows.length) sub = 'All (' + rows.length + ')';
        else if (checked === 0) sub = 'None';
        else sub = checked + ' / ' + rows.length;
        btn.textContent = '';
        var lbl = document.createElement('span');
        var strong = document.createElement('b');
        strong.textContent = labelFor(dim) + ':';
        lbl.appendChild(strong);
        lbl.appendChild(document.createTextNode(' ' + sub));
        var caret = document.createElement('span');
        caret.className = 'caret';
        caret.textContent = '▾';
        btn.appendChild(lbl);
        btn.appendChild(caret);
      }

      var popup = document.createElement('div');
      popup.className = 'bub-filter-dd-popup hidden';

      /* Satır kümesi her birleştirme/bozmada değiştiği için popup baştan
         kurulur. */
      function rebuild() {
        popup.innerHTML = '';
        recomputeRows();

        // ── All | None ──────────────────────────────────────────────────────
        var actions = document.createElement('div');
        actions.className = 'bub-filter-dd-actions';
        var allLink = document.createElement('a');
        allLink.href = '#';
        allLink.textContent = 'All';
        allLink.addEventListener('click', function (ev) {
          ev.preventDefault();
          effectiveEntries().forEach(function (v) { state[dim][v] = true; });
          rebuild(); updateBtnLabel(); onChange();
        });
        var noneLink = document.createElement('a');
        noneLink.href = '#';
        noneLink.textContent = 'None';
        noneLink.addEventListener('click', function (ev) {
          ev.preventDefault();
          effectiveEntries().forEach(function (v) { state[dim][v] = false; });
          rebuild(); updateBtnLabel(); onChange();
        });
        actions.appendChild(allLink);
        actions.appendChild(document.createTextNode(' | '));
        actions.appendChild(noneLink);
        popup.appendChild(actions);

        // ── Ham değer kutuları ──────────────────────────────────────────────
        availableVals.forEach(function (v) {
          var lblEl = document.createElement('label');
          lblEl.className = 'bub-filter-dd-opt';
          var cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.checked = state[dim][v] !== false;
          cb.addEventListener('change', function () {
            state[dim][v] = cb.checked;
            updateGroupBtn();
            updateBtnLabel();
            onChange();
          });
          lblEl.appendChild(cb);
          lblEl.appendChild(document.createTextNode(' ' + (v || '(boş)')));
          popup.appendChild(lblEl);
        });

        // ── Seçilenleri birleştir ───────────────────────────────────────────
        var groupBtn = document.createElement('button');
        groupBtn.type = 'button';
        groupBtn.className = 'bub-filter-dd-groupbtn';
        groupBtn.textContent = '＋ Group Selected';
        function selectedVals() {
          return availableVals.filter(function (v) {
            return state[dim][v] !== false;
          });
        }
        function updateGroupBtn() {
          var sel = selectedVals();
          groupBtn.disabled = opts.canGroup
            ? !opts.canGroup(dim, sel)
            : sel.length < 2;
        }
        updateGroupBtn();
        groupBtn.addEventListener('click', function () {
          var grp = buildGroup(dim, availableVals, selectedVals());
          if (!grp) return;
          // Ad çakışmasını ayırt edici ekle ile çöz (grup adı satır kimliğidir).
          if (mergedGroups.some(function (g) { return g.name === grp.name; })) {
            grp.name = grp.name + ' (' + (mergedGroups.length + 1) + ')';
          }
          mergedGroups.push(grp);
          state[dim][grp.name] = true;
          rebuild(); updateBtnLabel(); onChange();
        });
        popup.appendChild(groupBtn);

        // ── Mevcut gruplar ──────────────────────────────────────────────────
        if (mergedGroups.length) {
          var sep = document.createElement('div');
          sep.className = 'bub-filter-dd-sep';
          popup.appendChild(sep);
          var hdr = document.createElement('div');
          hdr.className = 'bub-filter-dd-grp-hdr';
          hdr.textContent = 'Groups (' + mergedGroups.length + ')';
          popup.appendChild(hdr);

          mergedGroups.slice().forEach(function (g) {
            var row = document.createElement('div');
            row.className = 'bub-filter-dd-merged';
            var nameSpan = document.createElement('label');
            nameSpan.className = 'bub-filter-dd-merged-name';
            nameSpan.title = g.members.join(', ');
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = state[dim][g.name] !== false;
            cb.addEventListener('change', function () {
              state[dim][g.name] = cb.checked;
              updateBtnLabel();
              onChange();
            });
            nameSpan.appendChild(cb);
            nameSpan.appendChild(document.createTextNode(
              ' ' + g.name + ' (' + g.members.length + ')'));
            var xBtn = document.createElement('span');
            xBtn.className = 'bub-filter-dd-merged-x';
            xBtn.textContent = '×';
            xBtn.title = 'Grubu boz';
            xBtn.addEventListener('click', function () {
              var idx = mergedGroups.indexOf(g);
              if (idx >= 0) mergedGroups.splice(idx, 1);
              delete state[dim][g.name];
              // Üyeler görünür listeye geri döner ve seçili gelir.
              g.members.forEach(function (m) { state[dim][m] = true; });
              rebuild(); updateBtnLabel(); onChange();
            });
            row.appendChild(nameSpan);
            row.appendChild(xBtn);
            popup.appendChild(row);
          });
        }
      }

      rebuild();
      updateBtnLabel();

      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        document.querySelectorAll('.bub-filter-dd-popup').forEach(function (p) {
          if (p !== popup) p.classList.add('hidden');
        });
        popup.classList.toggle('hidden');
      });
      popup.addEventListener('click', function (ev) { ev.stopPropagation(); });

      wrap.appendChild(btn);
      wrap.appendChild(popup);
      panel.appendChild(wrap);
    });
  }

  /**
   * Bir boyutun seçili (görünür) değerlerini döndürür — gruplar üyelerine
   * açılır. Hiçbiri seçili değilse boş dizi döner; ÇAĞIRAN bunu "hiçbiri"
   * olarak yorumlamalıdır ("tümü" değil).
   */
  function selectedValues(dim, meta, state, merges) {
    var st = (state || {})[dim] || {};
    var groups = ((merges || {})[dim]) || [];
    var absorbed = {};
    var out = [];
    groups.forEach(function (g) {
      (g.members || []).forEach(function (m) { absorbed[m] = true; });
      if (st[g.name] !== false) {
        (g.members || []).forEach(function (m) {
          if (out.indexOf(m) < 0) out.push(m);
        });
      }
    });
    ((meta || {})[dim] || []).forEach(function (v) {
      if (v === '' || v == null || absorbed[v]) return;
      if (st[v] !== false && out.indexOf(v) < 0) out.push(v);
    });
    return out;
  }

  /** Boyutun tüm satırları seçili mi? (filtre uygulanmasına gerek yok) */
  function isAllSelected(dim, meta, state, merges) {
    var sel = selectedValues(dim, meta, state, merges);
    var total = ((meta || {})[dim] || []).filter(function (v) {
      return v !== '' && v != null;
    });
    return sel.length === total.length;
  }

  MVP.renderBubFilters = renderBubFilters;
  MVP.bubSelected = selectedValues;
  MVP.bubIsAll = isAllSelected;

  // Dışarı tıklayınca açık açılır listeleri kapat (tek sefer bağlanır).
  if (!window.__bubFilterClickBound) {
    document.addEventListener('click', function () {
      document.querySelectorAll('.bub-filter-dd-popup').forEach(function (p) {
        p.classList.add('hidden');
      });
    });
    window.__bubFilterClickBound = true;
  }
})();
