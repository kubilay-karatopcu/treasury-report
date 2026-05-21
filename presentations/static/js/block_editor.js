/**
 * Phase 6.5.a — Block Editor controller.
 *
 * Vanilla JS, no bundler. Reads initial block from `#block-data`, drives the
 * form, and POSTs to /presentations/blocks/api/* for validate/save/preview.
 *
 * Form ⇆ Block contract is `block_to_dict` shape on the server. This file is
 * the single source of truth for the DOM bindings; keep IDs in sync with
 * `block_editor.html`.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const blockData = JSON.parse(document.getElementById("block-data").textContent);
  const semanticTags = JSON.parse(
    document.getElementById("semantic-tags-data").textContent
  );

  const BASE = (() => {
    // Respect SCRIPT_NAME if the page is served behind /proxy/8080.
    const path = window.location.pathname;
    const i = path.indexOf("/presentations/");
    return i >= 0 ? path.slice(0, i) : "";
  })();

  const ENDPOINTS = {
    validate:        BASE + "/presentations/blocks/api/validate",
    save:            BASE + "/presentations/blocks/api/save",
    saveNewVersion:  BASE + "/presentations/blocks/api/save_new_version",
    preview:         BASE + "/presentations/blocks/api/preview",
  };

  // ── CodeMirror (SQL editor) ─────────────────────────────────────────
  const sqlEditor = CodeMirror.fromTextArea($("be-sql"), {
    mode: "text/x-sql",
    theme: "eclipse",
    lineNumbers: true,
    indentUnit: 2,
    lineWrapping: true,
  });

  sqlEditor.on("change", () => {
    updateBindCount();
  });

  function updateBindCount() {
    const sql = sqlEditor.getValue();
    const matches = sql.match(/(?<!:):[a-zA-Z_][a-zA-Z0-9_]*\b/g) || [];
    const distinct = new Set(matches.map((m) => m.slice(1)));
    $("be-sql-bindcount").textContent = `${distinct.size} bind`;
  }

  // ── Variable rows ───────────────────────────────────────────────────
  const VAR_TYPES = [
    { value: "date",         label: "Tarih (date)" },
    { value: "date_range",   label: "Tarih aralığı (date_range)" },
    { value: "enum_single",  label: "Enum (tek)" },
    { value: "enum_multi",   label: "Enum (çoklu)" },
    { value: "number_range", label: "Sayı aralığı" },
  ];

  function renderVariableRow(v, index) {
    const wrap = document.createElement("div");
    wrap.className = "be-var-card";
    wrap.dataset.index = String(index);

    const head = document.createElement("div");
    head.className = "be-var-card-head";

    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.placeholder = "değişken_adı";
    nameInput.value = v.name || "";
    nameInput.dataset.field = "name";
    head.appendChild(nameInput);

    const tagSelect = document.createElement("select");
    tagSelect.dataset.field = "semantic_tag";
    semanticTags.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.tag;
      opt.textContent = `${t.tag} — ${t.label}`;
      if (t.tag === v.semantic_tag) opt.selected = true;
      tagSelect.appendChild(opt);
    });
    head.appendChild(tagSelect);

    const typeSelect = document.createElement("select");
    typeSelect.dataset.field = "type";
    VAR_TYPES.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.value;
      opt.textContent = t.label;
      if (t.value === (v.type || "date")) opt.selected = true;
      typeSelect.appendChild(opt);
    });
    head.appendChild(typeSelect);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "be-remove";
    removeBtn.textContent = "Sil";
    removeBtn.addEventListener("click", () => {
      state.variables.splice(index, 1);
      renderVariables();
    });
    head.appendChild(removeBtn);

    wrap.appendChild(head);

    // Required + default + allowed_values
    const detailRow = document.createElement("div");
    detailRow.className = "be-row";

    const requiredField = document.createElement("div");
    requiredField.className = "be-field";
    requiredField.innerHTML = `
      <label>Zorunlu mu?</label>
      <select data-field="required">
        <option value="true" ${v.required !== false ? "selected" : ""}>Evet</option>
        <option value="false" ${v.required === false ? "selected" : ""}>Hayır</option>
      </select>
    `;
    detailRow.appendChild(requiredField);

    const defaultField = document.createElement("div");
    defaultField.className = "be-field";
    defaultField.innerHTML = `
      <label>Varsayılan</label>
      <input data-field="default" type="text" value="${
        v.default == null ? "" : escapeAttr(stringifyDefault(v.default))
      }" placeholder="${defaultPlaceholder(v.type || 'date')}">
      <div class="be-hint">${defaultHint(v.type || 'date')}</div>
    `;
    detailRow.appendChild(defaultField);

    wrap.appendChild(detailRow);

    if ((v.type || "date").startsWith("enum")) {
      const allowedField = document.createElement("div");
      allowedField.className = "be-field";
      allowedField.innerHTML = `
        <label>Olanaklı değerler (virgülle)</label>
        <input data-field="allowed_values" type="text" value="${
          v.allowed_values ? escapeAttr(v.allowed_values.join(", ")) : ""
        }" placeholder="TRY, USD, EUR">
      `;
      wrap.appendChild(allowedField);
    }

    if ((tagSelect.value || "").toLowerCase() === "other") {
      wrap.classList.add("is-other-tag");
      const warn = document.createElement("div");
      warn.className = "be-tag-other-warning";
      warn.textContent =
        "Dikkat: 'other' kaçış kapısıdır. Phase 7 göçünde elle gözden geçirilecek.";
      wrap.appendChild(warn);
    }

    return wrap;
  }

  function defaultPlaceholder(type) {
    return {
      "date":         "today / today - 30d / 2026-01-01",
      "date_range":   "{\"from\": \"today - 30d\", \"to\": \"today\"}",
      "enum_single":  "TRY",
      "enum_multi":   "TRY, USD, EUR",
      "number_range": "{\"min\": 0, \"max\": 100}",
    }[type] || "";
  }

  function defaultHint(type) {
    return {
      "date":         "Göreceli (today / today - Nd/Nw/Nm/Ny / start_of_month) veya ISO tarih.",
      "date_range":   "JSON: { from: <ifade>, to: <ifade> }",
      "enum_single":  "Tek değer; allowed_values içinden olmalı.",
      "enum_multi":   "Virgülle ayrılmış liste; allowed_values'in alt kümesi.",
      "number_range": "JSON: { min: <num>, max: <num> }",
    }[type] || "";
  }

  function stringifyDefault(v) {
    if (Array.isArray(v)) return v.join(", ");
    if (v != null && typeof v === "object") return JSON.stringify(v);
    return String(v);
  }

  function escapeAttr(s) {
    return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  }

  function parseDefault(raw, type) {
    if (raw === "" || raw == null) return null;
    const s = String(raw).trim();
    if (s === "") return null;
    if (type === "enum_multi") {
      return s.split(",").map((x) => x.trim()).filter((x) => x !== "");
    }
    if (type === "date_range" || type === "number_range") {
      try {
        return JSON.parse(s);
      } catch (_err) {
        return s; // surface error from server
      }
    }
    return s;
  }

  function parseAllowedValues(raw) {
    if (!raw) return null;
    const parts = String(raw).split(",").map((x) => x.trim()).filter((x) => x !== "");
    return parts.length ? parts : null;
  }

  function renderVariables() {
    const list = $("be-var-list");
    list.innerHTML = "";
    state.variables.forEach((v, i) => {
      list.appendChild(renderVariableRow(v, i));
    });
    // Bind change handlers
    list.querySelectorAll(".be-var-card").forEach((card) => {
      const idx = Number(card.dataset.index);
      card.querySelectorAll("[data-field]").forEach((el) => {
        el.addEventListener("input", () => syncVariable(idx, card));
        el.addEventListener("change", () => syncVariable(idx, card));
      });
    });
  }

  function syncVariable(idx, card) {
    const v = state.variables[idx];
    if (!v) return;
    const get = (field) => card.querySelector(`[data-field='${field}']`);
    v.name = get("name").value.trim();
    const tagValue = get("semantic_tag").value;
    v.semantic_tag = tagValue;
    const typeChanged = v.type !== get("type").value;
    v.type = get("type").value;
    v.required = get("required").value === "true";
    v.default = parseDefault(get("default").value, v.type);
    const allowedEl = card.querySelector("[data-field='allowed_values']");
    v.allowed_values = allowedEl ? parseAllowedValues(allowedEl.value) : null;
    if (typeChanged) renderVariables();
    if (tagValue && tagValue.toLowerCase() === "other") {
      card.classList.add("is-other-tag");
    } else {
      card.classList.remove("is-other-tag");
    }
  }

  // ── State + form sync ────────────────────────────────────────────────
  const initialBlock = blockData.block || blockData;
  const state = {
    team: initialBlock.team || "",
    id: initialBlock.id || "",
    version: initialBlock.version || 1,
    title: initialBlock.title || "",
    description: initialBlock.description || "",
    owner: initialBlock.owner || "",
    created_at: initialBlock.created_at,
    tags: initialBlock.tags || [],
    documentation: initialBlock.documentation || {},
    variables: (initialBlock.variables || []).map((v) => ({ ...v })),
    visualization: initialBlock.visualization || { type: "bar_chart", config: {} },
    query: initialBlock.query || "",
  };

  function loadFormFromState() {
    $("be-team").value = state.team;
    $("be-id").value = state.id;
    $("be-title").value = state.title;
    $("be-description").value = state.description || "";
    $("be-tags").value = (state.tags || []).join(", ");
    $("be-doc-purpose").value = state.documentation?.purpose || "";
    $("be-doc-context").value = state.documentation?.business_context || "";
    $("be-doc-decision").value = state.documentation?.decision_support || "";
    $("be-doc-limit").value = state.documentation?.known_limitations || "";
    sqlEditor.setValue(state.query || "");
    $("be-viz-type").value = state.visualization?.type || "bar_chart";
    $("be-viz-config").value = JSON.stringify(
      state.visualization?.config || {},
      null,
      2,
    );
    renderVariables();
    updateBindCount();
  }

  function readFormIntoState() {
    state.team = $("be-team").value.trim();
    state.id = $("be-id").value.trim();
    state.title = $("be-title").value.trim();
    state.description = $("be-description").value.trim();
    state.tags = $("be-tags").value
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t !== "");
    state.documentation = {
      purpose: $("be-doc-purpose").value.trim(),
      business_context: $("be-doc-context").value.trim(),
      decision_support: $("be-doc-decision").value.trim(),
      known_limitations: $("be-doc-limit").value.trim(),
    };
    state.query = sqlEditor.getValue();
    let vizConfig = {};
    try {
      vizConfig = JSON.parse($("be-viz-config").value || "{}");
    } catch (_err) {
      vizConfig = { __raw: $("be-viz-config").value };
    }
    state.visualization = { type: $("be-viz-type").value, config: vizConfig };
  }

  function buildPayload() {
    readFormIntoState();
    // Drop empty allowed_values for non-enum vars; trim documentation; stamp owner.
    const variables = state.variables.map((v) => {
      const out = {
        name: v.name,
        semantic_tag: v.semantic_tag,
        type: v.type,
        required: !!v.required,
      };
      if (v.default !== null && v.default !== undefined && v.default !== "") {
        out.default = v.default;
      }
      if ((v.type === "enum_single" || v.type === "enum_multi") && v.allowed_values) {
        out.allowed_values = v.allowed_values;
      }
      return out;
    });

    const doc = state.documentation || {};
    const documentation = {};
    for (const k of ["purpose", "business_context", "decision_support", "known_limitations"]) {
      if (doc[k]) documentation[k] = doc[k];
    }

    return {
      block: {
        id: state.id,
        version: state.version,
        title: state.title,
        description: state.description || undefined,
        team: state.team,
        owner: state.owner || undefined,
        created_at: state.created_at,
        tags: state.tags,
        documentation: Object.keys(documentation).length ? documentation : undefined,
        query: state.query,
        variables,
        visualization: state.visualization,
      },
    };
  }

  // ── Messages ─────────────────────────────────────────────────────────
  function setMessages({ errors = [], warnings = [], success = null }) {
    const wrap = $("be-messages");
    wrap.innerHTML = "";
    if (success) {
      const el = document.createElement("div");
      el.className = "be-msg is-success";
      el.textContent = success;
      wrap.appendChild(el);
    }
    errors.forEach((msg) => {
      const el = document.createElement("div");
      el.className = "be-msg is-error";
      el.textContent = msg;
      wrap.appendChild(el);
    });
    warnings.forEach((msg) => {
      const el = document.createElement("div");
      el.className = "be-msg is-warning";
      el.textContent = msg;
      wrap.appendChild(el);
    });
  }

  // ── Action handlers ──────────────────────────────────────────────────
  async function postJSON(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await resp.text();
    let json = null;
    try { json = JSON.parse(text); } catch (_err) {}
    return { ok: resp.ok, status: resp.status, body: json, text };
  }

  $("be-validate").addEventListener("click", async () => {
    setMessages({ success: "Doğrulanıyor..." });
    const payload = buildPayload();
    const r = await postJSON(ENDPOINTS.validate, payload);
    if (r.ok && r.body?.ok) {
      setMessages({
        success: "Doğrulama başarılı.",
        warnings: r.body.warnings || [],
      });
    } else {
      setMessages({
        errors: r.body?.errors || [r.text || "Bilinmeyen hata"],
        warnings: r.body?.warnings || [],
      });
    }
  });

  $("be-save").addEventListener("click", async () => {
    const payload = buildPayload();
    setMessages({ success: "Kaydediliyor..." });
    const r = await postJSON(ENDPOINTS.save, payload);
    if (r.ok && r.body?.ok) {
      state.version = r.body.version;
      setMessages({
        success: `Kaydedildi: ${r.body.team}/${r.body.id} v${r.body.version}`,
        warnings: r.body.warnings || [],
      });
    } else {
      setMessages({
        errors: r.body?.errors || [r.text || "Kaydetme hatası"],
        warnings: r.body?.warnings || [],
      });
    }
  });

  $("be-bump").addEventListener("click", async () => {
    const payload = buildPayload();
    setMessages({ success: "Yeni sürüm kaydediliyor..." });
    const r = await postJSON(ENDPOINTS.saveNewVersion, payload);
    if (r.ok && r.body?.ok) {
      state.version = r.body.version;
      setMessages({
        success: `Yeni sürüm: ${r.body.team}/${r.body.id} v${r.body.version}`,
        warnings: r.body.warnings || [],
      });
    } else {
      setMessages({
        errors: r.body?.errors || [r.text || "Bilinmeyen hata"],
        warnings: r.body?.warnings || [],
      });
    }
  });

  $("be-run").addEventListener("click", async () => {
    const payload = buildPayload();
    setMessages({ success: "Çalıştırılıyor..." });
    const r = await postJSON(ENDPOINTS.preview, payload);
    if (r.ok && r.body?.ok) {
      renderPreview(r.body);
      setMessages({
        success: `Başarılı: ${r.body.meta?.row_count ?? 0} satır.`,
        warnings: r.body.meta?.warnings || [],
      });
    } else {
      setMessages({
        errors: r.body?.errors || [r.text || "Çalıştırma hatası"],
        warnings: r.body?.warnings || [],
      });
    }
  });

  function renderPreview(result) {
    $("be-resolved-sql").textContent = result.meta?.rewritten_sql || "";
    $("be-bind-params").textContent = JSON.stringify(
      result.meta?.bind_params || {}, null, 2,
    );
    $("be-meta-rows").textContent = String(result.meta?.row_count ?? "—");
    $("be-meta-duration").textContent = result.meta?.duration_ms != null
      ? `${result.meta.duration_ms} ms`
      : "—";
    $("be-meta-cols").textContent = (result.columns || []).join(", ") || "—";

    const table = $("be-preview-table");
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");
    thead.innerHTML = "";
    tbody.innerHTML = "";

    const cols = result.columns || [];
    if (cols.length) {
      const trh = document.createElement("tr");
      cols.forEach((c) => {
        const th = document.createElement("th");
        th.textContent = c;
        trh.appendChild(th);
      });
      thead.appendChild(trh);
    }
    (result.rows || []).slice(0, 100).forEach((row) => {
      const tr = document.createElement("tr");
      cols.forEach((c) => {
        const td = document.createElement("td");
        const v = row[c];
        td.textContent = v == null ? "" : String(v);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  $("be-add-var").addEventListener("click", () => {
    state.variables.push({
      name: "",
      semantic_tag: semanticTags[0]?.tag || "as_of_time",
      type: "date",
      required: true,
      default: "today",
    });
    renderVariables();
  });

  // ── Boot ────────────────────────────────────────────────────────────
  loadFormFromState();
  setMessages({ success: "Hazır." });
})();
