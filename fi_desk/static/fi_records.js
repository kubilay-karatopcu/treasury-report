/* FI Masası — İşlemler listesi (Faz 2, docs/FI_DESK_SPEC.md §6).
 * AG-Grid liste + satır detayı overlay'i (timeline, ödeme planı, onay/red).
 * Tarih gösterimi GG.AA.YYYY; overlay ✕ / Esc / dış tık ile kapanır. */
(function () {
  "use strict";

  const ENDPOINTS = JSON.parse(document.getElementById("fi-endpoints").textContent);
  const $ = (id) => document.getElementById(id);

  let GRID = null;
  let ROWS = [];
  let CURRENT_OFFER = null;

  /* ── yardımcılar ──────────────────────────────────────────────────── */
  function toast(msg, isErr) {
    const t = $("toast-msg");
    $("toast-body").textContent = msg;
    t.classList.toggle("is-err", !!isErr);
    t.classList.add("is-open");
    setTimeout(() => t.classList.remove("is-open"), 3500);
  }

  function dt(v) {           // "2026-07-20 00:00:00" → "20.07.2026"
    if (!v) return "";
    const d = String(v).slice(0, 10).split("-");
    return d.length === 3 ? `${d[2]}.${d[1]}.${d[0]}` : String(v);
  }
  function ts(v) {           // tarih + saat
    if (!v) return "";
    return dt(v) + " " + String(v).slice(11, 16);
  }
  function amt(v) {
    return v == null ? "" : Number(v).toLocaleString("tr-TR", { maximumFractionDigits: 0 });
  }

  function offerUrl(tpl, oid) { return tpl.replace("__OID__", encodeURIComponent(oid)); }

  /* ── grid ─────────────────────────────────────────────────────────── */
  const COLS = [
    { headerName: "Durum", field: "ROW_STATE", width: 130, pinned: "left",
      valueGetter: (p) => p.data.ROW_STATE === "PENDING_NEW"
        ? "ONAY BEKLİYOR"
        : (p.data.PENDING_COUNT > 0 ? "GÜNCELLEME BEKLİYOR" : "ONAYLI"),
      cellStyle: (p) => p.value === "ONAYLI" ? null : { color: "var(--gold)" } },
    { headerName: "Ürün", field: "PRODUCT_LABEL", width: 150, filter: "agSetColumnFilter" },
    { headerName: "Borrower", field: "BORROWER_BANK", width: 120, filter: "agSetColumnFilter" },
    { headerName: "Lender", field: "LENDER_BANK", width: 150, filter: "agSetColumnFilter" },
    { headerName: "Deal Status", field: "DEAL_STATUS", width: 110, filter: "agSetColumnFilter" },
    { headerName: "Reporting", field: "REPORTING_STATUS", width: 110, filter: "agSetColumnFilter",
      valueGetter: (p) => p.data.REPORTING_STATUS || "—" },
    { headerName: "CCY", field: "CURRENCY", width: 80, filter: "agSetColumnFilter" },
    { headerName: "Tutar", field: "FUNDING_AMT", width: 130, type: "rightAligned",
      valueFormatter: (p) => amt(p.value) },
    { headerName: "USD eqv.", field: "USD_EQV", width: 130, type: "rightAligned",
      valueFormatter: (p) => amt(p.value) },
    { headerName: "Value", field: "VALUE_DT", width: 105, valueFormatter: (p) => dt(p.value) },
    { headerName: "Maturity", field: "MATURITY_DT", width: 105, valueFormatter: (p) => dt(p.value) },
    { headerName: "Tenor (g)", field: "TENOR_DAYS", width: 95, type: "rightAligned",
      valueFormatter: (p) => p.value == null ? "" : String(Math.round(p.value)) },
    { headerName: "All-in (bps)", field: "ALL_IN_RATE_BPS", width: 110, type: "rightAligned",
      valueFormatter: (p) => p.value == null ? "" : Number(p.value).toFixed(0) },
    { headerName: "Son İşlem", field: "EVENT_TS", width: 140, sort: "desc",
      valueFormatter: (p) => ts(p.value) },
    { headerName: "Deal ID", field: "DEAL_ID", width: 160 },
    { headerName: "Offer ID", field: "OFFER_ID", width: 160 },
  ];

  function visibleRows() {
    if (!$("chk-pending-only").checked) return ROWS;
    return ROWS.filter((r) => r.ROW_STATE === "PENDING_NEW" || r.PENDING_COUNT > 0);
  }

  function refreshGrid() {
    const rows = visibleRows();
    GRID.setGridOption("rowData", rows);
    $("grid-info").textContent =
      `${rows.length} kayıt · ${ROWS.filter((r) => r.ROW_STATE === "PENDING_NEW" || r.PENDING_COUNT > 0).length} onay bekliyor`;
  }

  async function load() {
    const res = await fetch(ENDPOINTS.records);
    const body = await res.json();
    if (!body.ok) { toast(body.error || "Liste yüklenemedi", true); return; }
    ROWS = body.rows;
    refreshGrid();
  }

  /* ── detay overlay ────────────────────────────────────────────────── */
  const FIELD_LABELS = {
    LENDER_BANK: "Lender", LENDER_COUNTRY: "Lender Country",
    LENDER_REGION: "Lender Region", GROUP_COMPANY: "Group Company",
    OFFER_DT: "Offer Date", CURRENCY: "Currency", FUNDING_AMT: "Tutar",
    USD_EQV: "USD eqv.", VALUE_DT: "Value Date", MATURITY_DT: "Maturity",
    REPAYMENT_SCHEDULE: "Repayment", COVERAGE_FLG: "Coverage",
    COVERAGE_PROVIDER: "Coverage Provider", RATE_TYPE: "Rate Type",
    FIXED_RATE_BPS: "Fixed (bps)", FLOAT_BASE_RATE: "Base Rate",
    FLOAT_SPREAD_BPS: "Spread (bps)", COVERAGE_RATE_BPS: "Coverage (bps)",
    FEE: "Fee", ADDITIONAL_FEE_COST: "Ek Maliyet",
    ALL_IN_RATE_BPS: "All-in (bps)", ALL_IN_FIXED_USD_RATE: "All-in USD",
    TRADE_TXN_AMT: "Trade Amount", IMPORTER: "Importer", EXPORTER: "Exporter",
    BUSINESS_SEGMENT: "Segment", REFERENCE_NO: "Referans",
    SUSTAINABILITY_FLG: "ESG", ESG_TYPE: "ESG Type",
    ESG_ELIGIBILITY: "Eligibility", NOTES: "Notlar",
  };
  const DATEISH = new Set(["OFFER_DT", "VALUE_DT", "MATURITY_DT"]);
  const AMTISH = new Set(["FUNDING_AMT", "USD_EQV", "TRADE_TXN_AMT"]);

  function renderCurrent(cur) {
    const dl = $("ov-current");
    dl.innerHTML = "";
    if (!cur) {
      dl.innerHTML = "<dt>—</dt><dd>Henüz onaylı sürüm yok</dd>";
      return;
    }
    const push = (k, v) => {
      if (v == null || v === "") return;
      const dtEl = document.createElement("dt");
      dtEl.textContent = k;
      const ddEl = document.createElement("dd");
      ddEl.textContent = v;
      dl.appendChild(dtEl); dl.appendChild(ddEl);
    };
    push("Reporting Status", cur.REPORTING_STATUS);
    push("Deal Status", cur.DEAL_STATUS);
    Object.keys(FIELD_LABELS).forEach((k) => {
      let v = cur[k];
      if (v == null || v === "") return;
      if (DATEISH.has(k)) v = dt(v);
      else if (AMTISH.has(k)) v = amt(v);
      push(FIELD_LABELS[k], v);
    });
    push("Tenor (gün)", cur.TENOR_DAYS != null ? String(Math.round(cur.TENOR_DAYS)) : null);
  }

  function renderSchedule(detail) {
    const table = $("ov-sched");
    table.innerHTML = "";
    const evId = detail.current ? detail.current.EVENT_ID
      : (detail.events.length ? detail.events[0].EVENT_ID : null);
    const rows = evId != null ? (detail.schedules[String(evId)] || []) : [];
    $("ov-sched-note").textContent = rows.length
      ? (detail.current ? "onaylı sürümün planı" : "son gönderimin planı") : "";
    if (!rows.length) {
      table.innerHTML = "<tbody><tr><td style='text-align:left;'>Plan yok (Bullet)</td></tr></tbody>";
      return;
    }
    table.innerHTML =
      "<thead><tr><th style='text-align:left;'>Tarih</th><th>Anapara</th><th>Faiz</th></tr></thead>";
    const tb = document.createElement("tbody");
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td style="text-align:left;">${dt(r.PAY_DT)}</td>` +
        `<td>${amt(r.PRINCIPAL_AMT)}</td><td>${amt(r.INTEREST_AMT)}</td>`;
      tb.appendChild(tr);
    });
    table.appendChild(tb);
  }

  function approvalCell(ev) {
    if (ev.APPROVAL_STATUS === "PENDING") {
      if (!FI_CAN_APPROVE) return '<span class="is-pending">BEKLİYOR</span>';
      return '<span class="is-pending">BEKLİYOR</span> ' +
        `<button class="pk-btn pk-btn--sm" data-approve="${ev.EVENT_ID}">Onayla</button> ` +
        `<button class="pk-btn pk-btn--sm" data-reject="${ev.EVENT_ID}">Reddet</button>`;
    }
    if (ev.APPROVAL_STATUS === "REJECTED") {
      const why = ev.REJECT_REASON ? ` — ${ev.REJECT_REASON}` : "";
      return `<span class="is-rejected">RED (${ev.APPROVED_BY || "?"})${why}</span>`;
    }
    return `ONAYLI (${ev.APPROVED_BY || "?"} · ${ts(ev.APPROVED_TS)})`;
  }

  function summarize(ev) {
    const bits = [];
    if (ev.DEAL_STATUS) bits.push(ev.DEAL_STATUS);
    if (ev.FUNDING_AMT != null) bits.push(`${amt(ev.FUNDING_AMT)} ${ev.CURRENCY || ""}`);
    if (ev.ALL_IN_RATE_BPS != null) bits.push(`all-in ${Number(ev.ALL_IN_RATE_BPS).toFixed(0)}bps`);
    if (ev.VALUE_DT) bits.push(`val ${dt(ev.VALUE_DT)}`);
    return bits.join(" · ");
  }

  function renderTimeline(detail) {
    const table = $("ov-timeline");
    table.innerHTML =
      "<thead><tr><th>#</th><th>Tip</th><th>Zaman</th><th>Kullanıcı</th>" +
      "<th>Özet</th><th>Onay</th></tr></thead>";
    const tb = document.createElement("tbody");
    detail.events.forEach((ev) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${ev.EVENT_SEQ}</td><td>${ev.EVENT_TYPE}</td>` +
        `<td>${ts(ev.EVENT_TS)}</td><td>${ev.EVENT_USER || ""}</td>` +
        `<td>${summarize(ev)}</td><td>${approvalCell(ev)}</td>`;
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    tb.querySelectorAll("[data-approve]").forEach((b) =>
      b.addEventListener("click", () => decide(b.dataset.approve, "approve")));
    tb.querySelectorAll("[data-reject]").forEach((b) =>
      b.addEventListener("click", () => decide(b.dataset.reject, "reject")));
  }

  async function decide(eventId, action) {
    let reason = null;
    if (action === "reject") {
      reason = prompt("Red gerekçesi (opsiyonel):") || null;
      if (reason === null && !confirm("Gerekçesiz reddedilsin mi?")) return;
    }
    const url = ENDPOINTS.approval.replace("__EID__", eventId);
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, reason: reason }),
    });
    const body = await res.json();
    if (!body.ok) { toast(body.error || "İşlem başarısız", true); return; }
    toast(action === "approve" ? "Onaylandı." : "Reddedildi.");
    await load();
    if (CURRENT_OFFER) openDetail(CURRENT_OFFER);
  }

  async function openDetail(offerId) {
    CURRENT_OFFER = offerId;
    const res = await fetch(offerUrl(ENDPOINTS.offer_detail, offerId));
    const body = await res.json();
    if (!body.ok) { toast(body.error || "Detay yüklenemedi", true); return; }
    $("ov-eyebrow").textContent = `${body.deal.DEAL_ID} · ${offerId}`;
    $("ov-title").textContent =
      `${body.product_label} — ${body.deal.BORROWER_BANK}`;
    $("ov-sub").textContent = body.deal.DEAL_LABEL || "";
    const edit = $("ov-edit");
    if (FI_CAN_ENTER) {
      edit.style.display = "";
      edit.href = `${ENDPOINTS.entry_page}?offer=${encodeURIComponent(offerId)}`;
    }
    renderCurrent(body.current);
    renderSchedule(body);
    renderTimeline(body);
    $("fi-ov").classList.add("is-open");
  }

  function closeDetail() {
    $("fi-ov").classList.remove("is-open");
    CURRENT_OFFER = null;
  }

  /* ── açılış ───────────────────────────────────────────────────────── */
  document.addEventListener("DOMContentLoaded", () => {
    // Overlay'i body'ye portalla (CUSTOM_PAGE_DESIGN §6 — fixed referansı).
    document.body.appendChild($("fi-ov"));

    GRID = agGrid.createGrid($("fi-grid"), {
      columnDefs: COLS,
      rowData: [],
      defaultColDef: { sortable: true, resizable: true, filter: true },
      animateRows: false,
      onRowClicked: (ev) => openDetail(ev.data.OFFER_ID),
    });

    $("btn-refresh").addEventListener("click", load);
    $("chk-pending-only").addEventListener("change", refreshGrid);
    $("ov-close").addEventListener("click", closeDetail);
    $("fi-ov").addEventListener("click", (e) => {
      if (!e.target.closest(".fi-ov__inner")) closeDetail();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeDetail();
    });
    load();
  });
})();
