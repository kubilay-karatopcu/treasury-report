/* FI Masası — Yönetim ekranı (lookup tabloları; yalnız IS_ADMIN).
 * Üç bölüm: Bankalar (FI_LU_BANK), Kullanıcı Rolleri (FI_LU_USER),
 * Listeler (FI_LU_LIST). Desen: tablo satırındaki ✎ üstteki formu doldurur
 * (orig anahtar saklanır), Kaydet POST / 🗑 DELETE; her yazımdan sonra
 * tablo tazelenir. Sunucu (routes_admin) her istekte admin'i yeniden
 * doğrular — buradaki her şey yalnız UX. */
(function () {
  "use strict";

  const ENDPOINTS = JSON.parse(document.getElementById("fi-endpoints").textContent);
  const $ = (id) => document.getElementById(id);

  function toast(msg, isErr) {
    const t = $("toast-msg");
    $("toast-body").textContent = msg;
    t.classList.toggle("is-err", !!isErr);
    t.classList.add("is-open");
    setTimeout(() => t.classList.remove("is-open"), 3500);
  }

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

  async function call(url, method, body) {
    busy(true, "Kaydediliyor…");
    try {
      const res = await fetch(url, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      const out = await res.json();
      if (!out.ok) { toast(out.error || "İşlem başarısız", true); return null; }
      return out;
    } catch (e) {
      toast(String(e), true);
      return null;
    } finally {
      busy(false);
    }
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function bindRowActions(table, onEdit, onDel) {
    table.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => onEdit(JSON.parse(b.dataset.edit))));
    table.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => onDel(JSON.parse(b.dataset.del))));
  }

  function attachFilter(input, table) {
    input.addEventListener("input", () => {
      const f = input.value.trim().toLocaleLowerCase("tr");
      table.querySelectorAll("tbody tr").forEach((tr) => {
        tr.style.display = !f ||
          tr.textContent.toLocaleLowerCase("tr").includes(f) ? "" : "none";
      });
    });
  }

  /* ── Bankalar ─────────────────────────────────────────────────────── */
  let BK_ORIG = null;   // düzenlenen bankanın orijinal adı (null = yeni)

  function bkClear() {
    BK_ORIG = null;
    ["bk-name", "bk-country", "bk-region", "bk-grp", "bk-grp-c", "bk-grp-r"]
      .forEach((id) => { $(id).value = ""; });
    $("bk-self").checked = false;
    $("bk-status").textContent = "";
  }

  function bkEdit(row) {
    BK_ORIG = row.BANK_NM;
    $("bk-name").value = row.BANK_NM || "";
    $("bk-country").value = row.COUNTRY || "";
    $("bk-region").value = row.REGION || "";
    $("bk-grp").value = row.GROUP_COMPANY || "";
    $("bk-grp-c").value = row.GROUP_COMPANY_COUNTRY || "";
    $("bk-grp-r").value = row.GROUP_COMPANY_REGION || "";
    $("bk-self").checked = !!row.IS_SELF;
    $("bk-status").textContent = `Düzenleniyor: ${row.BANK_NM}`;
    $("bk-name").focus();
  }

  async function bkDelete(row) {
    if (!confirm(`"${row.BANK_NM}" bankası silinsin mi?\n(Geçmiş işlemler ` +
                 "etkilenmez; yalnız seçim listesinden düşer.)")) return;
    if (await call(ENDPOINTS.banks, "DELETE", { bank_nm: row.BANK_NM })) {
      toast("Banka silindi.");
      bkClear();
      bkLoad();
    }
  }

  async function bkSave() {
    const out = await call(ENDPOINTS.banks, "POST", {
      orig_bank_nm: BK_ORIG,
      bank_nm: $("bk-name").value,
      country: $("bk-country").value,
      region: $("bk-region").value,
      group_company: $("bk-grp").value,
      group_company_country: $("bk-grp-c").value,
      group_company_region: $("bk-grp-r").value,
      is_self: $("bk-self").checked,
    });
    if (out) { toast("Banka kaydedildi."); bkClear(); bkLoad(); }
  }

  async function bkLoad() {
    const out = await call(ENDPOINTS.banks, "GET");
    if (!out) return;
    const t = $("bk-table");
    t.innerHTML =
      "<thead><tr><th>Banka</th><th>Ülke</th><th>Region</th><th>Grup</th>" +
      "<th>Grup Ülke</th><th></th></tr></thead>";
    const tb = document.createElement("tbody");
    out.rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${esc(r.BANK_NM)}${r.IS_SELF ? ' <span class="fi-self-badge">SELF</span>' : ""}</td>` +
        `<td>${esc(r.COUNTRY)}</td><td>${esc(r.REGION)}</td>` +
        `<td>${esc(r.GROUP_COMPANY)}</td><td>${esc(r.GROUP_COMPANY_COUNTRY)}</td>` +
        `<td class="fi-adm-act">` +
        `<button class="fi-adm-btn" data-edit='${esc(JSON.stringify(r))}' title="Düzenle">✎</button> ` +
        `<button class="fi-adm-btn is-del" data-del='${esc(JSON.stringify(r))}' title="Sil">🗑</button></td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    bindRowActions(t, bkEdit, bkDelete);
  }

  /* ── Kullanıcı rolleri ────────────────────────────────────────────── */
  function usClear() {
    $("us-sicil").value = "";
    $("us-sicil").disabled = false;
    $("us-entry").checked = false;
    $("us-approver").checked = false;
    $("us-status").textContent = "";
  }

  function usEdit(row) {
    $("us-sicil").value = row.sicil;
    $("us-sicil").disabled = true;   // sicil kimliktir; değişmez (sil + yeni)
    $("us-entry").checked = row.roles.includes("ENTRY");
    $("us-approver").checked = row.roles.includes("APPROVER");
    $("us-status").textContent = `Düzenleniyor: ${row.sicil}`;
  }

  async function usDelete(row) {
    if (!confirm(`${row.sicil} kullanıcısının TÜM rolleri kaldırılsın mı?\n` +
                 "(Ekranlar salt-okunura düşer; geçmiş kayıtları durur.)")) return;
    if (await call(ENDPOINTS.users, "DELETE", { sicil: row.sicil })) {
      toast("Kullanıcı rolleri kaldırıldı.");
      usClear();
      usLoad();
    }
  }

  async function usSave() {
    const roles = [];
    if ($("us-entry").checked) roles.push("ENTRY");
    if ($("us-approver").checked) roles.push("APPROVER");
    const out = await call(ENDPOINTS.users, "POST", {
      sicil: $("us-sicil").value,
      roles: roles,
    });
    if (out) { toast("Kullanıcı kaydedildi."); usClear(); usLoad(); }
  }

  async function usLoad() {
    const out = await call(ENDPOINTS.users, "GET");
    if (!out) return;
    const t = $("us-table");
    t.innerHTML =
      "<thead><tr><th>Sicil</th><th>Roller</th><th></th></tr></thead>";
    const tb = document.createElement("tbody");
    out.rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${esc(r.sicil)}</td><td>${esc(r.roles.join(" + "))}</td>` +
        `<td class="fi-adm-act">` +
        `<button class="fi-adm-btn" data-edit='${esc(JSON.stringify(r))}' title="Düzenle">✎</button> ` +
        `<button class="fi-adm-btn is-del" data-del='${esc(JSON.stringify(r))}' title="Rolleri kaldır">🗑</button></td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    bindRowActions(t, usEdit, usDelete);
  }

  /* ── Listeler ─────────────────────────────────────────────────────── */
  let LS_ORIG = null;   // düzenlenen kaydın orijinal VALUE_CD'si (null = yeni)

  function lsClear() {
    LS_ORIG = null;
    ["ls-cd", "ls-label", "ls-parent", "ls-attr1"].forEach((id) => { $(id).value = ""; });
    $("ls-status").textContent = "";
  }

  function lsEdit(row) {
    LS_ORIG = row.VALUE_CD;
    $("ls-cd").value = row.VALUE_CD || "";
    $("ls-label").value = row.LABEL || "";
    $("ls-parent").value = row.PARENT_CD || "";
    $("ls-attr1").value = row.ATTR1 || "";
    $("ls-status").textContent = `Düzenleniyor: ${row.VALUE_CD}`;
    $("ls-cd").focus();
  }

  async function lsDelete(row) {
    const name = $("ls-name").value;
    if (!confirm(`${name} listesinden "${row.VALUE_CD}" silinsin mi?\n` +
                 "(Geçmiş işlemler etkilenmez; yalnız dropdown'dan düşer.)")) return;
    if (await call(ENDPOINTS.lists, "DELETE",
                   { list_name: name, value_cd: row.VALUE_CD })) {
      toast("Kayıt silindi.");
      lsClear();
      lsLoad();
    }
  }

  async function lsSave() {
    const out = await call(ENDPOINTS.lists, "POST", {
      list_name: $("ls-name").value,
      orig_value_cd: LS_ORIG,
      value_cd: $("ls-cd").value,
      label: $("ls-label").value,
      parent_cd: $("ls-parent").value,
      attr1: $("ls-attr1").value,
    });
    if (out) { toast("Kayıt yazıldı."); lsClear(); lsLoad(); }
  }

  async function lsLoad() {
    const name = $("ls-name").value || "EXPORTER";
    const out = await call(`${ENDPOINTS.lists}?name=${encodeURIComponent(name)}`, "GET");
    if (!out) return;
    // Liste seçici (ilk yüklemede doldurulur; seçim korunur)
    const sel = $("ls-name");
    if (!sel.options.length) {
      out.names.forEach((n) => {
        const o = document.createElement("option");
        o.value = n; o.textContent = n;
        sel.appendChild(o);
      });
      sel.value = out.name;
    }
    const t = $("ls-table");
    t.innerHTML =
      "<thead><tr><th>Kod</th><th>Etiket</th><th>Parent</th><th>Attr1</th>" +
      "<th class='num'>Sıra</th><th></th></tr></thead>";
    const tb = document.createElement("tbody");
    out.rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${esc(r.VALUE_CD)}</td><td>${esc(r.LABEL)}</td>` +
        `<td>${esc(r.PARENT_CD)}</td><td>${esc(r.ATTR1)}</td>` +
        `<td class="num">${esc(r.SORT_NO)}</td>` +
        `<td class="fi-adm-act">` +
        `<button class="fi-adm-btn" data-edit='${esc(JSON.stringify(r))}' title="Düzenle">✎</button> ` +
        `<button class="fi-adm-btn is-del" data-del='${esc(JSON.stringify(r))}' title="Sil">🗑</button></td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    bindRowActions(t, lsEdit, lsDelete);
  }

  /* ── açılış ───────────────────────────────────────────────────────── */
  document.addEventListener("DOMContentLoaded", () => {
    $("bk-save").addEventListener("click", bkSave);
    $("bk-clear").addEventListener("click", bkClear);
    $("us-save").addEventListener("click", usSave);
    $("us-clear").addEventListener("click", usClear);
    $("ls-save").addEventListener("click", lsSave);
    $("ls-clear").addEventListener("click", lsClear);
    $("ls-name").addEventListener("change", () => { lsClear(); lsLoad(); });
    attachFilter($("bk-filter"), $("bk-table"));
    attachFilter($("ls-filter"), $("ls-table"));
    bkLoad();
    usLoad();
    lsLoad();
  });
})();
