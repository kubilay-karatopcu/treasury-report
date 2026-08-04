/* FI Masası — Veri Girişi (Faz 1, docs/FI_DESK_SPEC.md §6).
 *
 * Form tamamen field_matrix.json'dan çizilir (bootstrap API): ürün seçilince
 * o ürünün R/O alanları bölüm kartları halinde açılır; required_if kuralları
 * alanları canlı gösterir/gizler; auto kuralları (banka→group/ülke,
 * ülke→region, Coverage=NO→0, all-in toplamı) kullanıcı yazmadıysa doldurur.
 * Sunucu (service.validate_entry) her hâlükârda otoritedir — buradaki hesap
 * yalnız UX. Tarih gösterimi GG.AA.YYYY overlay'i (CUSTOM_PAGE_DESIGN §2b:
 * focus/blur/input üçlüsü zorunlu). */
(function () {
  "use strict";

  const ENDPOINTS = JSON.parse(document.getElementById("fi-endpoints").textContent);
  const $ = (id) => document.getElementById(id);

  let MATRIX = null, LOOKUPS = null, SELF_BANK = null;
  let PRODUCT = null;           // seçili ürün kodu
  const els = {};               // alan kodu → input/select elementi
  const wraps = {};             // alan kodu → .pk-field sarmalayıcı

  // Edit modu: /fi-desk/entry?offer=FIO-... → form son gönderimle dolu
  // açılır, kimlik alanları kilitlenir, gönderim yeni PENDING event olur.
  const EDIT_OFFER = new URLSearchParams(window.location.search).get("offer");

  /* ── yardımcılar ──────────────────────────────────────────────────── */
  function toast(msg, isErr) {
    const t = $("toast-msg");
    $("toast-body").textContent = msg;
    t.classList.toggle("is-err", !!isErr);
    t.classList.add("is-open");
    setTimeout(() => t.classList.remove("is-open"), 3500);
  }

  // Tam-sayfa meşgul kilidi: uzun süren istek boyunca kullanıcı bir şeye
  // basamaz, ne olduğunu görür (2026-07-31 saha isteği).
  function busy(on, text) {
    let el = document.getElementById("fi-busy");
    if (!el) {
      el = document.createElement("div");
      el.id = "fi-busy";
      el.innerHTML = '<div class="fi-busy-box"></div>';
      document.body.appendChild(el);
    }
    el.querySelector(".fi-busy-box").textContent = text || "İşleniyor…";
    el.style.display = on ? "flex" : "none";
  }

  // Tutar alanları: binlik ayraçlı gösterim + 5e6 gibi bilimsel girişleri
  // sayıya açma. Gerçek değer daima düz sayı olarak gönderilir.
  const AMOUNT_FIELDS = new Set(["FUNDING_AMT", "USD_EQV", "TRADE_TXN_AMT",
                                 "FEE"]);

  function parseAmountText(t) {
    const s = String(t || "").trim().replace(/,/g, "");
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : NaN;
  }

  function fmtAmount(n) {
    return n.toLocaleString("en-US", { maximumFractionDigits: 4 });
  }

  function attachAmount(input) {
    input.type = "text";
    input.inputMode = "decimal";
    input.addEventListener("blur", () => {
      const n = parseAmountText(input.value);
      if (n !== null && !Number.isNaN(n)) input.value = fmtAmount(n);
    });
    input.addEventListener("focus", () => {
      const n = parseAmountText(input.value);
      if (n !== null && !Number.isNaN(n)) input.value = String(n);
    });
  }

  function fieldValue(key) {
    const el = els[key];
    if (!el) return null;
    const wrap = wraps[key];
    if (wrap && wrap.style.display === "none") return null;
    const raw = (el.value || "").trim();
    if (!raw) return null;
    if (AMOUNT_FIELDS.has(key)) {
      const n = parseAmountText(raw);
      return n === null || Number.isNaN(n) ? raw : String(n);
    }
    return raw;
  }

  /* tarih yardımcıları (Tenor + fixing date otomatiği) */
  function dayDiff(fromIso, toIso) {
    if (!fromIso || !toIso) return null;
    const ms = new Date(toIso) - new Date(fromIso);
    return Number.isFinite(ms) ? Math.round(ms / 86400000) : null;
  }

  function isoAddDays(iso, days) {
    if (!iso) return null;
    const d = new Date(iso + "T00:00:00");
    d.setDate(d.getDate() + days);
    const p = (x) => String(x).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  function condRules() {
    return MATRIX.rules.filter((r) => r.required_if);
  }
  function autoRules() {
    return MATRIX.rules.filter((r) => r.auto);
  }
  function prodFields() {
    return MATRIX.products[PRODUCT].fields;
  }
  // "D" = girişte opsiyonel, sonradan tamamlanması beklenen alan (eksikse
  // işlem listesi satırı sarı) — form akışında R/O gibi çizilir.
  function inProduct(code) {
    return code === "R" || code === "O" || code === "D";
  }

  /* ── tarih: flatpickr — görünen input DAİMA GG.AA.YYYY ─────────────
     (CUSTOM_PAGE_DESIGN §2b dock'suz tarif, MVP.initDatePicker ile aynı:
     altInput d.m.Y kullanıcıya, gerçek input ISO Y-m-d bize. Native date
     input'un tarayıcı-diline-bağlı mm/dd/yyyy segmentleri devre dışı.
     flatpickr yüklenemezse native'e düşer — form yine çalışır.) */
  function initDate(input, defaultValue) {
    if (defaultValue) input.value = String(defaultValue).slice(0, 10);
    if (typeof window.flatpickr !== "function") return;
    input._fp = window.flatpickr(input, {
      dateFormat: "Y-m-d",
      altInput: true,
      altFormat: "d.m.Y",
      allowInput: true,
      disableMobile: true,
    });
    if (input.disabled && input._fp.altInput) input._fp.altInput.disabled = true;
  }

  function setDateVal(input, iso) {
    if (input._fp) input._fp.setDate(iso || "", false);
    input.value = iso || "";
  }

  /* ── aranabilir dropdown (combobox) ───────────────────────────────────
     8+ seçenekli select'lerin üstüne yazarak-filtrelenen liste koyar.
     Select DOM'da kalır ve TEK OTORİTEDİR (fieldValue/submit oradan okur);
     seçim select.value + change event'i üretir. Programatik set sonrası
     el._combo.sync() çağrılır. */
  const COMBO_MIN_OPTIONS = 8;

  function enhanceSelect(sel) {
    const optCount = Array.from(sel.options).filter((o) => o.value).length;
    if (optCount < COMBO_MIN_OPTIONS || sel._combo) return;

    const wrap = document.createElement("div");
    wrap.className = "fi-combo";
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    sel.style.display = "none";

    const input = document.createElement("input");
    input.type = "text";
    input.className = "pk-input";
    input.autocomplete = "off";
    input.placeholder = "ara / seç…";
    wrap.appendChild(input);
    const list = document.createElement("div");
    list.className = "fi-combo-list";
    wrap.appendChild(list);

    const options = () => Array.from(sel.options).filter((o) => o.value);

    function render(filter) {
      const f = (filter || "").toLocaleLowerCase("tr");
      list.innerHTML = "";
      options()
        .filter((o) => !f || o.textContent.toLocaleLowerCase("tr").includes(f))
        .slice(0, 200)
        .forEach((o) => {
          const item = document.createElement("div");
          item.className = "fi-combo-item";
          item.textContent = o.textContent;
          if (o.value === sel.value) item.classList.add("is-active");
          item.addEventListener("mousedown", (e) => {
            e.preventDefault();
            choose(o.value, o.textContent);
          });
          list.appendChild(item);
        });
    }

    function choose(value, label) {
      sel.value = value;
      input.value = label;
      wrap.classList.remove("is-open");
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function sync() {
      const cur = options().find((o) => o.value === sel.value);
      input.value = cur ? cur.textContent : "";
      input.disabled = sel.disabled;
    }

    input.addEventListener("focus", () => {
      input.select();
      render("");
      wrap.classList.add("is-open");
    });
    input.addEventListener("input", () => {
      render(input.value);
      wrap.classList.add("is-open");
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const first = list.querySelector(".fi-combo-item");
        if (first) first.dispatchEvent(new Event("mousedown"));
      } else if (e.key === "Escape") {
        wrap.classList.remove("is-open");
        sync();
      }
    });
    input.addEventListener("blur", () => {
      setTimeout(() => { wrap.classList.remove("is-open"); sync(); }, 120);
    });

    sel._combo = { sync: sync };
    sync();
  }

  function syncCombo(sel) {
    if (sel && sel._combo) sel._combo.sync();
  }

  /* ── additional cost satırları (cost_list) ────────────────────────────
     Tip dropdown'u (ADDITIONAL_COST_TYPE lookup'ı) + bps girişi + satır
     ekle/sil. Otorite gizli input'tur: değeri {"TIP": bps} JSON map'i —
     sunucu da bu şekilde saklar (ADDITIONAL_COSTS kolonu). Her değişimde
     change event'i yayılır ki all-in otomatiği toplamı güncellesin. */
  function buildCostList(key, meta) {
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.id = "fi-f-" + key;

    const box = document.createElement("div");
    box.className = "fi-costs";
    const rowsHost = document.createElement("div");
    box.appendChild(rowsHost);
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "pk-btn pk-btn--sm";
    addBtn.textContent = "+ Maliyet Ekle";
    addBtn.disabled = !FI_CAN_ENTER;
    box.appendChild(addBtn);
    box.appendChild(hidden);

    const types = () => (LOOKUPS.lists[meta.list] || [])
      .map((r) => ({ value: r.VALUE_CD, label: r.LABEL }));

    function syncHidden() {
      const map = {};
      rowsHost.querySelectorAll(".fi-cost-row").forEach((tr) => {
        const cd = tr.querySelector("select").value;
        const n = parseAmountText(tr.querySelector("input").value);
        if (cd && n !== null && !Number.isNaN(n)) map[cd] = n;
      });
      hidden.value = Object.keys(map).length ? JSON.stringify(map) : "";
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function addRow(code, bps) {
      const tr = document.createElement("div");
      tr.className = "fi-cost-row";
      const sel = document.createElement("select");
      sel.className = "pk-input";
      fillSelect(sel, types(), "— tip seçin —");
      if (code) sel.value = code;
      const inp = document.createElement("input");
      inp.type = "number";
      inp.step = "any";
      inp.className = "pk-input";
      inp.placeholder = "bps";
      if (bps != null) inp.value = String(bps);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "fi-sched-remove";
      del.title = "Satırı sil";
      del.textContent = "✕";
      del.addEventListener("click", () => { tr.remove(); syncHidden(); });
      sel.addEventListener("change", syncHidden);
      inp.addEventListener("input", syncHidden);
      if (!FI_CAN_ENTER) { sel.disabled = true; inp.disabled = true; del.disabled = true; }
      tr.appendChild(sel); tr.appendChild(inp); tr.appendChild(del);
      rowsHost.appendChild(tr);
      return tr;
    }

    addBtn.addEventListener("click", () => addRow());
    hidden._costs = {
      set(map) {
        rowsHost.innerHTML = "";
        Object.keys(map || {}).forEach((cd) => addRow(cd, map[cd]));
        syncHidden();
      },
    };
    return { el: hidden, widget: box };
  }

  /* ── müşteri typeahead (customer) ─────────────────────────────────────
     Importer artık lehtar listesinden gelmez — bizim müşterimizdir ve
     sunucuda aranır (/api/customers, EDW müşteri tablosu ya da fallback).
     3+ karakterde debounce'lu önek araması; öneriye tıklanınca değer dolar,
     serbest metin de kabul (sunucu doğrulaması metin alanı gibi davranır). */
  function attachCustomerSearch(input) {
    const wrap = document.createElement("div");
    wrap.className = "fi-combo";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const list = document.createElement("div");
    list.className = "fi-combo-list";
    wrap.appendChild(list);

    let timer = null, lastQ = "";
    async function search() {
      const q = input.value.trim();
      lastQ = q;
      if (q.length < 3) { wrap.classList.remove("is-open"); return; }
      let body;
      try {
        const res = await fetch(ENDPOINTS.customers + "?q=" + encodeURIComponent(q));
        body = await res.json();
      } catch (e) { return; }
      if (!body.ok || input.value.trim() !== lastQ) return;
      list.innerHTML = "";
      (body.rows || []).forEach((name) => {
        const item = document.createElement("div");
        item.className = "fi-combo-item";
        item.textContent = name;
        item.addEventListener("mousedown", (e) => {
          e.preventDefault();
          input.value = name;
          wrap.classList.remove("is-open");
          input.dispatchEvent(new Event("change", { bubbles: true }));
        });
        list.appendChild(item);
      });
      wrap.classList.toggle("is-open", !!(body.rows || []).length);
    }
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(search, 250);
    });
    input.addEventListener("blur", () => {
      setTimeout(() => wrap.classList.remove("is-open"), 120);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") wrap.classList.remove("is-open");
    });
  }

  /* ── alan üretimi ─────────────────────────────────────────────────── */
  function makeOption(value, label) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = label;
    return o;
  }

  function fillSelect(sel, rows, placeholder) {
    sel.innerHTML = "";
    sel.appendChild(makeOption("", placeholder || "— seçin —"));
    rows.forEach((r) => sel.appendChild(makeOption(r.value, r.label)));
  }

  function listRows(name, parentValue) {
    const rows = (LOOKUPS.lists[name] || []);
    return rows
      .filter((r) => !r.PARENT_CD || !parentValue || r.PARENT_CD === parentValue)
      .map((r) => ({ value: r.VALUE_CD, label: r.LABEL }));
  }

  function bankRows() {
    return LOOKUPS.banks.map((b) => ({ value: b.BANK_NM, label: b.BANK_NM }));
  }

  function buildField(key, meta, req) {
    const wrap = document.createElement("div");
    wrap.className = "pk-field" + (meta.input === "text" ? " is-wide" : "");
    const label = document.createElement("span");
    label.className = "pk-label";
    label.textContent = meta.label;
    // Etiketler İngilizce — CSS uppercase'i sayfa dili (tr) ile 'i'yi 'İ'
    // yapıyordu (FUNDİNG); lang=en doğru büyük harf eşlemesini seçer.
    label.lang = "en";
    if (req === "R") {
      const star = document.createElement("span");
      star.className = "fi-req";
      star.textContent = "*";
      label.appendChild(star);
    }
    if (req === "D") {
      const note = document.createElement("span");
      note.className = "fi-auto-note";
      note.textContent = " (sonradan tamamlanabilir)";
      note.lang = "tr";
      label.appendChild(note);
    }
    if (meta.input === "auto" || meta.input === "readonly") {
      const note = document.createElement("span");
      note.className = "fi-auto-note";
      note.textContent = " (otomatik)";
      label.appendChild(note);
    }
    wrap.appendChild(label);

    if (meta.input === "cost_list") {
      wrap.classList.add("is-wide");
      const built = buildCostList(key, meta);
      wrap.appendChild(built.widget);
      els[key] = built.el;
      wraps[key] = wrap;
      built.el.addEventListener("change", onFieldChange);
      return wrap;
    }

    let el;
    if (meta.input === "lookup_bank") {
      el = document.createElement("select");
      fillSelect(el, bankRows());
    } else if (meta.input === "list") {
      el = document.createElement("select");
      fillSelect(el, listRows(meta.list));
    } else if (meta.input === "enum") {
      el = document.createElement("select");
      fillSelect(el, meta.options.map((o) => ({ value: o, label: o })));
    } else if (meta.input === "date") {
      el = document.createElement("input");
      el.type = "date";
    } else if (meta.input === "number") {
      el = document.createElement("input");
      if (AMOUNT_FIELDS.has(key)) {
        attachAmount(el);
      } else {
        el.type = "number";
        el.step = "any";
      }
    } else if (meta.input === "auto" || meta.input === "readonly") {
      el = document.createElement("input");
      el.type = "text";
      el.readOnly = true;
    } else {
      el = document.createElement("input");
      el.type = "text";
    }
    el.className = "pk-input";
    el.id = "fi-f-" + key;
    el.disabled = !FI_CAN_ENTER && meta.input !== "auto";
    wrap.appendChild(el);

    els[key] = el;
    wraps[key] = wrap;
    if (meta.input === "date") initDate(el);
    if (meta.input === "customer") {
      el.placeholder = "müşteri ara (en az 3 karakter)…";
      attachCustomerSearch(el);
    }
    if (el.tagName === "SELECT") enhanceSelect(el);
    el.addEventListener("change", onFieldChange);
    return wrap;
  }

  /* ── bölüm çizimi (drill-down adım 2+) ────────────────────────────── */
  function renderSections() {
    const host = $("fi-sections");
    host.innerHTML = "";
    Object.keys(els).forEach((k) => delete els[k]);
    Object.keys(wraps).forEach((k) => delete wraps[k]);
    $("fi-schedule-wrap").style.display = "none";
    $("fi-errors").innerHTML = "";
    if (!PRODUCT) {
      $("btn-submit").disabled = true;
      return;
    }
    const pf = prodFields();
    MATRIX.sections.forEach((sec) => {
      // "deal" bölümünün storage=deal alanları (borrower/ürün/açıklama)
      // statik kartta; storage=event olanlar (DEAL_STATUS) BURADA çizilir —
      // atlanınca form Deal Status'suz kalıyordu (2026-07-31 saha hatası).
      const keys = Object.keys(MATRIX.fields).filter((k) => {
        const m = MATRIX.fields[k];
        if (m.section !== sec.id) return false;
        if (m.storage === "event") return inProduct(pf[k]);
        // Türetilen görünüm alanları (TENOR): formda salt-okunur gösterilir,
        // gönderilmez (deal bölümündeki REPORTING_STATUS girişte çizilmez).
        return m.storage === "derived" && m.input === "readonly" &&
               sec.id !== "deal";
      });
      if (!keys.length) return;
      const card = document.createElement("div");
      card.className = "pk-card";
      card.innerHTML =
        '<div class="pk-card__head"><span class="pk-card__title"></span></div>';
      card.querySelector(".pk-card__title").textContent = sec.title;
      const grid = document.createElement("div");
      grid.className = "fi-fields";
      keys.forEach((k) => grid.appendChild(buildField(k, MATRIX.fields[k], pf[k])));
      card.appendChild(grid);
      host.appendChild(card);
    });

    // Deal Status ürün akışındaysa deal kartında değil ilk bölümde durur;
    // varsayılanı BIDDING (kullanıcı WON/LOST'a çevirebilir).
    if (els.DEAL_STATUS && !els.DEAL_STATUS.value) els.DEAL_STATUS.value = "BIDDING";

    applyConditionals();
    applyAutos();
    $("btn-submit").disabled = !FI_CAN_ENTER;
  }

  /* ── koşullar + otomatikler ───────────────────────────────────────── */
  function applyConditionals() {
    condRules().forEach((r) => {
      const wrap = wraps[r.field];
      if (!wrap) return;
      const parentVal = fieldValue(r.required_if.field);
      const show = parentVal === r.required_if.equals;
      wrap.style.display = show ? "" : "none";
    });
    // Ödeme planı kartı (PAYMENT_PLAN subtable — required_if AMORTIZED)
    const pf = PRODUCT ? prodFields() : {};
    const amort = fieldValue("REPAYMENT_SCHEDULE") === "AMORTIZED" &&
                  inProduct(pf.PAYMENT_PLAN);
    $("fi-schedule-wrap").style.display = amort ? "" : "none";
    if (amort && !$("fi-sched-body").children.length) addSchedRow();
    // ESG eligibility kaskadı: type değişince seçenekler daralır
    if (els.ESG_ELIGIBILITY) {
      const cur = els.ESG_ELIGIBILITY.value;
      fillSelect(els.ESG_ELIGIBILITY, listRows("ESG_ELIGIBILITY", fieldValue("ESG_TYPE")));
      els.ESG_ELIGIBILITY.value = cur;
      syncCombo(els.ESG_ELIGIBILITY);
    }
  }

  function applyAutos() {
    if (!PRODUCT) return;
    const bank = LOOKUPS.banks.find((b) => b.BANK_NM === fieldValue("LENDER_BANK"));
    // ÖNCE bankadan ülke — region kuralı (list_attr) bu değeri okur; eski
    // sırada ülke kurallardan SONRA dolduğundan region hep bir tur geride
    // kalıyordu (2026-07-31 saha bulgusu: JP Morgan → USA dolup region boş).
    if (bank && els.LENDER_COUNTRY && els.LENDER_COUNTRY.dataset.userEdited !== "1") {
      if (els.LENDER_COUNTRY.value !== (bank.COUNTRY || "")) {
        els.LENDER_COUNTRY.value = bank.COUNTRY || "";
        syncCombo(els.LENDER_COUNTRY);
      }
    }
    autoRules().forEach((r) => {
      const el = els[r.field];
      if (!el || el.dataset.userEdited === "1") return;
      const a = r.auto;
      if (a.kind === "bank_attr" && bank) {
        el.value = bank[a.attr] || "";
      } else if (a.kind === "list_attr") {
        const src = fieldValue(a.source_field);
        const row = (LOOKUPS.lists[a.list] || []).find((x) => x.VALUE_CD === src);
        el.value = row ? (row[a.attr] || "") : "";
      } else if (a.kind === "zero_if") {
        if (fieldValue(a.field) === a.equals && !el.value) el.value = "0";
      } else if (a.kind === "date_offset") {
        const src = fieldValue(a.source_field);
        const iso = isoAddDays(src, a.days || 0);
        if (iso && el.value !== iso) setDateVal(el, iso);
      } else if (a.kind === "date_diff") {
        const d = dayDiff(fieldValue(a.from), fieldValue(a.to));
        el.value = d === null ? "" : String(d);
      } else if (a.kind === "all_in_sum") {
        const rt = fieldValue("RATE_TYPE");
        const base = parseFloat(rt === "FIXED" ? fieldValue("FIXED_RATE_BPS")
                                               : fieldValue("FLOAT_SPREAD_BPS"));
        if (!isNaN(base)) {
          const cov = parseFloat(fieldValue("COVERAGE_RATE_BPS")) || 0;
          el.value = String(base + cov + costsTotal());
        }
      } else if (a.kind === "all_in_label") {
        // All-in string gösterimi: FLOATING → "{base etiketi} + {allin} bps",
        // FIXED → "{allin} bps (Fixed)". Sunucu (validate_entry) otoritedir;
        // buradaki hesap yalnız canlı önizleme.
        const allin = parseFloat(fieldValue("ALL_IN_RATE_BPS"));
        if (isNaN(allin)) { el.value = ""; return; }
        const bps = String(+allin.toFixed(2));
        if (fieldValue("RATE_TYPE") === "FLOATING") {
          const sel = els.FLOAT_BASE_RATE;
          const opt = sel && sel.value
            ? Array.from(sel.options).find((o) => o.value === sel.value) : null;
          el.value = opt ? `${opt.textContent} + ${bps} bps` : `${bps} bps`;
        } else {
          el.value = `${bps} bps (Fixed)`;
        }
      }
    });
  }

  function costsTotal() {
    const raw = fieldValue("ADDITIONAL_COSTS");
    if (!raw) return 0;
    try {
      const map = JSON.parse(raw);
      return Object.values(map).reduce(
        (s, v) => s + (typeof v === "number" ? v : 0), 0);
    } catch (e) { return 0; }
  }

  function onFieldChange(ev) {
    const key = ev.target.id.replace("fi-f-", "");
    const meta = MATRIX.fields[key];
    if (meta && meta.input !== "auto") {
      // Kullanıcının elle doldurduğu auto-hedefleri ezmemek için işaretle
      if (autoRules().some((r) => r.field === key)) ev.target.dataset.userEdited = "1";
    }
    applyConditionals();
    applyAutos();
  }

  /* ── ödeme planı satırları ────────────────────────────────────────── */
  function addSchedRow(values) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td><input type="date" class="pk-input fi-sched-dt"></td>' +
      '<td><input type="number" step="any" class="pk-input fi-sched-pr"></td>' +
      '<td><input type="number" step="any" class="pk-input fi-sched-in"></td>' +
      '<td><button type="button" class="fi-sched-remove" title="Satırı sil">✕</button></td>';
    tr.querySelector(".fi-sched-remove").addEventListener("click", () => tr.remove());
    tr.querySelectorAll("input").forEach((i) => { i.disabled = !FI_CAN_ENTER; });
    attachAmount(tr.querySelector(".fi-sched-pr"));
    attachAmount(tr.querySelector(".fi-sched-in"));
    if (values) {
      if (values.PRINCIPAL_AMT != null) tr.querySelector(".fi-sched-pr").value = fmtAmount(Number(values.PRINCIPAL_AMT));
      if (values.INTEREST_AMT != null) tr.querySelector(".fi-sched-in").value = fmtAmount(Number(values.INTEREST_AMT));
    }
    $("fi-sched-body").appendChild(tr);
    initDate(tr.querySelector(".fi-sched-dt"),
             values ? values.PAY_DT : null);
    return tr;
  }

  function collectSchedule() {
    const num = (el) => {
      const n = parseAmountText(el.value);
      return n === null ? null : (Number.isNaN(n) ? el.value : String(n));
    };
    return Array.from($("fi-sched-body").children).map((tr) => ({
      pay_dt: tr.querySelector(".fi-sched-dt").value || null,
      principal_amt: num(tr.querySelector(".fi-sched-pr")),
      interest_amt: num(tr.querySelector(".fi-sched-in")),
    })).filter((r) => r.pay_dt || r.principal_amt || r.interest_amt);
  }

  /* ── gönderim ─────────────────────────────────────────────────────── */
  function showErrors(errors) {
    const ul = $("fi-errors");
    ul.innerHTML = "";
    (errors || []).forEach((e) => {
      const li = document.createElement("li");
      li.textContent = (e.field ? e.field + ": " : "") + e.message;
      ul.appendChild(li);
    });
  }

  async function submit() {
    if (!PRODUCT) return;
    showErrors([]);
    const fields = {};
    Object.keys(els).forEach((k) => { fields[k] = fieldValue(k); });
    const payload = {
      deal: {
        borrower_bank: $("f-borrower").value,
        product_type: PRODUCT,
        deal_label: $("f-label").value.trim() || null,
      },
      fields: fields,
      schedule: collectSchedule(),
    };
    const url = EDIT_OFFER
      ? ENDPOINTS.offer_events.replace("__OID__", encodeURIComponent(EDIT_OFFER))
      : ENDPOINTS.entries;
    const btn = $("btn-submit");
    btn.disabled = true;
    $("fi-status").textContent = "Gönderiliyor…";
    busy(true, EDIT_OFFER ? "Değişiklik onaya gönderiliyor…"
                          : "Kayıt onaya gönderiliyor…");
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json();
      if (!res.ok || !body.ok) {
        if (body.errors) {
          showErrors(body.errors);
          $("fi-status").textContent = "Doğrulama hatası";
          toast("Eksik/hatalı alanlar var", true);
        } else {
          $("fi-status").textContent = "Hata";
          toast(body.error || "Kaydedilemedi", true);
        }
        return;
      }
      (body.warnings || []).forEach((w) => console.info("fi_desk uyarı:", w));
      if (EDIT_OFFER) {
        $("fi-status").textContent =
          `Değişiklik onaya gönderildi (${body.event_type})`;
        toast("Değişiklik onaya gönderildi.");
        if (ENDPOINTS.records_page) {
          setTimeout(() => { window.location.href = ENDPOINTS.records_page; }, 900);
        }
        return;
      }
      $("fi-status").textContent =
        `Onaya gönderildi — ${body.deal_id} / ${body.offer_id}`;
      toast("Kayıt onaya gönderildi.");
      // Form SIFIRDAN başlar: ürün seçimi de temizlenir (2026-07-31 isteği)
      $("f-product").value = "";
      PRODUCT = null;
      syncCombo($("f-product"));
      $("f-label").value = "";
      if (SELF_BANK) { $("f-borrower").value = SELF_BANK; syncCombo($("f-borrower")); }
      renderSections();
    } catch (e) {
      $("fi-status").textContent = "Hata";
      toast(String(e), true);
    } finally {
      busy(false);
      btn.disabled = !FI_CAN_ENTER;
    }
  }

  /* ── edit modu ────────────────────────────────────────────────────── */
  async function enterEditMode() {
    const url = ENDPOINTS.offer_detail.replace("__OID__", encodeURIComponent(EDIT_OFFER));
    const res = await fetch(url);
    const body = await res.json();
    if (!body.ok) {
      $("fi-status").textContent = "Teklif yüklenemedi: " + (body.error || "");
      return;
    }
    $("f-borrower").value = body.deal.BORROWER_BANK;
    $("f-product").value = body.deal.PRODUCT_TYPE;
    $("f-label").value = body.deal.DEAL_LABEL || "";
    // Kimlik alanları edit'te değişmez (deal başlığı DB otoritesidir)
    ["f-borrower", "f-product", "f-label"].forEach((id) => { $(id).disabled = true; });
    syncCombo($("f-borrower"));
    syncCombo($("f-product"));
    PRODUCT = body.deal.PRODUCT_TYPE;
    renderSections();

    const ev = body.events[0]; // son gönderim (onay durumundan bağımsız)
    Object.keys(els).forEach((k) => {
      const meta = MATRIX.fields[k];
      const v = ev[k];
      if (v == null || v === "") return;
      if (meta.input === "cost_list") {
        try { els[k]._costs.set(JSON.parse(v)); } catch (e) { /* bozuk kayıt — boş başlar */ }
        return;
      }
      if (meta.input === "date") {
        setDateVal(els[k], String(v).slice(0, 10));
      } else if (AMOUNT_FIELDS.has(k)) {
        const n = Number(v);
        els[k].value = Number.isFinite(n) ? fmtAmount(n) : String(v);
      } else {
        els[k].value = String(v);
      }
      // Kayıtlı değerler otomatiklerce ezilmesin
      if (autoRules().some((r) => r.field === k)) els[k].dataset.userEdited = "1";
    });
    applyConditionals();
    applyAutos();
    Object.values(els).forEach((el) => syncCombo(el));
    ($("fi-sched-body")).innerHTML = "";
    (body.schedules[String(ev.EVENT_ID)] || []).forEach((r) => addSchedRow(r));

    $("btn-submit").textContent = "Değişikliği Onaya Gönder";
    $("fi-status").textContent =
      `Düzenleme: ${EDIT_OFFER} (v${ev.EVENT_SEQ}, ${ev.APPROVAL_STATUS})`;

    // Silme yalnız edit ekranında (2026-08-04 saha isteği #3): talep onay
    // akışına girer — onaylanınca kayıt listeden düşer, geçmiş silinmez.
    const delBtn = $("btn-delete");
    if (delBtn && FI_CAN_ENTER) delBtn.style.display = "";
  }

  async function requestDelete() {
    if (!EDIT_OFFER) return;
    if (!confirm("Bu teklif için SİLME talebi oluşturulacak ve onaya " +
                 "gidecek. Devam edilsin mi?")) return;
    busy(true, "Silme talebi gönderiliyor…");
    try {
      const url = ENDPOINTS.offer_delete.replace(
        "__OID__", encodeURIComponent(EDIT_OFFER));
      const res = await fetch(url, { method: "POST" });
      const body = await res.json();
      if (!body.ok) { toast(body.error || "Silme talebi başarısız", true); return; }
      toast("Silme talebi onaya gönderildi.");
      if (ENDPOINTS.records_page) {
        setTimeout(() => { window.location.href = ENDPOINTS.records_page; }, 900);
      }
    } catch (e) {
      toast(String(e), true);
    } finally {
      busy(false);
    }
  }

  /* ── açılış ───────────────────────────────────────────────────────── */
  async function boot() {
    let body;
    try {
      const res = await fetch(ENDPOINTS.bootstrap);
      body = await res.json();
      if (!body.ok) throw new Error(body.error || "bootstrap başarısız");
    } catch (e) {
      $("fi-status").textContent = "Yüklenemedi: " + e;
      return;
    }
    MATRIX = body.matrix;
    LOOKUPS = body.lookups;
    SELF_BANK = body.self_bank;

    const borrower = $("f-borrower");
    fillSelect(borrower, bankRows());
    if (SELF_BANK) borrower.value = SELF_BANK; // varsayılan biz (karar #2)

    const product = $("f-product");
    Object.keys(MATRIX.products).forEach((k) => {
      product.appendChild(makeOption(k, MATRIX.products[k].label));
    });
    product.addEventListener("change", () => {
      PRODUCT = product.value || null;
      renderSections();
    });

    ["f-borrower", "f-product", "f-label"].forEach((id) => {
      $(id).disabled = !FI_CAN_ENTER;
    });
    enhanceSelect(borrower);
    enhanceSelect(product);
    $("btn-sched-add").addEventListener("click", () => addSchedRow());
    $("btn-sched-add").disabled = !FI_CAN_ENTER;
    $("btn-submit").addEventListener("click", submit);
    if ($("btn-delete")) $("btn-delete").addEventListener("click", requestDelete);
    $("fi-status").textContent = FI_CAN_ENTER ? "" : "ENTRY rolü yok — form kilitli";

    if (EDIT_OFFER) {
      busy(true, "Kayıt yükleniyor…");
      try {
        await enterEditMode();
      } finally {
        busy(false);
      }
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
