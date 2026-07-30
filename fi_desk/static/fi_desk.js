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

  function ddmmyyyy(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-");
    return `${d}.${m}.${y}`;
  }

  function fieldValue(key) {
    const el = els[key];
    if (!el) return null;
    const wrap = wraps[key];
    if (wrap && wrap.style.display === "none") return null;
    return (el.value || "").trim() || null;
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

  /* ── tarih overlay'i (GG.AA.YYYY) ─────────────────────────────────── */
  function wrapDate(input) {
    const wrap = document.createElement("span");
    wrap.className = "fi-datewrap is-empty";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const ov = document.createElement("span");
    ov.className = "fi-dateoverlay";
    wrap.appendChild(ov);
    const sync = () => {
      ov.textContent = ddmmyyyy(input.value);
      wrap.classList.toggle("is-empty", !input.value);
    };
    input.addEventListener("focus", () => wrap.classList.add("is-editing"));
    input.addEventListener("blur", () => { wrap.classList.remove("is-editing"); sync(); });
    input.addEventListener("input", sync);
    sync();
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
    if (req === "R") {
      const star = document.createElement("span");
      star.className = "fi-req";
      star.textContent = "*";
      label.appendChild(star);
    }
    if (meta.input === "auto") {
      const note = document.createElement("span");
      note.className = "fi-auto-note";
      note.textContent = " (otomatik)";
      label.appendChild(note);
    }
    wrap.appendChild(label);

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
      el.type = "number";
      el.step = "any";
    } else if (meta.input === "auto") {
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
    if (meta.input === "date") wrapDate(el);
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
      if (sec.id === "deal") return;
      const keys = Object.keys(MATRIX.fields).filter((k) => {
        const m = MATRIX.fields[k];
        return m.section === sec.id && m.storage === "event" &&
               (pf[k] === "R" || pf[k] === "O");
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
                  (pf.PAYMENT_PLAN === "R" || pf.PAYMENT_PLAN === "O");
    $("fi-schedule-wrap").style.display = amort ? "" : "none";
    if (amort && !$("fi-sched-body").children.length) addSchedRow();
    // ESG eligibility kaskadı: type değişince seçenekler daralır
    if (els.ESG_ELIGIBILITY) {
      const cur = els.ESG_ELIGIBILITY.value;
      fillSelect(els.ESG_ELIGIBILITY, listRows("ESG_ELIGIBILITY", fieldValue("ESG_TYPE")));
      els.ESG_ELIGIBILITY.value = cur;
    }
  }

  function applyAutos() {
    if (!PRODUCT) return;
    const bank = LOOKUPS.banks.find((b) => b.BANK_NM === fieldValue("LENDER_BANK"));
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
      } else if (a.kind === "all_in_sum") {
        const rt = fieldValue("RATE_TYPE");
        const base = parseFloat(rt === "FIXED" ? fieldValue("FIXED_RATE_BPS")
                                               : fieldValue("FLOAT_SPREAD_BPS"));
        if (!isNaN(base)) {
          const cov = parseFloat(fieldValue("COVERAGE_RATE_BPS")) || 0;
          el.value = String(base + cov);
        }
      }
    });
    // LENDER_COUNTRY dropdown'ı da bankadan önerilir (kullanıcı değiştirebilir)
    if (bank && els.LENDER_COUNTRY && !els.LENDER_COUNTRY.dataset.userEdited &&
        !els.LENDER_COUNTRY.value) {
      els.LENDER_COUNTRY.value = bank.COUNTRY || "";
    }
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
    if (values) {
      tr.querySelector(".fi-sched-dt").value = String(values.PAY_DT || "").slice(0, 10);
      if (values.PRINCIPAL_AMT != null) tr.querySelector(".fi-sched-pr").value = values.PRINCIPAL_AMT;
      if (values.INTEREST_AMT != null) tr.querySelector(".fi-sched-in").value = values.INTEREST_AMT;
    }
    wrapDate(tr.querySelector(".fi-sched-dt"));
    $("fi-sched-body").appendChild(tr);
    return tr;
  }

  function collectSchedule() {
    return Array.from($("fi-sched-body").children).map((tr) => ({
      pay_dt: tr.querySelector(".fi-sched-dt").value || null,
      principal_amt: tr.querySelector(".fi-sched-pr").value || null,
      interest_amt: tr.querySelector(".fi-sched-in").value || null,
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
      renderSections(); // aynı ürünle temiz form (arka arkaya giriş akışı)
    } catch (e) {
      $("fi-status").textContent = "Hata";
      toast(String(e), true);
    } finally {
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
    PRODUCT = body.deal.PRODUCT_TYPE;
    renderSections();

    const ev = body.events[0]; // son gönderim (onay durumundan bağımsız)
    Object.keys(els).forEach((k) => {
      const meta = MATRIX.fields[k];
      const v = ev[k];
      if (v == null || v === "") return;
      if (meta.input === "date") {
        els[k].value = String(v).slice(0, 10);
        els[k].dispatchEvent(new Event("input")); // GG.AA.YYYY overlay senkronu
      } else {
        els[k].value = String(v);
      }
      // Kayıtlı değerler otomatiklerce ezilmesin
      if (autoRules().some((r) => r.field === k)) els[k].dataset.userEdited = "1";
    });
    applyConditionals();
    applyAutos();
    ($("fi-sched-body")).innerHTML = "";
    (body.schedules[String(ev.EVENT_ID)] || []).forEach((r) => addSchedRow(r));

    $("btn-submit").textContent = "Değişikliği Onaya Gönder";
    $("fi-status").textContent =
      `Düzenleme: ${EDIT_OFFER} (v${ev.EVENT_SEQ}, ${ev.APPROVAL_STATUS})`;
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
    $("btn-sched-add").addEventListener("click", () => addSchedRow());
    $("btn-sched-add").disabled = !FI_CAN_ENTER;
    $("btn-submit").addEventListener("click", submit);
    $("fi-status").textContent = FI_CAN_ENTER ? "" : "ENTRY rolü yok — form kilitli";

    if (EDIT_OFFER) await enterEditMode();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
