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

  // Tam-sayfa meşgul kilidi (onay/red vb.) — kullanıcı işlemin sürdüğünü
  // görür, ikinci kez basamaz (2026-07-31 saha isteği).
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
    // Satır aksiyonu: detay/düzenleme modalı butonla açılır (yanlışlıkla
    // tek tık açılmaz; çift tık da açar — aşağıda onRowDoubleClicked).
    { headerName: "", colId: "fi_actions", width: 56, pinned: "left",
      sortable: false, filter: false, resizable: false,
      cellRenderer: (p) => {
        const b = document.createElement("button");
        b.className = "fi-grid-edit-btn";
        b.textContent = "✎";
        b.title = "Detay / Düzenle";
        b.addEventListener("click", (e) => {
          e.stopPropagation();
          openDetail(p.data.OFFER_ID);
        });
        return b;
      } },
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
    if (rows.length && GRID.autoSizeAllColumns) {
      // Excel'deki çift-tık genişletme gibi: her kolon içeriğine açılır
      GRID.autoSizeAllColumns();
    }
    $("grid-info").textContent =
      `${rows.length} kayıt · ${ROWS.filter((r) => r.ROW_STATE === "PENDING_NEW" || r.PENDING_COUNT > 0).length} onay bekliyor`;
  }

  async function load() {
    if (GRID.showLoadingOverlay) GRID.showLoadingOverlay();
    try {
      const res = await fetch(ENDPOINTS.records);
      const body = await res.json();
      if (!body.ok) { toast(body.error || "Liste yüklenemedi", true); return; }
      ROWS = body.rows;
      refreshGrid();
    } finally {
      if (!visibleRows().length && GRID.showNoRowsOverlay) GRID.showNoRowsOverlay();
    }
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

  function _fmtVal(k, v) {
    if (v == null || v === "") return null;
    if (DATEISH.has(k)) return dt(v);
    if (AMTISH.has(k)) return amt(v);
    return String(v);
  }

  function renderCurrent(detail) {
    const dl = $("ov-current");
    dl.innerHTML = "";
    const cur = detail.current;
    // Bekleyen değişiklik varsa onaylı değerle KARŞILAŞTIRMALI gösterilir:
    // eski üstü çizili, yeni vurgulu (2026-07-31 saha isteği #14).
    const first = detail.events.length ? detail.events[0] : null;
    const pend = first && first.APPROVAL_STATUS === "PENDING" && cur ? first : null;

    const push = (label, content) => {
      if (content == null) return;
      const dtEl = document.createElement("dt");
      dtEl.textContent = label;
      const ddEl = document.createElement("dd");
      if (content instanceof Node) ddEl.appendChild(content);
      else ddEl.textContent = content;
      dl.appendChild(dtEl); dl.appendChild(ddEl);
    };
    const diffOrPlain = (k, label) => {
      const curv = _fmtVal(k, cur[k]);
      const pendv = pend ? _fmtVal(k, pend[k]) : null;
      if (pend && pendv !== curv) {
        const frag = document.createDocumentFragment();
        const oldS = document.createElement("span");
        oldS.className = "fi-diff-old";
        oldS.textContent = curv == null ? "—" : curv;
        const newS = document.createElement("span");
        newS.className = "fi-diff-new";
        newS.textContent = pendv == null ? "—" : pendv;
        frag.appendChild(oldS); frag.appendChild(newS);
        push(label, frag);
      } else if (curv != null) {
        push(label, curv);
      }
    };

    if (!cur) {
      dl.innerHTML = "<dt>—</dt><dd>Henüz onaylı sürüm yok (ilk giriş onay bekliyor)</dd>";
      return;
    }
    push("Reporting Status", cur.REPORTING_STATUS);
    diffOrPlain("DEAL_STATUS", "Deal Status");
    Object.keys(FIELD_LABELS).forEach((k) => diffOrPlain(k, FIELD_LABELS[k]));
    push("Tenor (gün)", cur.TENOR_DAYS != null ? String(Math.round(cur.TENOR_DAYS)) : null);
    if (pend) {
      push("Bekleyen", `#${pend.EVENT_SEQ} · ${pend.EVENT_TYPE} · ${ts(pend.EVENT_TS)}`);
    }
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
    busy(true, action === "approve" ? "Onaylanıyor…" : "Reddediliyor…");
    try {
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
      if (CURRENT_OFFER) await openDetail(CURRENT_OFFER);
    } finally {
      busy(false);
    }
  }

  function _ovLoading() {
    $("ov-eyebrow").textContent = "";
    $("ov-title").textContent = "Yükleniyor…";
    $("ov-sub").textContent = "";
    $("ov-edit").style.display = "none";
    $("ov-current").innerHTML = '<dt></dt><dd class="fi-ov__loading">Yükleniyor…</dd>';
    $("ov-sched").innerHTML = "";
    $("ov-sched-note").textContent = "";
    $("ov-timeline").innerHTML = '<tbody><tr><td class="fi-ov__loading">Yükleniyor…</td></tr></tbody>';
  }

  async function openDetail(offerId) {
    CURRENT_OFFER = offerId;
    // Modal ÖNCE açılır, içi yüklenirken 'Yükleniyor…' görünür — 2-3 sn'lik
    // fetch sırasında hiçbir şey olmuyormuş hissi kalmaz (saha isteği #8).
    _ovLoading();
    $("fi-ov").classList.add("is-open");
    const res = await fetch(offerUrl(ENDPOINTS.offer_detail, offerId));
    const body = await res.json();
    if (!body.ok) {
      toast(body.error || "Detay yüklenemedi", true);
      closeDetail();
      return;
    }
    $("ov-eyebrow").textContent = `${body.deal.DEAL_ID} · ${offerId}`;
    $("ov-title").textContent =
      `${body.product_label} — ${body.deal.BORROWER_BANK}`;
    $("ov-sub").textContent = body.deal.DEAL_LABEL || "";
    const edit = $("ov-edit");
    if (FI_CAN_ENTER) {
      edit.style.display = "";
      edit.href = `${ENDPOINTS.entry_page}?offer=${encodeURIComponent(offerId)}`;
    }
    renderCurrent(body);
    renderSchedule(body);
    renderTimeline(body);
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
      // Tek tık modal AÇMAZ (yanlışlıkla açılıyordu) — ✎ butonu ya da çift tık
      onRowDoubleClicked: (ev) => openDetail(ev.data.OFFER_ID),
      overlayLoadingTemplate:
        '<span class="ag-overlay-loading-center">Yükleniyor…</span>',
      overlayNoRowsTemplate:
        '<span style="color: var(--ink-mute); font-size: 12px;">Kayıt yok</span>',
    });
    if (GRID.showLoadingOverlay) GRID.showLoadingOverlay();

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
