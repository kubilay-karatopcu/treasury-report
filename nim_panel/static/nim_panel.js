/* nim_panel.js — NIM_calculation @ bs_evolution5 SPA'sinin deposit-only portu.
   Kaynak: templates/index.html satir 3073-15668 (dogutan/NIM_calculation).
   Asagidaki onsoz porta ozgudur; gerisi kaynak SPA kodudur (NII kirpimli). */
(function () {
  var cfg = window.NIM_CONFIG || {};
  // Blueprint url_prefix + OpenShift SCRIPT_NAME uyumu: SPA'nin "/api/..."
  // cagrilari blueprint tabanina yonlendirilir. Sayfa-scoped tek shim.
  var base = (cfg.apiBase || "/").replace(/\/$/, "");
  var origFetch = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    if (typeof url === "string" && url.indexOf("/api/") === 0) url = base + url;
    return origFetch(url, opts);
  };
  // Tema koprusu: PRISMA kabugu localStorage("prisma-theme") kullanir;
  // SPA body.light-mode + #theme-toggle ile calisir. Iki yonlu esitleme.
  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    try {
      if (localStorage.getItem("prisma-theme") === "light" &&
          !document.body.classList.contains("light-mode") && btn) btn.click();
    } catch (e) {}
    if (btn) btn.addEventListener("click", function () {
      setTimeout(function () {
        try {
          localStorage.setItem("prisma-theme",
            document.body.classList.contains("light-mode") ? "light" : "dark");
        } catch (e) {}
      }, 0);
    });
  });
})();

(function() {
  const elNimType = document.getElementById("nimType");
  const elDate0 = document.getElementById("date0");
  const elDate1 = document.getElementById("date1");
  const elStatus = document.getElementById("status");
  const elSimStatus = document.getElementById("sim-status");
  const elErr = document.getElementById("err");
  const singleTitle = document.getElementById("single-title");
  const standardFilters = document.getElementById("standard-filters");
  const simulationFilters = document.getElementById("simulation-filters");
  const crossFilters = document.getElementById("cross-filters");
  const stdSection = document.getElementById("std-section");
  const simSection = document.getElementById("sim-section");
  const manualSection = document.getElementById("manual-section");

  // True when running off the standalone HTML export (data embedded in <head>)
  const IS_EXPORT = !!window.__NIM_EMBEDDED__;
  const elCrossScn1   = document.getElementById("crossScn1");
  const elCrossScn2   = document.getElementById("crossScn2");
  const elCrossNimType = document.getElementById("crossNimType");
  const elCrossDate   = document.getElementById("crossDate");
  const elCrossStatus = document.getElementById("cross-status");

  // Simulation scenarios from backend (populated from SCN.xlsx)
  var SIM_SCENARIOS = [];
  var simScenarioMode   = true;    // start in simulation-scenario mode
  var crossScenarioMode = false;

  let currentDataSource = "";
  let currentPage = "standard";
  let rawGridApi = null;


  let chartInstances = {};
  let collapsedGroups = new Set();

  // ── Waterfall carousel ─────────────────────────────────────────────────────
  // Slides: 0=Mix vs Pricing (wf1), 1=Pricing Drivers (wf2), 2=Economic Mix (wf4)
  // Slide 2 also shows Weight Changes (wf3) as a companion below.
  var WF_SLIDES = ["wf1", "wf2", "wf4"];
  var wfSlide = 0;
  var wfFigs  = null;   // cached figs from last /api/waterfalls response

  // ── Simulation Results carousels ───────────────────────────────────────────
  var simNimFigs   = [];   // [total_nim_fig, try_nim_fig]
  var simNimSlide  = 0;
  var simLoansFigs = [];   // [loans_fig_scn1, loans_fig_scn2, ...]
  var simLoansSlide = 0;

  // ── Loans chart extra product overlay ────────────────────────────────────
  var loansAllProducts = [];  // [{name, currency, bs_type}, ...] from API
  var loansExtraData   = {};  // key: "name|currency" → {label, color, series: {source: [...]}}
  var loansColorIdx    = 0;
  var LOANS_EXTRA_COLORS = [
    "#6B8FA8","#9BAE8A","#B8946A","#8B7BA8","#7B6B95",
    "#A06B6B","#6B7589","#8B95A7","#7A9B7E","#4A6B8A",
  ];

  function _loansKey(name, currency) { return name + "|" + (currency || ""); }

  function syncLoansDropdown() {
    var sel = document.getElementById("loans-product-select");
    if (!sel) return;
    while (sel.options.length > 1) sel.remove(1);
    var groups = {};   // bs_type -> <optgroup>
    loansAllProducts.forEach(function(p) {
      if (loansExtraData[_loansKey(p.name, p.currency)]) return;
      var bs = p.bs_type || "Other";
      if (!groups[bs]) {
        var og = document.createElement("optgroup");
        og.label = bs;
        sel.appendChild(og);
        groups[bs] = og;
      }
      groups[bs].appendChild(new Option(p.name, JSON.stringify({name: p.name, currency: p.currency})));
    });
  }

  function renderLoansExtraTags() {
    var container = document.getElementById("loans-extra-tags");
    if (!container) return;
    var keys = Object.keys(loansExtraData);
    container.style.display = keys.length > 0 ? "flex" : "none";
    container.innerHTML = "";
    keys.forEach(function(key) {
      var info = loansExtraData[key];
      var tag  = document.createElement("span");
      tag.style.cssText = "display:inline-flex;align-items:center;gap:4px;" +
        "background:" + info.color + "22;border:1px solid " + info.color + ";" +
        "border-radius:3px;padding:2px 6px;font-size:11px;";
      var dot = document.createElement("span");
      dot.style.cssText = "width:8px;height:8px;border-radius:50%;background:" + info.color + ";flex-shrink:0;";
      var lbl = document.createElement("span");
      lbl.textContent = info.label;
      lbl.style.color = "#7A8399";
      var btn = document.createElement("button");
      btn.textContent = "×";
      btn.dataset.key = key;
      btn.style.cssText = "background:none;border:none;cursor:pointer;padding:0;font-size:13px;color:#485166;line-height:1;margin-left:2px;";
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        delete loansExtraData[this.dataset.key];
        syncLoansDropdown();
        renderLoansExtraTags();
        renderSimLoansSlide(simLoansSlide);
      });
      tag.appendChild(dot); tag.appendChild(lbl); tag.appendChild(btn);
      container.appendChild(tag);
    });
  }

  async function addLoansProduct(name, currency) {
    var key = _loansKey(name, currency);
    if (loansExtraData[key]) return;
    var color = LOANS_EXTRA_COLORS[loansColorIdx % LOANS_EXTRA_COLORS.length];
    loansColorIdx++;
    var label = name;
    loansExtraData[key] = { label: label, color: color, series: {} };
    syncLoansDropdown();
    renderLoansExtraTags();
    // Fetch series for every loaded scenario slide
    var fetchErrors = [];
    for (var i = 0; i < simLoansFigs.length; i++) {
      var fig = simLoansFigs[i];
      if (!fig.source) {
        fetchErrors.push("slide " + i + ": missing source");
        continue;
      }
      if (!loansExtraData[key]) continue;
      var src = fig.source;
      if (loansExtraData[key].series[src]) continue;
      try {
        var url = "/api/loan_extra_series?source=" + encodeURIComponent(src) +
                  "&product=" + encodeURIComponent(name) +
                  (currency ? "&currency=" + encodeURIComponent(currency) : "");
        var res = await fetch(url);
        var jd  = await res.json();
        if (!jd.ok) {
          fetchErrors.push(src + ": " + (jd.error || "unknown"));
        } else if (!jd.series || !jd.series.length) {
          fetchErrors.push(src + ": empty series");
        } else if (loansExtraData[key]) {
          loansExtraData[key].series[src] = jd.series.map(function(s) {
            return Object.assign({}, s, { color: color });
          });
        }
      } catch(e) {
        fetchErrors.push(src + ": " + (e && e.message ? e.message : String(e)));
      }
    }
    if (fetchErrors.length) {
      showError("Product could not be added (" + label + "): " + fetchErrors.join("; "));
    }
    renderSimLoansSlide(simLoansSlide);
  }

  function renderSimNimSlide(idx) {
    simNimSlide = idx;
    destroyChart("sr-nim-main");
    renderChart("sr-nim-main", simNimFigs[idx]);
    var btnPrev = document.getElementById("nim-prev");
    var btnNext = document.getElementById("nim-next");
    if (btnPrev) btnPrev.disabled = idx === 0;
    if (btnNext) btnNext.disabled = idx === simNimFigs.length - 1;
    var lbl = document.getElementById("nim-slide-label");
    if (lbl) lbl.textContent = (idx + 1) + " / " + simNimFigs.length;
  }

  // ── Export PDF: CBRT + currently selected NIM chart, landscape single page ──
  // Rasterize an ApexCharts SVG at high resolution by extracting from DOM directly.
  // chart.dataURI({scale}) has a v3 bug (blank content) so we bypass it entirely.
  function _svgToHighResJpeg(containerId, scale) {
    return new Promise(function(resolve, reject) {
      var container = document.getElementById(containerId);
      if (!container) { reject(new Error("Container not found: " + containerId)); return; }
      var svgEl = container.querySelector("svg");
      if (!svgEl) { reject(new Error("SVG not found in: " + containerId)); return; }
      var w = svgEl.clientWidth || svgEl.getBoundingClientRect().width;
      var h = svgEl.clientHeight || svgEl.getBoundingClientRect().height;
      if (!w || !h) { reject(new Error("SVG has zero dimensions in: " + containerId)); return; }
      // Clone SVG and inject explicit dimensions so canvas renders at full size
      var clone = svgEl.cloneNode(true);
      clone.setAttribute("width", w);
      clone.setAttribute("height", h);
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      var svgStr = new XMLSerializer().serializeToString(clone);
      var img = new Image();
      img.onload = function() {
        var c = document.createElement("canvas");
        c.width = Math.round(w * scale);
        c.height = Math.round(h * scale);
        var ctx = c.getContext("2d");
        // PDF zemin rengi aktif temayı izler: light'ta panel kremi, dark'ta navy.
        ctx.fillStyle = document.body.classList.contains("light-mode") ? "#FFFEFA" : "#131826";
        ctx.fillRect(0, 0, c.width, c.height);
        ctx.drawImage(img, 0, 0, c.width, c.height);
        resolve({ uri: c.toDataURL("image/jpeg", 0.95), w: c.width, h: c.height });
      };
      img.onerror = reject;
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgStr);
    });
  }

  async function exportSimResultsPdf() {
    var btn  = document.getElementById("sr-pdf-btn");
    if (!window.jspdf || !window.jspdf.jsPDF) {
      showError("PDF library (jsPDF) could not be loaded — check your internet connection.");
      return;
    }
    if (!chartInstances["sr0"] || !chartInstances["sr-nim-main"]) {
      showError("Charts are not loaded yet.");
      return;
    }
    var orig = btn.textContent;
    btn.textContent = "Preparing…";
    btn.disabled = true;
    try {
      var d1 = await _svgToHighResJpeg("sr0", 2);
      var d2 = await _svgToHighResJpeg("sr-nim-main", 2);

      var pdf   = new window.jspdf.jsPDF({ orientation: "landscape", unit: "pt", format: "a4" });
      var pageW = pdf.internal.pageSize.getWidth();
      var pageH = pdf.internal.pageSize.getHeight();
      var margin = 28, gap = 14;
      var availW = pageW - margin * 2;
      var availH = (pageH - margin * 2 - gap) / 2;

      function fit(d) {
        var s = Math.min(availW / d.w, availH / d.h);
        return { w: d.w * s, h: d.h * s };
      }
      var f1 = fit(d1), f2 = fit(d2);
      pdf.addImage(d1.uri, "JPEG", (pageW - f1.w) / 2, margin, f1.w, f1.h);
      pdf.addImage(d2.uri, "JPEG", (pageW - f2.w) / 2, margin + availH + gap, f2.w, f2.h);
      pdf.save("simulation_results.pdf");
    } catch(e) {
      showError("PDF could not be created: " + (e && e.message ? e.message : String(e)));
    } finally {
      btn.textContent = orig;
      btn.disabled = false;
    }
  }

  function _loansYRange(series) {
    var allY = [];
    (series || []).forEach(function(s) {
      (s.data || []).forEach(function(p) {
        if (p.y != null && isFinite(p.y)) allY.push(p.y);
      });
    });
    if (!allY.length) return {};
    var lo = Math.min.apply(null, allY);
    var hi = Math.max.apply(null, allY);
    var pad = Math.max((hi - lo) * 0.08, 0.2);
    return { y_min: parseFloat((lo - pad).toFixed(4)), y_max: parseFloat((hi + pad).toFixed(4)) };
  }

  function renderSimLoansSlide(idx) {
    simLoansSlide = idx;
    var btnPrev = document.getElementById("loans-prev");
    var btnNext = document.getElementById("loans-next");
    if (btnPrev) btnPrev.disabled = idx === 0;
    if (btnNext) btnNext.disabled = idx === simLoansFigs.length - 1;
    var lbl = document.getElementById("loans-slide-label");
    if (lbl) lbl.textContent = (idx + 1) + " / " + simLoansFigs.length;
    var fig = simLoansFigs[idx];
    if (!fig) return;
    // Merge any extra series the user has added via the product dropdown
    var extra = [];
    Object.keys(loansExtraData).forEach(function(key) {
      var info = loansExtraData[key];
      var cached = fig.source && info.series[fig.source];
      if (cached) cached.forEach(function(s) { extra.push(s); });
    });
    var allSeries = extra.length ? (fig.series || []).concat(extra) : (fig.series || []);
    var range = _loansYRange(allSeries);
    var combinedFig = Object.assign({}, fig, { series: allSeries }, range);
    destroyChart("sr-loans-main");
    try {
      renderChart("sr-loans-main", combinedFig);
    } catch(e) {
      showError("Chart could not be rendered: " + (e && e.message ? e.message : String(e)));
    }
  }

  // ── Simulation Balance-Sheet table (all months, per scenario) ──────────────
  var simBsGridApi   = null;
  var simBsView      = "rate";   // "rate" | "bal" | "nii"
  var simBsSource    = null;
  var simBsData      = {};       // cache: source → {dates, rows}
  var simBsCollapsed = new Set();
  var simScenarios   = [];       // [{name, source}]

  var SBT_MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

  function _sbtDateLabel(ds, allDates) {
    var parts = ds.split("-");
    var m = parseInt(parts[1], 10) - 1;
    var yy = parts[0].slice(2);
    var sameMonth = allDates && allDates.every(function(d) { return d.slice(0,7) === ds.slice(0,7); });
    return (sameMonth ? parts[2] + " " : "") + SBT_MONTH_NAMES[m] + " " + yy;
  }

  function _buildSbtColDefs(dates, view) {
    var productCol = {
      field: "PRODUCT_NAME",
      headerName: "Product",
      width: 240,
      pinned: "left",
      suppressSizeToFit: true,
      cellRenderer: function(params) {
        var d = params.data;
        if (!d) return "";
        var v = params.value || "";
        if (d._type === "toplevel") {
          // Summary rows (Total NIM / TRY NIM / FX NIM): bold, no chevron.
          return '<span style="font-weight:700">' + v + "</span>";
        }
        if (d._type === "group") {
          var ico    = simBsCollapsed.has(d._groupId) ? "▶" : "▼";
          var indent = (d._level || 0) * 20;
          var fw     = d._level === 0 ? "700" : "600";
          return '<span style="cursor:pointer;padding-left:' + indent
                 + 'px;font-weight:' + fw + ';display:inline-block">'
                 + ico + " " + v + "</span>";
        }
        if (d._type === "sub-leaf") {
          return '<span style="padding-left:64px;display:inline-block">' + v + "</span>";
        }
        // leaf — expandable or plain
        if (d._hasChildren) {
          var ico2 = simBsCollapsed.has(d._groupId) ? "▶" : "▼";
          return '<span style="padding-left:48px;cursor:pointer;display:inline-block">'
                 + '<span style="font-size:9px;margin-right:3px;color:#485166">' + ico2 + "</span>"
                 + v + "</span>";
        }
        return '<span style="padding-left:48px;display:inline-block">' + v + "</span>";
      },
    };

    var prefix = view === "rate" ? "r_" : view === "bal" ? "b_" : "n_";
    var dateCols = dates.map(function(ds) {
      var fk = ds.replace(/-/g, "_");
      return {
        field: prefix + fk,
        headerName: _sbtDateLabel(ds, dates),
        width: 88,
        type: "numericColumn",
        valueFormatter: function(params) {
          if (params.value == null || isNaN(params.value)) return "";
          if (view === "rate") return params.value.toFixed(2) + "%";
          return Math.round(params.value).toLocaleString("tr-TR");
        },
        cellStyle: function(params) {
          var d = params.data;
          if (!d) return {};
          if (d._type === "toplevel") return { fontWeight: "700", color: "#D4A574" };
          if (d._type === "group" && d._level === 0)
            return { fontWeight: "700", color: "#E4E8F0" };
          if (d._type === "group" && d._level === 1)
            return { fontWeight: "600",
                     color: d.CURRENCY === "TRY" ? "#4A6B8A" : "#B8946A" };
          // leaf / sub-leaf
          if (view === "rate")
            return { color: d.BS_TYPE === "Assets" ? "#4A6B8A" : "#B8826B" };
          return {};
        },
      };
    });

    return [productCol].concat(dateCols);
  }

  function _initSimBsGrid(dates, rows) {
    // Level-0 groups (Assets/Liabilities) start expanded; book rows and
    // expandable leaf rows start collapsed.
    simBsCollapsed = new Set(
      rows.filter(function(r) {
        return ((r._type === "group" && r._level === 1) ||
                (r._type === "leaf" && r._hasChildren)) && r._groupId;
      }).map(function(r) { return r._groupId; })
    );

    if (simBsGridApi) { simBsGridApi.destroy(); simBsGridApi = null; }

    simBsGridApi = agGrid.createGrid(document.getElementById("sim-bs-grid"), {
      columnDefs: _buildSbtColDefs(dates, simBsView),
      rowData: rows,
      isExternalFilterPresent: function() { return true; },
      doesExternalFilterPass: function(params) {
        var d = params.data;
        if (!d || !d._ancestors || !d._ancestors.length) return true;
        return !d._ancestors.some(function(id) { return simBsCollapsed.has(id); });
      },
      onRowClicked: function(params) {
        var d = params.data;
        if (!d || (d._type !== "group" && !(d._type === "leaf" && d._hasChildren))) return;
        if (simBsCollapsed.has(d._groupId)) simBsCollapsed.delete(d._groupId);
        else simBsCollapsed.add(d._groupId);
        simBsGridApi.onFilterChanged();
        simBsGridApi.refreshCells({ columns: ["PRODUCT_NAME"], force: true });
      },
      defaultColDef: { resizable: true, sortable: false },
      domLayout: "autoHeight",
      suppressMenuHide: true,
      animateRows: false,
      getRowStyle: function(params) {
        var d = params.data;
        if (!d) return {};
        if (d._type === "toplevel") return { background: "rgba(212,165,116,0.1)", fontWeight: "700" };
        if (d._type === "group" && d._level === 0)
          return { background: "rgba(255,255,255,0.06)", fontWeight: "700", cursor: "pointer" };
        if (d._type === "group" && d._level === 1)
          return { background: "rgba(255,255,255,0.04)", fontWeight: "600", cursor: "pointer" };
        return {};
      },
    });
  }

  async function fetchSimBsTable(source) {
    if (simBsData[source]) {
      simBsSource = source;
      _initSimBsGrid(simBsData[source].dates, simBsData[source].rows);
      return;
    }
    try {
      var res = await fetch("/api/sim_bs_table?source=" + encodeURIComponent(source));
      var jd  = await res.json();
      if (!jd.ok) { showError("Balance sheet table could not be loaded: " + (jd.error || "")); return; }
      simBsData[source] = { dates: jd.dates, rows: jd.rows };
      simBsSource = source;
      _initSimBsGrid(jd.dates, jd.rows);
    } catch(e) {
      showError("Balance sheet table could not be loaded: " + (e.message || ""));
    }
  }

  function _renderSbtScnDropdown() {
    var sel = document.getElementById("sbt-scn-select");
    if (!sel) return;
    sel.innerHTML = "";
    simScenarios.forEach(function(scn) {
      sel.appendChild(new Option(scn.name, scn.source));
    });
  }

  // ── Bar double-click → navigate to product in Balance Sheet Table ──────────
  var pendingNavigation  = null;   // { bsType, productName } awaiting grid init
  var highlightedProduct = null;   // { bsType, productName } for row highlight
  var _navCooldownUntil  = 0;      // debounce: ignore calls within 600ms

  function handleWfBarDblClick(bar) {
    var label = bar.x || "";
    var sep = label.indexOf(" | ");
    if (sep < 0) return;
    var bsType      = label.substring(0, sep).trim();
    var productName = label.substring(sep + 3).trim();
    navigateToProduct(bsType, productName);
  }

  function navigateToProduct(bsType, productName) {
    // Debounce: ignore rapid re-entrancy (e.g. stale listeners firing together)
    var now = Date.now();
    if (now < _navCooldownUntil) return;
    _navCooldownUntil = now + 700;

    // NOTE: we do NOT scroll early — the scroll happens inside doNavigateInGrid
    // after the table is fully open and the row is highlighted, so there is no
    // jarring "scroll while animating" effect.

    if (sections.table.open) {
      // Table is already open — navigate directly without toggling the accordion.
      // doNavigateInGrid handles collapsing all groups and expanding only the target.
      doNavigateInGrid(bsType, productName);
    } else {
      // Table is closed — open it, then navigate when the grid is ready.
      toggleSection("table");
      if (rawGridApi && !sections.table.dirty) {
        setTimeout(function() { doNavigateInGrid(bsType, productName); }, 200);
      } else {
        pendingNavigation = { bsType: bsType, productName: productName };
      }
    }
  }

  function doNavigateInGrid(bsType, productName) {
    if (!rawGridApi) return;
    var bsId = bsType.toLowerCase().replace(/ /g, "_");

    // ── Reset: collapse all groups + expandable leaves, clear previous highlight
    highlightedProduct = null;
    var allGroupIds = new Set();
    rawGridApi.forEachNode(function(node) {
      var d = node.data;
      if (d && (d._type === "group" || (d._type === "leaf" && d._hasChildren)) && d._groupId) {
        allGroupIds.add(d._groupId);
      }
    });
    collapsedGroups = allGroupIds;
    // ──────────────────────────────────────────────────────────────────────────

    // Find the target leaf or sub-leaf node
    var targetNode = null;
    rawGridApi.forEachNode(function(node) {
      if (!targetNode && node.data &&
          (node.data._type === "leaf" || node.data._type === "sub-leaf") &&
          node.data.PRODUCT_NAME === productName &&
          Array.isArray(node.data._ancestors) &&
          node.data._ancestors[0] === bsId) {
        targetNode = node;
      }
    });
    if (!targetNode) return;

    // Expand only the ancestors of the target product
    (targetNode.data._ancestors || []).forEach(function(id) {
      collapsedGroups.delete(id);
    });

    // Set row highlight and force full row redraw so getRowStyle is re-evaluated
    // (refreshCells only updates cell content, not row background styles)
    highlightedProduct = { bsType: bsType, productName: productName };
    rawGridApi.onFilterChanged();
    rawGridApi.redrawRows();

    // Scroll after DOM has updated: first snap the section header to the top
    // of the viewport (instant, no animation), then smoothly scroll to the
    // highlighted row — so the user never sees scrolling while the table is
    // still animating open.
    setTimeout(function() {
      var accBtn = document.getElementById("acc-btn-table");
      if (accBtn) accBtn.scrollIntoView({ behavior: "instant", block: "start" });

      var targetIdx = -1;
      rawGridApi.forEachNodeAfterFilterAndSort(function(node, idx) {
        if (node === targetNode) targetIdx = idx;
      });
      if (targetIdx >= 0) {
        var rowEl = document.querySelector(
          '#rawDataGrid .ag-row[row-index="' + targetIdx + '"]'
        );
        if (rowEl) rowEl.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 150);
  }

  function renderWfSlide(idx) {
    wfSlide = idx;
    var key = WF_SLIDES[idx];
    destroyChart("wf-main");
    destroyChart("wf3");
    if (wfFigs && wfFigs[key]) {
      renderChart("wf-main", wfFigs[key], { height: 420, onBarDblClick: handleWfBarDblClick });
    }
    var companion = document.getElementById("wf-companion");
    if (idx === 2 && wfFigs && wfFigs.wf3) {
      companion.classList.remove("hidden");
      renderChart("wf3", wfFigs.wf3, { height: 320 });
    } else {
      companion.classList.add("hidden");
    }
    var btnPrev = document.getElementById("wf-prev");
    var btnNext = document.getElementById("wf-next");
    if (btnPrev) btnPrev.disabled = idx === 0;
    if (btnNext) btnNext.disabled = idx === WF_SLIDES.length - 1;
    var lbl = document.getElementById("wf-slide-label");
    if (lbl) lbl.textContent = (idx + 1) + " / " + WF_SLIDES.length;
  }

  // Section state: open = visible, dirty = needs re-fetch
  var sections = {
    historic:  { open: false, dirty: true },
    waterfall: { open: false, dirty: true },
    table:     { open: false, dirty: true },
  };

  // BS Evolution tab state
  var currentTab = "nim-evolution";   // "nim-evolution" | "bs-evolution" | "deposit-detail"
  var bseSections = {
    a: { open: false, dirty: true },
    b: { open: false, dirty: true },
    c: { open: false, dirty: true },
  };
  var bseCurrencyA = "TRY";  // active currency for Section A charts ("TRY" | "FX")
  var bseDataA = null;        // cached Section A API response

  // Deposit Detail tab state
  var DD_SLIDES = ["wf1", "wf2", "wf4"];
  var DD_DIMS   = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"];
  var ddSlide   = 0;
  var ddFigs    = null;
  var ddWfOpen  = false;
  var ddDims    = { PRODUCT: true, CUSTOMER_TYPE: true, AUM: true, SEGMENT: true };
  var ddBubMeta    = {};   // { dim: [val, ...] }
  var ddBubPdims   = {};   // { product: { dim: val } }
  var ddBubFilter  = {};   // { dim: { val: true/false } }
  var ddBubMerges  = {};   // { dim: [{ name, members: [val,...] }] }
  var ddBubAggMembers = {};// { aggName: [originalProduct,...] } — for drill-down

  // ── ORTAK gruplama (merge) hafızası ─────────────────────────────────────────
  // Outstanding Cost / Balance / Tenor Analysis + New Business Volume & Pricing
  // sayfalarındaki filtre panellerinde yapılan GRUPLAMALAR bu tek objede yaşar:
  // sayfalar arası geçişte ve refetch'lerde korunur. Dim ADI anahtardır — aynı
  // dim adını kullanan sayfalar (PRODUCT/AUM/SEGMENT/... = outstanding üçlüsü)
  // grupları paylaşır; NP sayfası farklı dim adları (AUM_BAND/CUST_TP/...)
  // kullandığından kendi grupları kendi anahtarlarında durur. FİLTRE seçimleri
  // (state) sayfa-lokal kalır — sadece gruplama ortaktır. _renderBubFilters
  // merges objesini in-place mutate ettiği için referans paylaşımı yeterlidir.
  var sharedDimMerges = {};   // { dim: [{ name, members: [val,...] }] }

  // Daily Deposit Detail tab state (own date pickers, +SEGMENT dimension)
  var DDD_SLIDES = ["wf1", "wf2", "wf4"];
  var DDD_DIMS   = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"];
  var dddSlide   = 0;
  var dddFigs    = null;
  var dddWfOpen  = false;
  var dddDims    = { PRODUCT: false, SUBPRODUCT: true, CUSTOMER_TYPE: true, AUM: true, SEGMENT: true };
  var dddDatesLoaded = false;
  var dddDateSet     = null;   // Set of valid YYYY-MM-DD strings
  var dddBubMeta   = {};
  var dddBubPdims  = {};
  var dddBubFilter = {};
  var dddBubMerges = sharedDimMerges;   // gruplama ortak hafızada
  var dddBubAggMembers = {};

  // Cost Analysis > Monthly Averages state (parallel to Deposit Detail with
  // its own date dropdowns so date selection is independent from Realized NII)
  var CA_MON_SLIDES = ["wf1", "wf2", "wf4"];
  var caTab         = "daily-evolution"   ;   // "monthly-averages" | "daily-evolution"
  var caMonSlide    = 0;
  var caMonFigs     = null;
  var caMonWfOpen   = false;
  var caMonDims     = { PRODUCT: false, SUBPRODUCT: true, CUSTOMER_TYPE: true, AUM: true, SEGMENT: true };
  var caMonDatesLoaded = false;
  var caMonBubMeta   = {};
  var caMonBubPdims  = {};
  var caMonBubFilter = {};
  var caMonBubMerges = sharedDimMerges;   // gruplama ortak hafızada
  var caMonBubAggMembers = {};

  // ── Tenor Analysis state ────────────────────────────────────────────────────
  var taTab            = "daily-evolution"   ;   // "monthly-averages" | "daily-evolution"
  var taDatesLoaded    = false;
  var taMonthlyDates   = [];                    // ["YYYY-MM-DD", ...]
  var taDailyDates     = [];                    // ["YYYY-MM-DD", ...]
  var taDailyDateSet   = null;
  var taFilterMeta     = {};                    // {DIM: [val, ...]}
  // Bubble-style filter state: { DIM: { val: true/false } } + merges: { DIM: [{name,members}] }
  var taMonBubState  = {};    // filter checkbox state for monthly
  var taMonBubMerges = sharedDimMerges;   // gruplama ortak hafızada
  var taDlyBubState  = {};    // filter checkbox state for daily
  var taDlyBubMerges = sharedDimMerges;   // gruplama ortak hafızada
  var taMonPayload   = null;  // last successful monthly payload
  var taDlyPayload   = null;  // last successful daily payload
  var taMonWfSlide   = 0;
  var taDlyWfSlide   = 0;
  var TA_WF_SLIDES   = ["wf1", "wf2", "wf4"];
  var TA_DIMS        = ["PRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"];

  // ── Balance Analysis state ──────────────────────────────────────────────────
  var baTab            = "daily-evolution"   ;   // "monthly-averages" | "daily-evolution"
  var baDatesLoaded    = false;
  var baMonthlyDates   = [];
  var baDailyDates     = [];
  var baDailyDateSet   = null;
  var baFilterMeta     = {};
  var baMonBubState  = {};
  var baMonBubMerges = sharedDimMerges;   // gruplama ortak hafızada
  var baDlyBubState  = {};
  var baDlyBubMerges = sharedDimMerges;   // gruplama ortak hafızada
  var baMonPayload   = null;
  var baDlyPayload   = null;
  // Balance Analysis filtre serileştirme boyutları — panel chip'leriyle AYNI
  // küme olmalı. SUBPRODUCT ve MATURITY_BUCKET (TENOR) sonradan eklendiğinde
  // buraya işlenmemişti → seçimler backend'e hiç gitmiyordu (KPI/waterfall/
  // heatmap etkilenmiyordu). Backend zaten ikisini de işler
  // (_parse_balance_dim_filters + _filter_by_dims/DIM_BUCKET).
  var BA_DIMS        = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT", "MATURITY_BUCKET"];
  // Balance heatmap mode: "delta" = balance change, "abs" = balance at Date(End)
  var baHmMode       = { "ba-mon": "delta", "ba-dly": "delta" };
  var baCustHmMode   = { "ba-mon": "delta", "ba-dly": "delta" };   // Customer Number heatmap modu
  // Balance / Customer heatmap'i tek kartta metrik slider'ıyla seçilir.
  var baHmMetric     = { "ba-mon": "balance", "ba-dly": "balance" }; // "balance" | "customer"
  // Cost analysis rate heatmap mode: "delta" = rate change (bps), "abs" = rate at Date(End)
  var caRateHmMode   = { "ca-mon": "delta", "ddd": "delta" };

  // Historical sources that show the tab switcher
  var HISTORICAL_SOURCES = ["Realized NII"];

  // ── Bubble filter + merge helpers ────────────────────────────────────────────

  // Parse an AUM band name. Returns { lo, hi } in absolute units, or null.
  //   "AUM_0_100K"   → { lo: 0, hi: 100000 }
  //   "AUM_5M_10M"   → { lo: 5e6, hi: 1e7 }
  //   "AUM_10M+"     → { lo: 1e7, hi: Infinity }
  function _parseAumBand(s) {
    if (!s) return null;
    function num(raw, suf) {
      var n = parseFloat(String(raw).replace(",", "."));
      if (!isFinite(n)) return NaN;
      var u = (suf || "").toUpperCase();
      if (u === "K") n *= 1e3;
      else if (u === "M") n *= 1e6;
      return n;
    }
    var m1 = /^AUM_(\d+(?:[.,]\d+)?)([KM]?)_(\d+(?:[.,]\d+)?)([KM]?)$/i.exec(s);
    if (m1) {
      var lo = num(m1[1], m1[2]);
      var hi = num(m1[3], m1[4]);
      if (isFinite(lo) && isFinite(hi)) return { lo: lo, hi: hi, openEnded: false };
    }
    var m2 = /^AUM_(\d+(?:[.,]\d+)?)([KM]?)\+$/i.exec(s);
    if (m2) {
      var lo2 = num(m2[1], m2[2]);
      if (isFinite(lo2)) return { lo: lo2, hi: Infinity, openEnded: true };
    }
    return null;
  }

  function _fmtAumNum(n) {
    if (n === 0) return "0";
    if (n >= 1e6) {
      var v = n / 1e6;
      return (v % 1 === 0 ? v.toFixed(0) : v.toString()) + "M";
    }
    if (n >= 1e3) {
      var v2 = n / 1e3;
      return (v2 % 1 === 0 ? v2.toFixed(0) : v2.toString()) + "K";
    }
    return String(n);
  }

  function _fmtAumBand(lo, hi) {
    if (!isFinite(hi)) return "AUM_" + _fmtAumNum(lo) + "+";
    return "AUM_" + _fmtAumNum(lo) + "_" + _fmtAumNum(hi);
  }

  // Order values for a dimension. AUM uses numeric band order; others alphabetic.
  // Unparseable AUM entries (rare/legacy) go to the end.
  function _parseBucketLower(s) {
    // Parse "0-30" / "30-90" → lower bound integer for numeric sort. Returns Infinity on failure.
    var m = String(s).match(/^(\d+)/);
    return m ? parseInt(m[1], 10) : Infinity;
  }

  // NP New Business AUM band etiketleri ("0-1M","200M-500M","1B+") — _AUM_LABELS
  // (np_agg.py) ile aynı kanonik numerik sıra. _parseAumBand bu formatı parse
  // edemediği için (AUM_ prefix + B desteği yok) ayrı sıra listesi tutulur.
  var _NP_AUM_ORDER = ["0-1M","1M-2M","2M-5M","5M-10M","10M-25M","25M-50M",
                       "50M-100M","100M-200M","200M-500M","500M-1B","1B+"];
  function _npAumIndex(b) {
    var i = _NP_AUM_ORDER.indexOf(b);
    return i < 0 ? 999 : i;
  }
  // Bir NP band etiketinin alt/üst sınır metni: "0-1M"→{lo:"0",hi:"1M"},
  // "1B+"→{lo:"1B",hi:"+"} (açık uçlu).
  function _npBandBounds(b) {
    if (b.indexOf("-") >= 0) {
      var p = b.split("-");
      return { lo: p[0], hi: p[1] };
    }
    return { lo: b.replace("+", ""), hi: "+" };
  }

  // ── Outstanding "AUM" ↔ NP "AUM_BAND" gruplama köprüsü (best effort) ────────
  // İki taraf AUM'u farklı binler: outstanding AUM_1M_5M... vs NP 1M-2M/2M-5M...
  // Bir tarafta yapılan grup, sayısal aralığa çevrilir ve karşı tarafın bu
  // aralığın İÇİNDE TAM kalan bantlarına eşlenir (kısmen taşan bant alınmaz —
  // sınırlar hizalanmıyorsa o taraf grubu temsil edemez, grup yansıtılmaz).
  var _OS_AUM_ORDER = ["AUM_0_100K","AUM_100K_500K","AUM_500K_1M","AUM_1M_5M",
                       "AUM_5M_10M","AUM_10M_20M","AUM_20M_25M","AUM_25M_30M",
                       "AUM_30M_50M","AUM_50M_75M","AUM_75M_100M","AUM_100M_200M",
                       "AUM_200M+"];
  function _npBandRange(b) {
    // "0-1M"→{lo:0,hi:1e6}, "500M-1B"→{lo:5e8,hi:1e9}, "1B+"→{lo:1e9,hi:∞}.
    function num(s) {
      var m = /^(\d+(?:[.,]\d+)?)\s*([KMB]?)$/i.exec(String(s).trim());
      if (!m) return NaN;
      var n = parseFloat(m[1].replace(",", "."));
      var u = (m[2] || "").toUpperCase();
      if (u === "K") n *= 1e3; else if (u === "M") n *= 1e6; else if (u === "B") n *= 1e9;
      return n;
    }
    if (!b || b === "Bilinmiyor") return null;
    b = String(b);
    if (b.slice(-1) === "+") {
      var lo2 = num(b.slice(0, -1));
      return isFinite(lo2) ? { lo: lo2, hi: Infinity } : null;
    }
    if (b.indexOf("-") >= 0) {
      var p = b.split("-");
      var lo = num(p[0]), hi = num(p[1]);
      return (isFinite(lo) && isFinite(hi)) ? { lo: lo, hi: hi } : null;
    }
    return null;
  }
  function _aumRangeOf(dim, label) {
    return dim === "AUM" ? _parseAumBand(label) : _npBandRange(label);
  }
  // Karşı boyutun aday bant listesi: yüklüyse sayfa metalarından, yoksa kanonik.
  function _aumVocab(dim) {
    if (dim === "AUM") {
      var seen = {};
      [typeof baFilterMeta !== "undefined" ? baFilterMeta : null,
       typeof taFilterMeta !== "undefined" ? taFilterMeta : null,
       typeof caMonBubMeta !== "undefined" ? caMonBubMeta : null,
       typeof dddBubMeta   !== "undefined" ? dddBubMeta   : null].forEach(function(meta) {
        ((meta && meta["AUM"]) || []).forEach(function(v) { seen[v] = true; });
      });
      var vals = Object.keys(seen);
      return vals.length ? _sortDimValues("AUM", vals) : _OS_AUM_ORDER.slice();
    }
    var np = (typeof npVpMeta !== "undefined" && npVpMeta && npVpMeta["AUM_BAND"]) || [];
    if (!np.length) return _NP_AUM_ORDER.slice();
    var have = {};
    np.forEach(function(v) { have[v] = true; });
    return _NP_AUM_ORDER.filter(function(b) { return have[b]; });
  }
  // dim'de grup eklendi/söküldü → karşı AUM boyutuna yansıt. Ayna gruplar
  // `_mirror` alanını taşır; ayna grup tekrar yansıtılmaz (döngü koruması).
  // Ayna grubu kullanıcı sökerse kaynağı da sökülür (grup paylaşımı simetrik).
  function _mirrorAumMergeAcross(dim, grp, action) {
    var tgt = (dim === "AUM") ? "AUM_BAND" : (dim === "AUM_BAND" ? "AUM" : null);
    if (!tgt || !grp) return;
    var list = sharedDimMerges[tgt] = sharedDimMerges[tgt] || [];
    // Mevcut ayna eşlerini temizle (remove + add'in upsert davranışı).
    for (var i = list.length - 1; i >= 0; i--) {
      if (list[i]._mirror === grp.name || (grp._mirror && list[i].name === grp._mirror)) {
        list.splice(i, 1);
      }
    }
    if (action !== "add" || grp._mirror) return;
    var lo = Infinity, hi = -Infinity;
    (grp.members || []).forEach(function(m) {
      var r = _aumRangeOf(dim, m);
      if (!r) return;
      if (r.lo < lo) lo = r.lo;
      if (r.hi > hi) hi = r.hi;
    });
    if (!(lo < hi)) return;
    var members = _aumVocab(tgt).filter(function(b) {
      var r = _aumRangeOf(tgt, b);
      return r && r.lo >= lo && r.hi <= hi;   // aralığın içinde TAM kalanlar
    });
    if (!members.length) return;
    list.push({ name: grp.name, members: members, _mirror: grp.name });
  }

  function _sortDimValues(dim, vals) {
    if (dim === "AUM_BAND") {
      return vals.slice().sort(function(a, b) { return _npAumIndex(a) - _npAumIndex(b); });
    }
    if (dim === "AUM") {
      return vals.slice().sort(function(a, b) {
        var pa = _parseAumBand(a);
        var pb = _parseAumBand(b);
        if (pa && pb) return pa.lo - pb.lo;
        if (pa) return -1;
        if (pb) return 1;
        return String(a).localeCompare(String(b));
      });
    }
    if (dim === "MATURITY_BUCKET") {
      return vals.slice().sort(function(a, b) {
        var na = _parseBucketLower(a), nb = _parseBucketLower(b);
        if (na !== Infinity && nb !== Infinity) return na - nb;
        if (na !== Infinity) return -1;
        if (nb !== Infinity) return 1;
        return String(a).localeCompare(String(b));
      });
    }
    return vals.slice().sort(function(a, b) { return String(a).localeCompare(String(b)); });
  }

  // Build a "Grupla" action result for the selected values.
  // For AUM: fills any gaps between min and max selected indices.
  // For others: returns selected as-is.
  // Returns { name, members } or null when nothing to group (< 2 effective members).
  function _buildMergeGroup(dim, allOrdered, selected) {
    var selSet = {};
    selected.forEach(function(v) { selSet[v] = true; });
    if (dim === "AUM_BAND") {
      // NP band'leri için gap-fill: seçilenlerin min..max index aralığındaki
      // tüm band'leri kapsa (kanonik _NP_AUM_ORDER üzerinden).
      var ordered = _NP_AUM_ORDER.filter(function(b) {
        return allOrdered.indexOf(b) >= 0;
      });
      var lo = -1, hi = -1;
      for (var i = 0; i < ordered.length; i++) {
        if (selSet[ordered[i]]) { if (lo === -1) lo = i; hi = i; }
      }
      if (lo === -1 || lo === hi) return null;
      var members = ordered.slice(lo, hi + 1);
      var bLo = _npBandBounds(ordered[lo]).lo;
      var bHi = _npBandBounds(ordered[hi]).hi;
      var name = (bHi === "+") ? (bLo + "+") : (bLo + "-" + bHi);
      return { name: name, members: members };
    }
    if (dim === "AUM") {
      // Use parseable bands in order; find the index span covered by selection
      var parsed = allOrdered.map(function(v) { return _parseAumBand(v); });
      var lo = -1, hi = -1;
      for (var i = 0; i < allOrdered.length; i++) {
        if (selSet[allOrdered[i]] && parsed[i]) {
          if (lo === -1) lo = i;
          hi = i;
        }
      }
      if (lo === -1 || lo === hi) return null;
      // Gap-fill: include all parseable bands between lo and hi (inclusive)
      var members = [];
      for (var j = lo; j <= hi; j++) {
        if (parsed[j]) members.push(allOrdered[j]);
      }
      var name = _fmtAumBand(parsed[lo].lo, parsed[hi].hi);
      return { name: name, members: members };
    }
    if (dim === "MATURITY_BUCKET") {
      // Vade bucket'ları "lo-hi" formatında → grup adı = min-lo–max-hi (temiz
      // aralık); grafik ekseninde birleşik bucket okunur görünür. Üyeler tam
      // seçilenler (gap-fill yok → kullanıcı ne seçtiyse o).
      var sel = selected.filter(function(v) {
        return /^\d+-\d+$/.test(String(v));
      });
      if (sel.length < 2) {
        return selected.length < 2 ? null : { name: selected.join(","), members: selected.slice() };
      }
      var los = sel.map(function(v) { return parseInt(String(v).split("-")[0], 10); });
      var his = sel.map(function(v) { return parseInt(String(v).split("-")[1], 10); });
      var name = Math.min.apply(null, los) + "-" + Math.max.apply(null, his);
      return { name: name, members: sel.slice() };
    }
    // Generic: free merge of all selected values
    if (selected.length < 2) return null;
    return { name: selected.join(","), members: selected.slice() };
  }

  // Render filter dropdowns into panelId.
  // meta    = { dim: [val, ...] }
  // state   = { dim: { val: true/false } }   — visibility filter (mutated)
  // merges  = { dim: [{ name, members: [val,...] }, ...] }  (mutated)
  // onChange() — re-render bubbles after any state/merge change
  function _renderBubFilters(panelId, meta, state, merges, onChange) {
    var panel = document.getElementById(panelId);
    if (!panel) return;
    panel.innerHTML = "";
    var dims = Object.keys(meta || {});
    if (!dims.length) return;

    dims.forEach(function(dim) {
      var allVals = meta[dim] || [];
      if (!allVals.length) return;

      // Strip empty-string values from dropdown entirely (per user request)
      var visibleRawVals = allVals.filter(function(v) { return v !== "" && v != null; });

      if (!merges[dim]) merges[dim] = [];
      var mergedGroups = merges[dim];
      // Values currently absorbed into a merged group (hidden from raw list)
      var absorbed = {};
      mergedGroups.forEach(function(g) {
        (g.members || []).forEach(function(m) { absorbed[m] = true; });
      });

      // Initialize state defaults — raw vals + merged group names
      if (!state[dim]) state[dim] = {};
      visibleRawVals.forEach(function(v) {
        if (state[dim][v] === undefined) state[dim][v] = true;
      });
      mergedGroups.forEach(function(g) {
        if (state[dim][g.name] === undefined) state[dim][g.name] = true;
      });

      var availableVals = _sortDimValues(dim, visibleRawVals.filter(function(v) { return !absorbed[v]; }));

      var wrap = document.createElement("div");
      wrap.className = "bub-filter-dd";
      wrap.dataset.dim = dim;   // BSC Presentation slide bazlı chip gizleme için

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "bub-filter-dd-btn";

      function effectiveEntries() {
        // Visible "row" identities = available raw vals (non-absorbed) + merged group names
        var rows = availableVals.slice();
        mergedGroups.forEach(function(g) { rows.push(g.name); });
        return rows;
      }

      function updateBtnLabel() {
        var rows = effectiveEntries();
        var checked = rows.filter(function(v) { return state[dim][v] !== false; }).length;
        var sub;
        if (rows.length === 0)            sub = "—";
        else if (checked === rows.length) sub = "All (" + rows.length + ")";
        else if (checked === 0)           sub = "None";
        else                              sub = checked + " / " + rows.length;
        btn.innerHTML = '<span><b>' + dim + ':</b> ' + sub + '</span><span class="caret">▾</span>';
      }
      updateBtnLabel();

      var popup = document.createElement("div");
      popup.className = "bub-filter-dd-popup hidden";

      // Re-render this dropdown's contents (called after every merge/ungroup
      // since the row set changes).
      function rebuild() {
        popup.innerHTML = "";
        absorbed = {};
        mergedGroups.forEach(function(g) {
          (g.members || []).forEach(function(m) { absorbed[m] = true; });
        });
        availableVals = _sortDimValues(dim, visibleRawVals.filter(function(v) { return !absorbed[v]; }));

        // ── Top actions: Tümü | Hiçbiri ─────────────────────────────────────
        var actions = document.createElement("div");
        actions.className = "bub-filter-dd-actions";
        var allLink = document.createElement("a");
        allLink.textContent = "All";
        allLink.addEventListener("click", function(ev) {
          ev.preventDefault();
          effectiveEntries().forEach(function(v) { state[dim][v] = true; });
          rebuild(); updateBtnLabel(); onChange();
        });
        var noneLink = document.createElement("a");
        noneLink.textContent = "None";
        noneLink.addEventListener("click", function(ev) {
          ev.preventDefault();
          effectiveEntries().forEach(function(v) { state[dim][v] = false; });
          rebuild(); updateBtnLabel(); onChange();
        });
        actions.appendChild(allLink);
        actions.appendChild(document.createTextNode(" | "));
        actions.appendChild(noneLink);
        popup.appendChild(actions);

        // ── Raw value checkboxes (ordered) ──────────────────────────────────
        var checkboxes = {};
        availableVals.forEach(function(v) {
          var lblEl = document.createElement("label");
          lblEl.className = "bub-filter-dd-opt";
          var cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = state[dim][v] !== false;
          cb.addEventListener("change", function() {
            state[dim][v] = cb.checked;
            updateGroupBtn();
            updateBtnLabel();
            onChange();
          });
          checkboxes[v] = cb;
          lblEl.appendChild(cb);
          lblEl.appendChild(document.createTextNode(" " + (v || "(empty)")));
          popup.appendChild(lblEl);
        });

        // ── Grupla button (enabled when ≥ 2 raw vals are checked) ───────────
        var groupBtn = document.createElement("button");
        groupBtn.type = "button";
        groupBtn.className = "bub-filter-dd-groupbtn";
        groupBtn.textContent = "➕ Group Selected";
        function updateGroupBtn() {
          var selected = availableVals.filter(function(v) { return state[dim][v] !== false; });
          // For AUM: need ≥ 2 parseable; for others ≥ 2 raw vals
          if (dim === "AUM") {
            var p = selected.filter(function(v) { return !!_parseAumBand(v); });
            groupBtn.disabled = p.length < 2;
          } else {
            groupBtn.disabled = selected.length < 2;
          }
        }
        updateGroupBtn();
        groupBtn.addEventListener("click", function() {
          var selected = availableVals.filter(function(v) { return state[dim][v] !== false; });
          var grp = _buildMergeGroup(dim, availableVals, selected);
          if (!grp) return;
          // Reject duplicate group name → reuse / append disambiguator
          var nameTaken = mergedGroups.some(function(g) { return g.name === grp.name; });
          if (nameTaken) grp.name = grp.name + " (" + (mergedGroups.length + 1) + ")";
          mergedGroups.push(grp);
          state[dim][grp.name] = true;
          // AUM ↔ AUM_BAND: grubu karşı sayfa ailesine best-effort yansıt.
          _mirrorAumMergeAcross(dim, grp, "add");
          // Members absorbed into group → their visibility filter is cleared
          // (they're effectively hidden by absence from the row list).
          rebuild(); updateBtnLabel(); onChange();
        });
        popup.appendChild(groupBtn);

        // ── Merged groups section ───────────────────────────────────────────
        if (mergedGroups.length) {
          var sep = document.createElement("div");
          sep.className = "bub-filter-dd-sep";
          popup.appendChild(sep);
          var hdr = document.createElement("div");
          hdr.className = "bub-filter-dd-grp-hdr";
          hdr.textContent = "Gruplar (" + mergedGroups.length + ")";
          popup.appendChild(hdr);
          mergedGroups.slice().forEach(function(g) {
            var row = document.createElement("div");
            row.className = "bub-filter-dd-merged";
            var nameSpan = document.createElement("label");
            nameSpan.className = "bub-filter-dd-merged-name";
            nameSpan.title = g.members.join(", ");
            var cb = document.createElement("input");
            cb.type = "checkbox";
            cb.checked = state[dim][g.name] !== false;
            cb.addEventListener("change", function() {
              state[dim][g.name] = cb.checked;
              updateBtnLabel();
              onChange();
            });
            nameSpan.appendChild(cb);
            nameSpan.appendChild(document.createTextNode(" " + g.name +
              " (" + g.members.length + ")"));
            var xBtn = document.createElement("span");
            xBtn.className = "bub-filter-dd-merged-x";
            xBtn.textContent = "×";
            xBtn.title = "Grubu boz";
            xBtn.addEventListener("click", function() {
              // Remove this group and restore members as visible
              var idx = mergedGroups.indexOf(g);
              if (idx >= 0) mergedGroups.splice(idx, 1);
              delete state[dim][g.name];
              g.members.forEach(function(m) { state[dim][m] = true; });
              // AUM ↔ AUM_BAND: ayna grubu da kaldır (paylaşım simetrik).
              _mirrorAumMergeAcross(dim, g, "remove");
              rebuild(); updateBtnLabel(); onChange();
            });
            row.appendChild(nameSpan);
            row.appendChild(xBtn);
            popup.appendChild(row);
          });
        }
      }
      rebuild();

      btn.addEventListener("click", function(ev) {
        ev.stopPropagation();
        document.querySelectorAll(".bub-filter-dd-popup").forEach(function(p) {
          if (p !== popup) p.classList.add("hidden");
        });
        popup.classList.toggle("hidden");
      });
      popup.addEventListener("click", function(ev) { ev.stopPropagation(); });

      wrap.appendChild(btn);
      wrap.appendChild(popup);
      panel.appendChild(wrap);
    });
  }

  // Global outside-click closer for any open filter dropdown
  if (!window.__bubFilterClickBound) {
    document.addEventListener("click", function() {
      document.querySelectorAll(".bub-filter-dd-popup").forEach(function(p) {
        p.classList.add("hidden");
      });
    });
    window.__bubFilterClickBound = true;
  }

  // Extract full per-product data from the balance fig (which carries
  // b0_m, b1_m, r0% in customdata + r1 implicit in y%).
  function _extractFullBubData(balFig) {
    var out = {};
    if (!balFig || !balFig.data || !balFig.data[0]) return out;
    var t = balFig.data[0];
    var texts = t.text || [];
    var ys    = t.y    || [];
    var cd    = t.customdata || [];
    for (var i = 0; i < texts.length; i++) {
      var c = cd[i] || [0, 0, 0];
      out[texts[i]] = {
        b0_m: c[0] || 0,
        b1_m: c[1] || 0,
        r0:   (c[2] || 0) / 100.0,
        r1:   (ys[i] || 0) / 100.0,
        // Opsiyonel outstanding (STOK) bakiye (NP bubble). Varsa Balance X = OS
        // deltası; yoksa (Cost) undefined → new-prod hacim deltası kullanılır.
        osb0_m: (c.length > 4 ? c[3] : undefined),
        osb1_m: (c.length > 4 ? c[4] : undefined),
      };
    }
    return out;
  }

  // Aggregate products into bubble points after applying filter + merges.
  // Returns { points: [{ name, b0_m, b1_m, r0, r1 }...],
  //           members: { aggName: [originalProduct,...] } }
  // activeDims (opsiyonel): verilirse SADECE bu boyutlara göre gruplanır — kapatılan
  // boyutlar composite'ten çıkar, o boyutun değerleri birleşir (New Business
  // "Dimensions" toggle'ı, Cost muadili; client-side, backend'e bağlı DEĞİL). Verilmezse
  // prodDims'teki tüm boyutlar kullanılır (Cost davranışı DEĞİŞMEZ).
  // splitMap (opsiyonel): { parentLabel: dimKey } — o etikete düşen ürünler,
  // verilen boyutun (efektif) değerine göre "parent_değer" alt-bubble'larına
  // ayrılır (per-bubble kırılım; Cost tek-tık seçim + Enter özelliği). Dönüşte
  // parents: { childLabel: parentLabel }.
  function _aggregateBubbles(balFig, state, merges, prodDims, activeDims, splitMap) {
    var full  = _extractFullBubData(balFig);
    var trace = balFig && balFig.data && balFig.data[0];
    var products = (trace && trace.text) || [];
    var parentsOut = {};
    // Dim member → group name
    var dimMemberToGroup = {};
    Object.keys(merges || {}).forEach(function(dim) {
      var map = {};
      (merges[dim] || []).forEach(function(g) {
        g.members.forEach(function(m) { map[m] = g.name; });
      });
      dimMemberToGroup[dim] = map;
    });
    var groups = {};      // aggKey → { name, b0_m, b1_m, wsum0, wsum1, members:[] }
    var membersMap = {};  // aggName → [originalProduct,...]
    products.forEach(function(prod) {
      var pd = prodDims[prod];
      if (!pd) return;
      var d = full[prod];
      if (!d) return;
      // Gruplanacak boyutlar: activeDims verildiyse yalnız onlar (kapatılanlar
      // çıkar → o boyut üzerinden toplanır); yoksa ürünün tüm boyutları.
      var dimsToUse = Object.keys(pd).filter(function(dim) {
        return !activeDims || activeDims.indexOf(dim) >= 0;
      });
      // Skip points whose any (KULLANILAN) dim value is empty — kapatılan boyutun
      // boş değeri artık gruplamayı iptal etmez.
      var skipEmpty = false;
      dimsToUse.forEach(function(dim) {
        if (pd[dim] === "" || pd[dim] == null) skipEmpty = true;
      });
      if (skipEmpty) return;
      // FİLTRE ⟂ GRUPLAMA: filtre TÜM boyutlarda uygulanır (filtre paneli tüm
      // grafikleri filtreler), gruplama yalnız dimsToUse ile. Böylece bir boyut
      // Dimensions'tan kapatılsa bile filtresi (ör. CCY=yalnız TRY) yine geçerli —
      // sadece composite'e/label'a girmez. Cost'ta activeDims yok → ikisi de tüm pd.
      var passed = true;
      Object.keys(pd).forEach(function(dim) {
        var raw = pd[dim];
        var grp = dimMemberToGroup[dim] && dimMemberToGroup[dim][raw];
        var effVal = grp || raw;
        var s = state[dim];
        if (s && s[effVal] === false) passed = false;
      });
      if (!passed) return;
      var effOrdered = dimsToUse.map(function(dim) {
        var raw = pd[dim];
        var grp = dimMemberToGroup[dim] && dimMemberToGroup[dim][raw];
        return { dim: dim, val: grp || raw };
      });
      var key = effOrdered.map(function(e) { return e.dim + "=" + e.val; }).join("|");
      var label = effOrdered.map(function(e) { return e.val; }).filter(Boolean).join("_");
      // Per-bubble split: bu etiket splitMap'te ise ürünü split-boyutunun
      // (merge-grubu uygulanmış) değerine göre alt-bubble'a yönlendir.
      if (splitMap && splitMap[label]) {
        var sd = splitMap[label];
        var sraw = pd[sd];
        var sgrp = dimMemberToGroup[sd] && dimMemberToGroup[sd][sraw];
        var sval = (sgrp || sraw);
        if (sval != null && sval !== "") {
          key = key + "|SPLIT:" + sd + "=" + sval;
          var childLbl = label + "_" + sval;
          parentsOut[childLbl] = label;
          label = childLbl;
        }
      }
      if (!groups[key]) {
        groups[key] = { name: label, b0_m: 0, b1_m: 0, wsum0: 0, wsum1: 0,
                        osb0_m: 0, osb1_m: 0, _os: false };
        membersMap[label] = [];
      }
      var g = groups[key];
      g.b0_m  += d.b0_m;
      g.b1_m  += d.b1_m;
      g.wsum0 += d.b0_m * d.r0;
      g.wsum1 += d.b1_m * d.r1;
      // Outstanding (STOK) additive toplanır — client gruplaması OS deltasını doğru
      // toplar (backend hacim payıyla dağıtmıştı). Yalnız NP bubble'da mevcut.
      if (d.osb0_m != null || d.osb1_m != null) {
        g.osb0_m += (d.osb0_m || 0);
        g.osb1_m += (d.osb1_m || 0);
        g._os = true;
      }
      membersMap[label].push(prod);
    });
    var points = Object.keys(groups).map(function(k) {
      var g = groups[k];
      g.r0 = g.b0_m !== 0 ? g.wsum0 / g.b0_m : 0;
      g.r1 = g.b1_m !== 0 ? g.wsum1 / g.b1_m : 0;
      if (!g._os) { g.osb0_m = undefined; g.osb1_m = undefined; }  // Cost → new-prod X
      return g;
    });
    return { points: points, members: membersMap, parents: parentsOut };
  }

  function _sizerefFor(sizes) {
    var mx = 0;
    for (var i = 0; i < sizes.length; i++) if (sizes[i] > mx) mx = sizes[i];
    return 2.0 * (mx || 1) / (45.0 * 45.0);
  }

  // Bubble boyut metriği. Default "avg" = (|b0|+|b1|)/2 (Cost davranışı, DEĞİŞMEZ);
  // "t1" = |b1| = Date(End) penceresi hacmi (New Business isteği). Hem çizim boyutu
  // (_buildBalFig/_buildRateFig) hem de min-size eşiği (_bubPtSize) aynı metriği kullanır.
  function _bubSizeOf(p, mode) {
    return mode === "t1" ? Math.abs(p.b1_m) : (Math.abs(p.b0_m) + Math.abs(p.b1_m)) / 2.0;
  }

  // ── Bubble renk kodlaması: ASINH (işaretli logaritmik) dönüşüm ──────────────
  // Sorun: doğrusal skala + tek uç değer → çoğunluk paletin gri ortasına sıkışır.
  // Çözüm: renk konumu = asinh(Δ/s), s = M/20. Sıfır civarında ~doğrusal (küçük
  // değişimler hızla ton kazanır, YÖN hemen okunur), uçlara doğru log gibi
  // sıkışır (büyük resim korunur, kırpma yok, sıralama bozulmaz). colorMax (M)
  // verilirse skala ona SABİTLENİR (split/merge'de renk kayması olmaz); yoksa
  // mevcut kümenin max |Δ|'sı kullanılır.
  function _asinh(v) { return Math.log(v + Math.sqrt(v * v + 1)); }
  // Dar-nötr 5-durak paletler: nötr bant ±%1'e indirildi, hemen dışında doygun
  // ara tonlar → azıcık sapan bubble bile renklenir; gerçek ~0 nötr kalır.
  var _BUB_SCALE_BAL = [[0, "#B8826B"], [0.40, "#B08A74"], [0.49, "#5C6478"],
                        [0.51, "#5C6478"], [0.60, "#82997E"], [1, "#7A9B7E"]];
  var _BUB_SCALE_RATE = [[0, "#7A9B7E"], [0.40, "#82997E"], [0.49, "#5C6478"],
                         [0.51, "#5C6478"], [0.60, "#B08A74"], [1, "#B8826B"]];
  // İnce colorbar: işaretler HAM değerlerle (dönüşüm doğrusal olmadığından
  // kullanıcı sıkıştırmayı çubuktan okur; kesin sayılar hover'da).
  function _bubColorCfg(values, colorMax, unit) {
    var M = colorMax > 0 ? colorMax : 0;
    if (!M) values.forEach(function (v) { var a = Math.abs(v); if (a > M) M = a; });
    if (!(M > 0)) M = 1;
    var s = M / 20;
    var cvals = values.map(function (v) { return _asinh(v / s); });
    var cmax = _asinh(M / s);
    var rawTicks = [-M, -M / 4, 0, M / 4, M];
    var fmt = function (v) {
      var a = Math.abs(v), txt;
      if (a >= 1000) txt = (v / 1000).toFixed(1).replace(/\.0$/, "") + "k";
      else txt = String(Math.round(v));
      return (v > 0 ? "+" : "") + txt;
    };
    return {
      cvals: cvals, cmin: -cmax, cmax: cmax,
      colorbar: {
        thickness: 8, len: 0.65, outlinewidth: 0,
        tickvals: rawTicks.map(function (v) { return _asinh(v / s); }),
        ticktext: rawTicks.map(fmt),
        tickfont: { size: 10, color: "#7A8399" },
        title: { text: unit, font: { size: 10, color: "#7A8399" }, side: "top" },
      },
    };
  }

  function _buildBalFig(points, srcLayout, sizeMode, colorMax) {
    if (!points.length) return { data: [], layout: srcLayout || { title: { text: "No data" } } };
    // Outstanding (STOK) modu: nokta osb taşıyorsa Balance X = OS deltası (End−Start),
    // BOYUT ise yine new-prod hacmi (_bubSizeOf). Cost'ta osb yok → new-prod deltası.
    var anyOS = points.some(function(p) { return p.osb0_m != null || p.osb1_m != null; });
    var x = [], y = [], text = [], sizes = [], colors = [], cd = [];
    points.forEach(function(p) {
      var os0 = p.osb0_m || 0, os1 = p.osb1_m || 0;
      var delta = anyOS ? (os1 - os0) : (p.b1_m - p.b0_m);
      x.push(delta); y.push(p.r1 * 100); text.push(p.name);
      sizes.push(_bubSizeOf(p, sizeMode));   // boyut = new-prod hacmi (DEĞİŞMEZ)
      colors.push(delta);
      cd.push(anyOS ? [os0, os1, p.r0 * 100, p.name, p.b1_m]
                    : [p.b0_m, p.b1_m, p.r0 * 100, p.name]);
    });
    var cc = _bubColorCfg(colors, colorMax, anyOS ? "Δ OS ₺M" : "Δ ₺M");
    return {
      data: [{
        type: "scatter", mode: "markers+text",
        x: x, y: y, text: text,
        textposition: "top center",
        textfont: { size: 11, color: _plotInk(), family: "system-ui" },
        marker: { size: sizes, sizemode: "area", sizeref: _sizerefFor(sizes), sizemin: 4,
                  color: cc.cvals, colorscale: _BUB_SCALE_BAL,
                  cmin: cc.cmin, cmax: cc.cmax,
                  showscale: true, colorbar: cc.colorbar,
                  // Nötr dolgulu bubble da net bir nesne olsun: belirgin kontur +
                  // daha yüksek opaklık (siliklik yalnız ton meselesi değildi).
                  opacity: 0.9, line: { width: 1.4, color: "#8B95A7" } },
        customdata: cd,
        // SADE hover (kullanıcı isteği): Balance (t1 değeri, "t1" yazılmaz) +
        // X ekseni büyüklüğü + Y ekseni büyüklüğü. Fazlası kafa karıştırıyordu.
        hovertemplate: anyOS
          ? "<b>%{customdata[3]}</b><br>Balance: %{customdata[1]:,.0f} ₺M<br>"
            + "Δ Outstanding: %{x:,.0f} ₺M<br>"
            + "Rate: %{y:.2f}%<extra></extra>"
          : "<b>%{customdata[3]}</b><br>Balance: %{customdata[1]:,.0f} ₺M<br>"
            + "Δ Balance: %{x:,.0f} ₺M<br>"
            + "Rate: %{y:.2f}%<extra></extra>",
      }],
      layout: srcLayout || {},
    };
  }

  // colorMax: bkz. _buildBalFig — split/merge'de renk skalası sabitleme.
  function _buildRateFig(points, srcLayout, sizeMode, colorMax) {
    if (!points.length) return { data: [], layout: srcLayout || { title: { text: "No data" } } };
    var x = [], y = [], text = [], sizes = [], colors = [], cd = [];
    points.forEach(function(p) {
      var bps = (p.r1 - p.r0) * 10000.0;
      x.push(bps); y.push(p.r1 * 100); text.push(p.name);
      sizes.push(_bubSizeOf(p, sizeMode));
      colors.push(bps);
      cd.push([p.r0 * 100, p.b1_m, p.name]);
    });
    var cc = _bubColorCfg(colors, colorMax, "Δ bps");
    return {
      data: [{
        type: "scatter", mode: "markers+text",
        x: x, y: y, text: text,
        textposition: "top center",
        textfont: { size: 11, color: _plotInk(), family: "system-ui" },
        marker: { size: sizes, sizemode: "area", sizeref: _sizerefFor(sizes), sizemin: 4,
                  color: cc.cvals, colorscale: _BUB_SCALE_RATE,
                  cmin: cc.cmin, cmax: cc.cmax,
                  showscale: true, colorbar: cc.colorbar,
                  opacity: 0.9, line: { width: 1.4, color: "#8B95A7" } },
        customdata: cd,
        // SADE hover (kullanıcı isteği): Balance (t1 değeri, "t1" yazılmaz) +
        // X (Δ Rate, bps) + Y (Rate %). NOT: bu Plotly derlemesi `%{x:+.0f}`
        // format'ını ayrıştıramıyor; `+d` hem yuvarlar hem +/- işaret verir.
        hovertemplate: "<b>%{customdata[2]}</b><br>Balance: %{customdata[1]:,.0f} ₺M<br>"
          + "Δ Rate: %{x:+d} bps<br>"
          + "Rate: %{y:.2f}%<extra></extra>",
      }],
      layout: srcLayout || {},
    };
  }

  // Reduce + render bubbles for a tab. aggMembersStore is mutated to map
  // each rendered bubble name → its underlying original product list (for
  // drill-down expansion when the user clicks a merged bubble).
  // Bubble "min size" GÖRSEL gösterim filtresi state'i — prefix (dd/ca-mon/ddd)
  // başına 0-100 arası yüzde. Bu bir VERİ filtresi DEĞİL: sadece hangi bubble'ın
  // ÇİZİLECEĞİNİ belirler; WAvg gibi tüm hesaplar filtrelenmemiş TÜM noktalar
  // üzerinden yapılır. Slider sürüklenirken input event'leri rAF ile birleştirilir.
  var _bubMinSize = { "dd": 0, "ca-mon": 0, "ddd": 0, "np-vp": 0 };

  // ── Bubble SEÇİM + per-bubble SPLIT (yalnız Outstanding Cost: ca-mon / ddd) ──
  // Tek tık = seç (parlar; diğerleri söner). Çift tık = drill (eski davranış).
  // Enter (seçiliyken) = bubble'ı üstteki "Detailed Dim"e göre KENDİ İÇİNDE böl
  // (global gruplama DEĞİL); çocuk tıklanınca kardeş grubu seçilir, Enter geri
  // birleştirir. Geçişler ~0.45sn Plotly animate ile.
  var _BUB_SELECT_PREFIXES = { "ca-mon": 1, "ddd": 1 };
  var _bubSel = {};        // prefix → {labels:[..], parent:null|parentLabel} | null
  var _bubSplit = {};      // prefix → { parentLabel: dimKey } (per-bubble kırılım)
  var _bubParents = {};    // prefix → { childLabel: parentLabel } (son render'dan)
  // Tam-ekran tarih slider'ının HAREKET kilidi (play/drag): varsa _renderBubbles
  // eksenleri union aralığa sabitler, renk çapasını union max'a çeker ve
  // (trans > 0 ise) Plotly geçiş animasyonu ekler. Durunca (settle) silinir.
  var _bubMotion = {};     // prefix → { ax:{bal:{x,y},rate:{x,y}}, colorMaxBal, colorMaxRate, trans }
  var _bubClickTimer = {};
  var _bubClickLast = {};

  function _bubRerender(prefix) {
    if (prefix === "ca-mon") _renderCaMonBubbles();
    else if (prefix === "ddd") _renderDddBubbles();
  }

  function _bubCtxStores(prefix) {
    return prefix === "ca-mon"
      ? { pdims: caMonBubPdims, members: caMonBubAggMembers, merges: caMonBubMerges }
      : { pdims: dddBubPdims,   members: dddBubAggMembers,   merges: dddBubMerges };
  }

  function _bubBreakDimKey(prefix) {
    var bd = (document.getElementById(prefix + "-break-dim") || {}).value || "PRODUCT";
    return bd === "TENOR" ? "MATURITY_BUCKET" : bd;   // pdims anahtarına çevir
  }

  // Seçimi vurgula: seçili noktalar parlak (opacity 1 + amber kontur), diğerleri
  // sönük. Nokta kimliği customdata'daki NAME ile (text smart-label'da değişebilir).
  function _applyBubSel(fig, sel, nameIdx) {
    if (!sel || !sel.labels || !sel.labels.length) return;
    if (!fig.data || !fig.data.length) return;
    var t = fig.data[0];
    var cd = t.customdata || [];
    var selSet = {};
    sel.labels.forEach(function(l) { selSet[l] = 1; });
    var baseLine = (t.marker && t.marker.line && t.marker.line.color) || "rgba(255,255,255,0.20)";
    var op = [], lw = [], lc = [];
    for (var i = 0; i < cd.length; i++) {
      var nm = cd[i] && cd[i][nameIdx];
      var s = selSet[nm];
      op.push(s ? 1.0 : 0.35);
      lw.push(s ? 2.5 : 1);
      lc.push(s ? "#D4A574" : baseLine);
    }
    t.marker = Object.assign({}, t.marker, { opacity: op });
    t.marker.line = Object.assign({}, (t.marker.line || {}), { width: lw, color: lc });
  }

  function _toggleBubSelect(prefix, label) {
    var parents = _bubParents[prefix] || {};
    var sel;
    if (parents[label]) {
      var p = parents[label];
      var sibs = Object.keys(parents).filter(function(k) { return parents[k] === p; });
      sel = { labels: sibs, parent: p };     // çocuğa tık → TÜM kardeşler seçili
    } else {
      sel = { labels: [label], parent: null };
    }
    var cur = _bubSel[prefix];
    _bubSel[prefix] = (cur && cur.labels.join("|") === sel.labels.join("|")) ? null : sel;
    // Enter'ın form elemanına gitmemesi için odağı bırak (bubble seçimi = klavye
    // etkileşiminin hedefi artık grafik).
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    _bubRerender(prefix);
  }

  // Grafikte bir noktanın (customdata name ile) indeks/konumunu bul.
  function _bubFindIdx(el, name, nameIdx) {
    var cd = (el && el.data && el.data[0] && el.data[0].customdata) || [];
    for (var i = 0; i < cd.length; i++) if (cd[i] && cd[i][nameIdx] === name) return i;
    return -1;
  }

  // SPLIT animasyonu: çocuklar önce ebeveyn konumunda minik doğar, ~0.45sn'de
  // gerçek konum/boylarına açılır. from = {bal:{x,y}, rate:{x,y}} (yakalanmış).
  function _bubAnimateFrom(prefix, from, childLabels) {
    [["bal", prefix + "-bub-bal", 3], ["rate", prefix + "-bub-rate", 2]].forEach(function(cfg) {
      var kind = cfg[0], el = document.getElementById(cfg[1]), nameIdx = cfg[2];
      if (!el || !el.data || !el.data.length || !from || !from[kind]) return;
      var t = el.data[0];
      var finX = (t.x || []).slice(), finY = (t.y || []).slice();
      var finS = ((t.marker && t.marker.size) || []).slice();
      var stX = finX.slice(), stY = finY.slice(), stS = finS.slice();
      var idxs = [];
      childLabels.forEach(function(l) {
        var i = _bubFindIdx(el, l, nameIdx);
        if (i >= 0) { idxs.push(i); stX[i] = from[kind].x; stY[i] = from[kind].y; stS[i] = Math.max(1, finS[i] * 0.15); }
      });
      if (!idxs.length) return;
      try {
        // Başlangıç konumlandırması restyle ile DEĞİL sıfır-süreli animate ile:
        // restyle autorange'i yeniden hesaplayıp eksenleri parent-konumundaki
        // sıkışık kümeye DARALTIYORDU; ardından redraw:false animasyon çocukları
        // eksen DIŞINA uçuruyordu. animate (redraw:false) eksenlere dokunmaz →
        // eksenler react'in hesapladığı nihai-fit aralıkta sabit kalır.
        Plotly.animate(el, { data: [{ x: stX, y: stY, "marker.size": stS }], traces: [0] },
          { transition: { duration: 0 }, frame: { duration: 0, redraw: false } })
          .then(function() {
            Plotly.animate(el, { data: [{ x: finX, y: finY, "marker.size": finS }], traces: [0] },
              { transition: { duration: 450, easing: "cubic-in-out" }, frame: { duration: 450, redraw: false } });
          });
      } catch (e) { /* animasyon başarısızsa statik hal zaten doğru */ }
    });
  }

  // MERGE animasyonu: çocuklar ~0.45sn'de boy-ağırlıklı merkezlerine toplanır,
  // ardından done() gerçek birleşik render'ı çizer.
  function _bubAnimateTo(prefix, childLabels, done) {
    var pending = 0, called = false;
    var finish = function() { if (!called) { called = true; done(); } };
    [["bal", prefix + "-bub-bal", 3], ["rate", prefix + "-bub-rate", 2]].forEach(function(cfg) {
      var el = document.getElementById(cfg[1]), nameIdx = cfg[2];
      if (!el || !el.data || !el.data.length) return;
      var t = el.data[0];
      var xs = (t.x || []).slice(), ys = (t.y || []).slice();
      var ss = ((t.marker && t.marker.size) || []).slice();
      var idxs = [];
      childLabels.forEach(function(l) { var i = _bubFindIdx(el, l, nameIdx); if (i >= 0) idxs.push(i); });
      if (idxs.length < 2) return;
      var wsum = 0, cx = 0, cy = 0;
      idxs.forEach(function(i) { var w = ss[i] || 1; wsum += w; cx += xs[i] * w; cy += ys[i] * w; });
      cx /= (wsum || 1); cy /= (wsum || 1);
      idxs.forEach(function(i) { xs[i] = cx; ys[i] = cy; });
      pending++;
      try {
        Plotly.animate(el, { data: [{ x: xs, y: ys }], traces: [0] },
          { transition: { duration: 450, easing: "cubic-in-out" }, frame: { duration: 450, redraw: false } })
          .then(function() { pending--; if (pending <= 0) finish(); })
          .catch(function() { pending--; if (pending <= 0) finish(); });
      } catch (e) { pending--; }
    });
    // Emniyet: animate promise'i gelmezse de birleşim gerçekleşsin.
    setTimeout(finish, 550);
  }

  function _bubSplitSelected(prefix, label) {
    var dim = _bubBreakDimKey(prefix);
    var st = _bubCtxStores(prefix);
    var members = (st.members && st.members[label]) || [];
    if (!members.length) return;
    // merge-grubu relabel'ını uygula (panelde gruplanmış değerler tek çocuk olur).
    var m2g = {};
    var dimUi = dim === "MATURITY_BUCKET" ? "MATURITY_BUCKET" : dim;
    (((st.merges || {})[dimUi]) || []).forEach(function(g) {
      (g.members || []).forEach(function(m) { m2g[m] = g.name; });
    });
    var effVals = {};
    members.forEach(function(p) {
      var pd = st.pdims[p];
      if (!pd) return;
      var raw = pd[dim];
      var v = m2g[raw] || raw;
      if (v != null && v !== "") effVals[String(v)] = 1;
    });
    var vals = Object.keys(effVals);
    var bdUi = (document.getElementById(prefix + "-break-dim") || {}).value || dim;
    if (vals.length < 2) {
      showError("'" + label + "' bubble already has a single value in the '" + bdUi + "' dimension — split would produce nothing.");
      return;
    }
    // Animasyon için ebeveynin mevcut konumunu yakala (her iki grafikte).
    var from = {};
    [["bal", prefix + "-bub-bal", 3], ["rate", prefix + "-bub-rate", 2]].forEach(function(cfg) {
      var el = document.getElementById(cfg[1]);
      var i = _bubFindIdx(el, label, cfg[2]);
      if (el && i >= 0) from[cfg[0]] = { x: el.data[0].x[i], y: el.data[0].y[i] };
    });
    _bubSplit[prefix] = _bubSplit[prefix] || {};
    _bubSplit[prefix][label] = dim;
    // Split sonrası seçim TEMİZLENİR: kullanıcı bir çocuğa tıklayınca kardeş
    // grubu seçilir (toggle mantığıyla çakışmasın — aksi halde çocuğa ilk tık
    // "zaten seçili grubu" bırakırdı).
    _bubSel[prefix] = null;
    var childLabels = vals.map(function(v) { return label + "_" + v; });
    _bubRerender(prefix);
    _bubAnimateFrom(prefix, from, childLabels);
  }

  function _bubMergeSelected(prefix, parentLabel) {
    var childLabels = (_bubSel[prefix] && _bubSel[prefix].labels) || [];
    _bubAnimateTo(prefix, childLabels, function() {
      if (_bubSplit[prefix]) delete _bubSplit[prefix][parentLabel];
      _bubSel[prefix] = { labels: [parentLabel], parent: null };
      _bubRerender(prefix);
    });
  }

  // Enter: Cost sayfasında seçili bubble varsa böl (kök) ya da birleştir (çocuk grubu).
  document.addEventListener("keydown", function(ev) {
    if (ev.key !== "Enter") return;
    var tg = ev.target;
    // Form elemanlarında Enter'ı karışma — TEK İSTİSNA "Detailed Dim" select'i:
    // kullanıcı boyutu seçtikten hemen sonra Enter'a basar (odak select'te kalır).
    if (tg && /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(tg.tagName)
           && !(tg.tagName === "SELECT" && /-break-dim$/.test(tg.id || ""))) return;
    // BSC Presentation Slide 2 (Balance to Cost) bubble'ı da Enter split/merge alır.
    var _inBscBub = (_bsc && _bsc.slide === 1);
    if (currentPage !== "cost-analysis" && !_inBscBub) return;
    var prefix = _inBscBub ? _bscPrefix(1, _bsc.mode)
               : (caTab === "monthly-averages") ? "ca-mon" : "ddd";
    var sel = _bubSel[prefix];
    if (!sel || !sel.labels || !sel.labels.length) return;
    ev.preventDefault();
    if (sel.parent) _bubMergeSelected(prefix, sel.parent);
    else _bubSplitSelected(prefix, sel.labels[0]);
  });
  var _bubMinRaf  = {};
  function _onBubMinSize(prefix, val) {
    _bubMinSize[prefix] = Math.max(0, Math.min(100, Number(val) || 0));
    if (_bubMinRaf[prefix]) return;
    _bubMinRaf[prefix] = requestAnimationFrame(function() {
      _bubMinRaf[prefix] = 0;
      var fn = prefix === "dd" ? _renderDdBubbles
             : prefix === "ca-mon" ? _renderCaMonBubbles
             : prefix === "np-vp" ? _renderNpVpBubbles
             : _renderDddBubbles;
      if (fn) fn();
    });
  }
  // Tüm script tek bir IIFE içinde; inline oninline="..." handler'ları GLOBAL
  // scope'ta çözülür. Bu yüzden slider'ın oninput'undan erişilebilmesi için
  // window'a açıyoruz (eski "Etiketleri Göster" onchange'i _toggleBubLabels'ı
  // global sanıp ÇALIŞMIYORDU — kaldırıldı).
  window._onBubMinSize = _onBubMinSize;
  function _bubPtSize(p, mode) { return _bubSizeOf(p, mode); }

  function _renderBubbles(balId, rateId, srcBalFig, srcRateFig,
                          state, merges, prodDims, aggMembersStore, prefix, activeDims) {
    if (!srcBalFig || !srcRateFig) return;
    var splitMap = (prefix && _bubSplit[prefix]) || null;
    var agg = _aggregateBubbles(srcBalFig, state, merges, prodDims, activeDims, splitMap);
    // Clear and repopulate aggMembersStore
    Object.keys(aggMembersStore).forEach(function(k) { delete aggMembersStore[k]; });
    Object.keys(agg.members).forEach(function(k) { aggMembersStore[k] = agg.members[k]; });
    if (prefix) {
      _bubParents[prefix] = agg.parents || {};
      // Filtre/merge/dimension değişimi seçili etiketleri yok ettiyse seçimi bırak.
      var _sel = _bubSel[prefix];
      if (_sel && !_sel.labels.every(function(l) { return agg.members[l] != null; })) {
        _bubSel[prefix] = null;
      }
    }
    // Bubble boyut metriği: New Business (np-vp) = Date(End) hacmi (|b1|); Cost = avg.
    var sizeMode = (prefix === "np-vp") ? "t1" : "avg";
    // Min-size GÖRSEL filtre: eşik = yüzde × (en büyük bubble boyutu). En büyük
    // bubble her zaman görünür kaldığından sizeref (bubble ölçeği) sabit kalır —
    // filtre yalnızca küçükleri gizler, kalanları yeniden ölçeklemez.
    var minPct = (prefix && _bubMinSize[prefix]) || 0;
    var maxSize = 0;
    agg.points.forEach(function(p) { var s = _bubPtSize(p, sizeMode); if (s > maxSize) maxSize = s; });
    var thr = (minPct / 100) * maxSize;
    var visPoints = minPct > 0
      ? agg.points.filter(function(p) { return _bubPtSize(p, sizeMode) >= thr; })
      : agg.points;
    // Slider'ın değer etiketini güncelle (eşiği ₺M olarak göster).
    if (prefix) {
      var vEl = document.getElementById(prefix + "-bub-minsize-val");
      if (vEl) vEl.textContent = minPct > 0
        ? ("≥ " + Math.round(thr).toLocaleString("tr-TR") + " ₺M  (" + minPct + "%)")
        : "All";
    }
    // RENK ÇAPASI: seçim-destekli prefix'lerde renk skalası her zaman
    // BÖLÜNMEMİŞ (split'siz) agregasyonun simetrik [-M, +M] aralığına sabitlenir
    // → bir bubble böl/birleştir yapıldığında dokunulmayan bubble'ların rengi
    // değişmez (kullanıcı isteği). Split yoksa taban = mevcut küme (davranış aynı).
    var colorMaxBal, colorMaxRate;
    if (prefix && _BUB_SELECT_PREFIXES[prefix]) {
      var basePts = (splitMap && Object.keys(splitMap).length)
        ? _aggregateBubbles(srcBalFig, state, merges, prodDims, activeDims, null).points
        : agg.points;
      var _anyOS = basePts.some(function(p) { return p.osb0_m != null || p.osb1_m != null; });
      var mB = 0, mR = 0;
      basePts.forEach(function(p) {
        var d = _anyOS ? ((p.osb1_m || 0) - (p.osb0_m || 0)) : (p.b1_m - p.b0_m);
        var r = (p.r1 - p.r0) * 10000.0;
        if (Math.abs(d) > mB) mB = Math.abs(d);
        if (Math.abs(r) > mR) mR = Math.abs(r);
      });
      colorMaxBal = mB || undefined;
      colorMaxRate = mR || undefined;
    }
    // HAREKET KİLİDİ (tarih slider'ı play/drag): renk çapası tüm adımların
    // union max'ına sabitlenir → oynatma sırasında renkler adım adım yeniden
    // ölçeklenmez (eksen sabitleme aşağıdaki eksen bloğunda).
    var mo = (prefix && _bubMotion[prefix]) || null;
    if (mo) {
      if (mo.colorMaxBal)  colorMaxBal  = mo.colorMaxBal;
      if (mo.colorMaxRate) colorMaxRate = mo.colorMaxRate;
    }
    var balFig  = _buildBalFig(visPoints,  (srcBalFig  && srcBalFig.layout)  || null, sizeMode, colorMaxBal);
    var rateFig = _buildRateFig(visPoints, (srcRateFig && srcRateFig.layout) || null, sizeMode, colorMaxRate);
    // Ekranda GÖSTERİLEN (filtre/merge sonrası) ürünlerin ağırlıklı ortalama
    // bitiş faizi = Σ(b1 × r1)/Σ(b1). Her iki grafiğin Y ekseni de "bitiş faizi
    // (%)" olduğundan bu değeri Y'yi kesen kesikli yatay çizgi olarak ekleriz.
    // Filtre değişince _renderBubbles yeniden çağrılır → çizgi seviyesi güncellenir.
    var _wsum = 0, _bsum = 0;
    agg.points.forEach(function(p) {
      var w = (p.b1_m != null && p.b1_m > 0) ? p.b1_m : 0;
      if (!w) return;
      _wsum += w * (p.r1 * 100); _bsum += w;
    });
    var _wavg = _bsum > 0 ? (_wsum / _bsum) : null;
    // Etiket ekleri (kullanıcı isteği): iki dönem arası WAvg değişimi (bps) +
    // son dönem toplam bakiye. Başlangıç WAvg'ı aynı nokta kümesinden b0
    // ağırlıklarıyla hesaplanır; bakiye OS modunda (NP bubble) outstanding
    // toplamıdır — hover'daki "Balance" ile aynı anlam.
    var _wsum0 = 0, _bsum0 = 0;
    agg.points.forEach(function(p) {
      var w0 = (p.b0_m != null && p.b0_m > 0) ? p.b0_m : 0;
      if (!w0) return;
      _wsum0 += w0 * (p.r0 * 100); _bsum0 += w0;
    });
    var _wavg0 = _bsum0 > 0 ? (_wsum0 / _bsum0) : null;
    var _dBps = (_wavg != null && _wavg0 != null) ? Math.round((_wavg - _wavg0) * 100) : null;
    var _anyOSb = agg.points.some(function(p) { return p.osb1_m != null; });
    var _balSum = _anyOSb
      ? agg.points.reduce(function(s, p) { return s + (p.osb1_m || 0); }, 0)
      : _bsum;
    // balFig.layout / rateFig.layout PAYLAŞILAN kaynak layout objesine referanstır
    // (dddFigs.bubble_balance.layout gibi). Doğrudan concat edersek her filtre
    // değişiminde çizgi BİRİKİR. Bu yüzden layout'u shallow-clone edip önceki
    // WAvg çizgi/etiketini (_wavgLine/_wavgAnn işaretli) filtreleyip tek tane bırakırız.
    [balFig, rateFig].forEach(function(f, fi) {
      var lay = Object.assign({}, f.layout || {});
      // EKSEN GÜVENCESİ: filtre/merge/split ile nokta kümesi her değiştiğinde
      // eksenler veriye YENİDEN otururmalı. Axis objeleri taze klonlanır, varsa
      // donmuş range silinir ve autorange açık zorlanır (Plotly'nin layout'a
      // geri yazdığı range bir sonraki render'a sızamaz; backend sabit range
      // gönderse bile ezilir) → eksen dışı bubble / gereksiz zoom-out kalmaz.
      // TEK İSTİSNA hareket kilidi: play/drag boyunca eksenler tüm adımların
      // union aralığına SABİT kalır (bubble'lar akarken eksen zıplamaz).
      var moAx = mo && mo.ax && (fi === 0 ? mo.ax.bal : mo.ax.rate);
      ["xaxis", "yaxis"].forEach(function(axk) {
        var a = Object.assign({}, lay[axk] || {});
        delete a.range;
        a.autorange = true;
        var rg = moAx && (axk === "xaxis" ? moAx.x : moAx.y);
        if (rg) { a.range = rg.slice(); a.autorange = false; }
        lay[axk] = a;
      });
      // Play adımlarında bubble'lar yeni konum/boyuta yumuşak geçsin.
      if (mo && mo.trans) lay.transition = { duration: mo.trans, easing: "cubic-in-out" };
      lay.shapes = (lay.shapes || []).filter(function(s) { return !s._wavgLine; });
      lay.annotations = (lay.annotations || []).filter(function(a) { return !a._wavgAnn; });
      if (_wavg != null) {
        lay.shapes = lay.shapes.concat([{
          _wavgLine: true,
          type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: _wavg, y1: _wavg,
          line: { color: "#D4A574", width: 1.5, dash: "dash" }, layer: "below",
        }]);
        var _annTxt = "WAvg: " + _wavg.toFixed(2) + "%";
        if (_dBps != null)
          _annTxt += "<br>Delta Interest Rate: " + (_dBps > 0 ? "+" : "") + _dBps + " bps";
        _annTxt += "<br>Balance: "
          + (_balSum / 1000).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })
          + " billion TRY";
        lay.annotations = lay.annotations.concat([{
          _wavgAnn: true,
          xref: "paper", x: 1, xanchor: "right", yref: "y", y: _wavg, yanchor: "bottom",
          text: _annTxt, align: "right", showarrow: false,
          font: { size: 11, color: "#D4A574" }, bgcolor: "rgba(0,0,0,0)",
        }]);
      }
      f.layout = lay;
    });
    // Seçim vurgusu (yalnız seçim destekli prefix'lerde ve seçim varken).
    if (prefix && _BUB_SELECT_PREFIXES[prefix] && _bubSel[prefix]) {
      _applyBubSel(balFig,  _bubSel[prefix], 3);   // bal customdata[3] = name
      _applyBubSel(rateFig, _bubSel[prefix], 2);   // rate customdata[2] = name
    }
    renderPlotlyFig(balId,  balFig,  380);
    renderPlotlyFig(rateId, rateFig, 380);
  }

  function _toggleBubLabels(balId, rateId, show) {
    var elBal  = document.getElementById(balId);
    var elRate = document.getElementById(rateId);
    if (!show) {
      // Smart mode: product name only, hide labels on densely overlapping bubbles
      _smartBubbleLabels(balId, "bal");
      _smartBubbleLabels(rateId, "rate");
      return;
    }
    // Detailed mode: every bubble gets a label with Δ value
    if (elBal && elBal.data && elBal.data.length) {
      var d = elBal.data[0];
      var newText = (d.customdata || []).map(function(cd, i) {
        var name = (cd && cd[3]) || (d.text && d.text[i]) || "";
        var dv = d.x[i];
        if (dv == null || isNaN(dv)) return name;
        var sign = dv >= 0 ? "+" : "";
        return name + "<br>Δ " + sign + Math.round(dv).toLocaleString("tr-TR") + " ₺M";
      });
      Plotly.restyle(elBal, { mode: "markers+text", text: [newText], textposition: "top center" }, [0]);
    }
    if (elRate && elRate.data && elRate.data.length) {
      var d2 = elRate.data[0];
      var newText2 = (d2.customdata || []).map(function(cd, i) {
        var name = (cd && cd[2]) || (d2.text && d2.text[i]) || "";
        var rv = d2.x[i];
        if (rv == null || isNaN(rv)) return name;
        var sign = rv >= 0 ? "+" : "";
        return name + "<br>" + sign + Math.round(rv) + " bps";
      });
      Plotly.restyle(elRate, { mode: "markers+text", text: [newText2], textposition: "top center" }, [0]);
    }
  }

  // Greedy density-aware label visibility: walk bubbles largest-first; a bubble
  // gets its product-key label only if no already-labeled bubble overlaps it
  // (centre-distance < r1 + r2 + label padding). Densely packed clusters keep
  // only the dominant bubble's label; sparse regions label everything.
  // Density-aware label visibility without relying on Plotly private APIs.
  // Converts data coords to approximate pixel space using element dimensions
  // and data range, then greedily labels largest bubbles that don't overlap.
  function _smartBubbleLabels(elId, kind) {
    var el = document.getElementById(elId);
    if (!el || !el.data || !el.data.length) return;
    var trace = el.data[0];
    var xs = trace.x || [], ys = trace.y || [];
    var sizes   = (trace.marker && trace.marker.size) || [];
    var sizeref = (trace.marker && trace.marker.sizeref) || 1;
    var sizemin = (trace.marker && trace.marker.sizemin) || 4;
    var cd = trace.customdata || [];
    var n = xs.length;
    if (!n) return;
    var nameIdx = (kind === "rate") ? 2 : 3;

    // Data range (fallback to actual min/max from trace)
    var validXs = [], validYs = [];
    for (var i = 0; i < n; i++) {
      if (xs[i] != null && isFinite(xs[i])) validXs.push(xs[i]);
      if (ys[i] != null && isFinite(ys[i])) validYs.push(ys[i]);
    }
    if (!validXs.length) return;
    var xMin = Math.min.apply(null, validXs), xMax = Math.max.apply(null, validXs);
    var yMin = Math.min.apply(null, validYs), yMax = Math.max.apply(null, validYs);
    var xSpan = (xMax - xMin) || 1, ySpan = (yMax - yMin) || 1;

    // Approximate plot-area pixel size (subtract estimated margins)
    var pw = Math.max((el.offsetWidth  || 600) - 90, 100);
    var ph = Math.max((el.offsetHeight || 380) - 90, 100);

    // Map each point to pixel space and compute pixel radius from marker area
    var px = new Array(n), py = new Array(n), pr = new Array(n);
    for (var j = 0; j < n; j++) {
      if (xs[j] == null || !isFinite(xs[j])) { px[j] = py[j] = pr[j] = NaN; continue; }
      px[j] = ((xs[j] - xMin) / xSpan) * pw;
      py[j] = ((ys[j] - yMin) / ySpan) * ph;
      var areaPx = Math.max(0, sizes[j] || 0) / sizeref;
      pr[j] = Math.max(sizemin / 2, Math.sqrt(areaPx / Math.PI));
    }

    // Greedy: label largest bubbles first; skip any that overlap a chosen one
    var order = [];
    for (var k = 0; k < n; k++) if (!isNaN(pr[k])) order.push(k);
    order.sort(function(a, b) { return pr[b] - pr[a]; });

    var labeled = new Array(n).fill(false), chosen = [];
    var AVG_LABEL_W = 70;   // px — typical product-key label width at 11px
    var LABEL_H     = 14;   // px — single line label height
    var V_PAD       = 8;    // extra vertical clearance under label
    order.forEach(function(i) {
      var clash = false;
      for (var c = 0; c < chosen.length; c++) {
        var jj = chosen[c];
        var dx = Math.abs(px[i] - px[jj]);
        var dy = Math.abs(py[i] - py[jj]);
        // axis-aware: labels stack horizontally so require full text width
        // clearance on X, but only marker radius + label height on Y.
        var minDx = pr[i] + pr[jj] + AVG_LABEL_W;
        var minDy = pr[i] + pr[jj] + LABEL_H + V_PAD;
        if (dx < minDx && dy < minDy) { clash = true; break; }
      }
      if (!clash) { labeled[i] = true; chosen.push(i); }
    });

    var baseNames = new Array(n);
    for (var m = 0; m < n; m++) {
      baseNames[m] = (cd[m] && cd[m][nameIdx]) || (trace.text && trace.text[m]) || "";
    }
    var newText = baseNames.map(function(t, i) { return labeled[i] ? t : ""; });
    Plotly.restyle(el, { mode: "markers+text", text: [newText], textposition: "top center" }, [0]);
  }

  // ── Deposit Detail functions ────────────────────────────────────────────────

  function renderDdSlide(idx) {
    ddSlide = idx;
    var key = DD_SLIDES[idx];
    // Remove any stale drill-down panel from a previous product selection
    var stale = document.getElementById("dd-drill-row");
    if (stale) stale.remove();
    destroyChart("dd-wf-main");
    destroyChart("dd-wf3");
    destroyChart("dd-wf2-bg");
    // DD sources monthly data; drill-down asks backend to align d0 to month
    // start and d1 to the last DAT-in-data inside d1's month.
    function _ddDrill(product, anchorEl) {
      var d0 = elDate0 ? elDate0.value : "";
      var d1 = elDate1 ? elDate1.value : "";
      var dims = DD_DIMS.filter(function(d) { return ddDims[d]; });
      _showDepositDrillDown("dd-drill-row", product, d0, d1, dims, anchorEl,
                            { align: "monthly" });
    }
    if (ddFigs && ddFigs[key]) {
      var wfOpts = { height: 420 };
      // Pricing Drivers (idx=1) OR Mix Drivers (idx=2) waterfalls — main chart
      if (idx === 1 || idx === 2) {
        wfOpts.onRelativeBarDblClick = function(bar) {
          _ddDrill(bar.x, document.getElementById("dd-wf-main"));
        };
      }
      renderChart("dd-wf-main", ddFigs[key], wfOpts);
    }
    // Slide 2 (Pricing Drivers) — balance growth companion
    var comp2 = document.getElementById("dd-wf2-companion");
    if (idx === 1 && ddFigs && ddFigs.wf2_bg) {
      if (comp2) comp2.classList.remove("hidden");
      renderChart("dd-wf2-bg", ddFigs.wf2_bg, {
        height: 300,
        onBarDblClick: function(cat) { _ddDrill(cat, document.getElementById("dd-wf2-bg")); },
      });
    } else {
      if (comp2) comp2.classList.add("hidden");
    }
    // Slide 3 (Mix Drivers) — weight change companion
    var companion = document.getElementById("dd-wf-companion");
    if (idx === 2 && ddFigs && ddFigs.wf3) {
      if (companion) companion.classList.remove("hidden");
      renderChart("dd-wf3", ddFigs.wf3, {
        height: 320,
        onBarDblClick: function(cat) { _ddDrill(cat, document.getElementById("dd-wf3")); },
      });
    } else {
      if (companion) companion.classList.add("hidden");
    }
    var btnPrev = document.getElementById("dd-prev");
    var btnNext = document.getElementById("dd-next");
    if (btnPrev) btnPrev.disabled = idx === 0;
    if (btnNext) btnNext.disabled = idx === DD_SLIDES.length - 1;
    var lbl = document.getElementById("dd-slide-label");
    if (lbl) lbl.textContent = (idx + 1) + " / " + DD_SLIDES.length;
  }

  async function fetchDepositDetailWaterfalls() {
    var d0 = elDate0.value, d1 = elDate1.value;
    if (!d0 || !d1 || d0 === d1) return;
    var dims = DD_DIMS.filter(function(d) { return ddDims[d]; });
    try {
      var r = await fetch("/api/deposit_detail_waterfalls?date_0=" + encodeURIComponent(d0) +
                          "&date_1=" + encodeURIComponent(d1) +
                          "&dims=" + encodeURIComponent(dims.join(",")));
      var data = await r.json();
      if (!data.ok) return;
      ddFigs  = data.figs || {};
      ddSlide = 0;
      // Open the accordion if it's closed
      if (!ddWfOpen) {
        ddWfOpen = true;
        var btn  = document.getElementById("acc-btn-dd-wf");
        var body = document.getElementById("acc-body-dd-wf");
        if (btn)  btn.classList.add("open");
        if (body) { body.style.maxHeight = "none"; body.style.overflow = "visible"; }
      }
      renderDdSlide(0);
      // Update bubble filter state from response
      ddBubMeta   = (ddFigs && ddFigs.bubble_filter_meta)  || {};
      ddBubPdims  = (ddFigs && ddFigs.bubble_product_dims) || {};
      ddBubFilter = {};
      ddBubMerges = {};
      ddBubAggMembers = {};
      _renderBubFilters("dd-bub-filters", ddBubMeta, ddBubFilter, ddBubMerges, function() {
        _renderDdBubbles();
      });
      requestAnimationFrame(function() { _renderDdBubbles(); });
    } catch(e) { /* silent */ }
  }

  function _renderDdBubbles() {
    if (!ddFigs || !ddFigs.bubble_balance) return;
    // activeDims — bubble kaynağı ürün×vade ince olduğundan ürün seviyesine geri
    // toplanması için aktif ekran boyutları verilir (dd'de TENOR chip'i YOK; bkz.
    // _renderCaMonBubbles). MATURITY_BUCKET grup dışı → eski görünümle birebir.
    _renderBubbles("dd-bub-bal", "dd-bub-rate",
                   ddFigs.bubble_balance, ddFigs.bubble_rate,
                   ddBubFilter, ddBubMerges, ddBubPdims, ddBubAggMembers, "dd",
                   DD_DIMS.filter(function(d) { return ddDims[d]; }));
    requestAnimationFrame(function() {
      _toggleBubLabels("dd-bub-bal", "dd-bub-rate", false);
      _attachDepositBubbleDrill("dd-bub-bal", "dd-bubble-drill", function() {
        return { d0: elDate0 ? elDate0.value : "",
                 d1: elDate1 ? elDate1.value : "",
                 dims: DD_DIMS.filter(function(d) { return ddDims[d]; }),
                 align: "monthly",
                 aggMembers: ddBubAggMembers };
      });
      _attachDepositBubbleDrill("dd-bub-rate", "dd-bubble-drill", function() {
        return { d0: elDate0 ? elDate0.value : "",
                 d1: elDate1 ? elDate1.value : "",
                 dims: DD_DIMS.filter(function(d) { return ddDims[d]; }),
                 align: "monthly",
                 aggMembers: ddBubAggMembers };
      });
    });
  }

  // ── Cost Analysis > Monthly Averages functions ─────────────────────────────
  // Mirrors Deposit Detail but reads from independent date dropdowns and
  // paints into its own DOM IDs (ca-mon-*).
  function renderCaMonSlide(idx) {
    caMonSlide = idx;
    var key = CA_MON_SLIDES[idx];
    var body = document.getElementById("acc-body-ca-mon-wf");
    if (body) { body.style.maxHeight = "none"; body.style.overflow = "visible"; }
    var stale = document.getElementById("ca-mon-drill-row");
    if (stale) stale.remove();
    destroyChart("ca-mon-wf-main");
    destroyChart("ca-mon-wf3");
    destroyChart("ca-mon-wf2-bg");
    // CA Mon sources monthly data; backend aligns d0 to month start and d1 to
    // the last DAT-in-data inside d1's month.
    function _caMonDrill(product, anchorEl) {
      var sel0 = document.getElementById("ca-mon-date0");
      var sel1 = document.getElementById("ca-mon-date1");
      var d0 = sel0 ? sel0.value : "";
      var d1 = sel1 ? sel1.value : "";
      var dims = DD_DIMS.filter(function(d) { return caMonDims[d]; });
      var bd = (document.getElementById("ca-mon-break-dim") || {}).value || "PRODUCT";
      _showDepositDrillDown("ca-mon-drill-row", product, d0, d1, dims, anchorEl,
                            { align: "monthly", breakDim: bd, source: "monthly", prefix: "ca-mon" });
    }
    if (caMonFigs && caMonFigs[key]) {
      var wfOpts = { height: 420 };
      if (idx === 1 || idx === 2) {
        wfOpts.onRelativeBarDblClick = function(bar) {
          _caMonDrill(bar.x, document.getElementById("ca-mon-wf-main"));
        };
      }
      renderChart("ca-mon-wf-main", caMonFigs[key], wfOpts);
    }
    var comp2 = document.getElementById("ca-mon-wf2-companion");
    if (idx === 1 && caMonFigs && caMonFigs.wf2_bg) {
      if (comp2) comp2.classList.remove("hidden");
      requestAnimationFrame(function() {
        renderChart("ca-mon-wf2-bg", caMonFigs.wf2_bg, {
          height: 300,
          onBarDblClick: function(cat) { _caMonDrill(cat, document.getElementById("ca-mon-wf2-bg")); },
        });
      });
    } else {
      if (comp2) comp2.classList.add("hidden");
    }
    var companion = document.getElementById("ca-mon-wf-companion");
    if (idx === 2 && caMonFigs && caMonFigs.wf3) {
      if (companion) companion.classList.remove("hidden");
      requestAnimationFrame(function() {
        renderChart("ca-mon-wf3", caMonFigs.wf3, {
          height: 320,
          onBarDblClick: function(cat) { _caMonDrill(cat, document.getElementById("ca-mon-wf3")); },
        });
      });
    } else {
      if (companion) companion.classList.add("hidden");
    }
    var btnPrev = document.getElementById("ca-mon-prev");
    var btnNext = document.getElementById("ca-mon-next");
    if (btnPrev) btnPrev.disabled = idx === 0;
    if (btnNext) btnNext.disabled = idx === CA_MON_SLIDES.length - 1;
    var lbl = document.getElementById("ca-mon-slide-label");
    if (lbl) lbl.textContent = (idx + 1) + " / " + CA_MON_SLIDES.length;
  }

  async function fetchCaMonWaterfalls() {
    var sel0 = document.getElementById("ca-mon-date0");
    var sel1 = document.getElementById("ca-mon-date1");
    if (!sel0 || !sel1) return;
    var d0 = sel0.value, d1 = sel1.value;
    if (!d0 || !d1 || d0 === d1) return;
    var dims = DD_DIMS.filter(function(d) { return caMonDims[d]; });
    try {
      // tenor_filter=1 → backend bubble_filter_meta'ya MATURITY_BUCKET (TENOR) ekler.
      // Legacy Deposit Detail (dd) sekmesi aynı endpoint'i bu param OLMADAN çağırır →
      // oraya TENOR filtresi sızmaz (yalnız Outstanding Cost Analysis'e eklenir).
      var r = await fetch("/api/deposit_detail_waterfalls?date_0=" + encodeURIComponent(d0) +
                          "&date_1=" + encodeURIComponent(d1) +
                          "&dims=" + encodeURIComponent(dims.join(",")) +
                          "&tenor_filter=1" + _rateConvQS("ca-mon") + _bscDemandQS());
      var data = await r.json();
      if (!data.ok) return;
      caMonFigs  = data.figs || {};
      caMonSlide = 0;
      if (!caMonWfOpen) {
        caMonWfOpen = true;
        var btn  = document.getElementById("acc-btn-ca-mon-wf");
        var body = document.getElementById("acc-body-ca-mon-wf");
        if (btn)  btn.classList.add("open");
        if (body) { body.style.maxHeight = "none"; body.style.overflow = "visible"; }
      }
      renderCaMonSlide(0);
      caMonBubMeta   = (caMonFigs && caMonFigs.bubble_filter_meta)  || {};
      caMonBubPdims  = (caMonFigs && caMonFigs.bubble_product_dims) || {};
      caMonBubFilter = {};
      // caMonBubMerges SIFIRLANMAZ — gruplama ortak hafızada (sharedDimMerges)
      // yaşar, sayfalar arası geçişte ve refetch'te korunur.
      caMonBubAggMembers = {};
      _bubSplit["ca-mon"] = {};   // yeni veri → per-bubble kırılım + seçim sıfırlanır
      _bubSel["ca-mon"] = null;
      // Tam-ekran SUBPRODUCT chip'i açıksa taze state objelerine yeniden bağla
      // (refetch caMonBubFilter'ı yeniledi; eski closure ölü kalırdı).
      _renderFsSubprodChip("ca-mon");
      // Tam-ekran tarih slider'ı açıksa seriyi tazele (Date(Start)/rate_conv
      // değişmiş olabilir; anahtar aynıysa yalnız konum senkronu — ağ yok).
      _tlInvalidate("ca-mon");
      _renderBubFilters("ca-mon-bub-filters", caMonBubMeta, caMonBubFilter, caMonBubMerges, function() {
        _renderCaMonBubbles();
        _fetchCaRateHeatmap("ca-mon");   // filtre değişimi rate heatmap'e de uygulanır
      });
      requestAnimationFrame(function() { _renderCaMonBubbles(); });
      // Rate heatmap (Decomposition Dim'e göre)
      _renderCaRateFromState("ca-mon", caMonFigs, d0, d1);
    } catch(e) {
      // Sessiz yutma teşhisi imkansızlaştırıyordu ("neden render olmadı") —
      // kullanıcıya sızdırmadan console'a yaz.
      console.warn("fetchCaMonWaterfalls:", e);
    }
  }

  function _renderCaMonBubbles() {
    if (!caMonFigs || !caMonFigs.bubble_balance) return;
    // activeDims = aktif ekran boyutları (MATURITY_BUCKET HARİÇ). Bubble kaynağı artık
    // ürün×vade ince → gruplama yalnız bu boyutlarda (ürün seviyesine geri toplanır),
    // TENOR filtresi ise tüm boyutlarda uygulanır (bkz. _aggregateBubbles).
    _renderBubbles("ca-mon-bub-bal", "ca-mon-bub-rate",
                   caMonFigs.bubble_balance, caMonFigs.bubble_rate,
                   caMonBubFilter, caMonBubMerges, caMonBubPdims, caMonBubAggMembers, "ca-mon",
                   DD_DIMS.filter(function(d) { return caMonDims[d]; }));
    requestAnimationFrame(function() {
      _toggleBubLabels("ca-mon-bub-bal", "ca-mon-bub-rate", false);
      var _caMonBubCtx = function() {
        var sel0 = document.getElementById("ca-mon-date0");
        var sel1 = document.getElementById("ca-mon-date1");
        return { d0: sel0 ? sel0.value : "",
                 d1: sel1 ? sel1.value : "",
                 dims: DD_DIMS.filter(function(d) { return caMonDims[d]; }),
                 align: "monthly",
                 aggMembers: caMonBubAggMembers,
                 breakDim: (document.getElementById("ca-mon-break-dim") || {}).value || "PRODUCT",
                 source: "monthly", prefix: "ca-mon" };
      };
      _attachDepositBubbleDrill("ca-mon-bub-bal",  "ca-mon-bubble-drill", _caMonBubCtx);
      _attachDepositBubbleDrill("ca-mon-bub-rate", "ca-mon-bubble-drill", function() {
        return _caMonBubCtx();
      });
    });
  }

  async function ensureCaMonDatesLoaded() {
    if (caMonDatesLoaded) return;
    try {
      var r = await fetch("/api/deposit_detail_dates");
      var data = await r.json();
      if (!data.ok) return;
      var dates = data.dates || [];
      var sel0 = document.getElementById("ca-mon-date0");
      var sel1 = document.getElementById("ca-mon-date1");
      if (!sel0 || !sel1) return;
      sel0.innerHTML = ""; sel1.innerHTML = "";
      dates.forEach(function(d) {
        sel0.appendChild(new Option(d, d));
        sel1.appendChild(new Option(d, d));
      });
      if (dates.length >= 2) {
        sel0.value = dates[dates.length - 2];
        sel1.value = dates[dates.length - 1];
      } else if (dates.length === 1) {
        sel0.value = dates[0]; sel1.value = dates[0];
      }
      caMonDatesLoaded = true;
    } catch(e) { /* silent */ }
  }

  // ── Tenor Analysis functions ──────────────────────────────────────────────

  // Convert bubble-style state to filter query params for the backend.
  // state = { DIM: { val: true/false } }, merges = { DIM: [{name, members}] }
  // Returns "&filter_DIM=v1|v2&..."
  function _tenorBubStateToQuery(state, merges) {
    var parts = [];
    var activeMerges = {};
    Object.keys(state).forEach(function(dim) {
      var dimState  = state[dim]  || {};
      var dimMerges = (merges && merges[dim]) || [];
      // Collect effectively-visible raw values = checked + members-of-checked-groups
      var allowed = [];
      var activeForDim = [];
      Object.keys(dimState).forEach(function(v) {
        if (dimState[v] === false) return;
        // Is v a group name?
        var grp = dimMerges.find(function(g) { return g.name === v; });
        if (grp) {
          grp.members.forEach(function(m) { allowed.push(m); });
          activeForDim.push({ name: grp.name, members: grp.members.slice() });
        } else {
          allowed.push(v);
        }
      });
      if (allowed.length > 0) {
        // If all raw values for this dim are allowed, skip (= no constraint)
        var allVals = taFilterMeta[dim] || [];
        var missing = allVals.some(function(v) { return !allowed.includes(v); });
        if (missing) {
          parts.push("filter_" + dim + "=" + encodeURIComponent(allowed.join("|")));
        }
      }
      // Gruplama (merge) → backend'e taşı ki grafiklerde birleşik bucket görünsün.
      if (activeForDim.length) activeMerges[dim] = activeForDim;
    });
    if (Object.keys(activeMerges).length) {
      parts.push("merges=" + encodeURIComponent(JSON.stringify(activeMerges)));
    }
    return parts.length ? "&" + parts.join("&") : "";
  }

  // Reset Daily Evolution date inputs to defaults (To=max, From=prev Thursday).
  // Used by tab open handlers so opening the tab always shows latest dates.
  function _setDailyDefaultDates(prefix, datesArr) {
    if (!datesArr || datesArr.length === 0) return;
    var inp0 = document.getElementById(prefix + "-date0");
    var inp1 = document.getElementById(prefix + "-date1");
    if (!inp0 || !inp1) return;
    var maxD = datesArr[datesArr.length - 1];
    inp1.value = maxD;
    inp0.value = _prevThursday(datesArr, maxD);
  }

  // Calendar prev-Thursday: endDate'ten ÖNCEKİ takvim Perşembe'si.
  // `dates` parametresi geriye dönük uyumluluk için tutuluyor ama
  // kullanılmıyor — backend, listede olmasa bile bu tarihi kabul ediyor.
  // Eski davranış (dates listesinde Perşembe arama) bazen geçerli tarihleri
  // kaçırıyordu (ör. 18/06/2026 listede yok → bir önceki Perşembe'ye geçiyor
  // veya en başa düşüyordu).
  function _prevThursday(dates, endDate) {
    if (!endDate) return endDate;
    var dt = new Date(endDate + "T00:00:00Z");
    if (isNaN(dt)) return endDate;
    var day = dt.getUTCDay();           // 0=Sun..6=Sat, 4=Thu
    var diff = (day - 4 + 7) % 7;
    if (diff === 0) diff = 7;            // endDate Perşembe ise 7 gün öncesi
    dt.setUTCDate(dt.getUTCDate() - diff);
    return dt.toISOString().slice(0, 10);
  }

  async function ensureTenorDatesLoaded() {
    if (taDatesLoaded) return;
    try {
      var r = await fetch("/api/tenor_dates");
      var data = await r.json();
      if (!data.ok) return;
      taMonthlyDates = data.monthly_dates || [];
      taDailyDates   = data.daily_dates   || [];
      taDailyDateSet = new Set(taDailyDates);
      taFilterMeta   = data.filter_meta   || {};
      // Populate monthly dropdowns
      var sel0 = document.getElementById("ta-mon-date0");
      var sel1 = document.getElementById("ta-mon-date1");
      if (sel0 && sel1) {
        sel0.innerHTML = ""; sel1.innerHTML = "";
        taMonthlyDates.forEach(function(d) {
          sel0.appendChild(new Option(d, d));
          sel1.appendChild(new Option(d, d));
        });
        if (taMonthlyDates.length >= 2) {
          sel0.value = taMonthlyDates[taMonthlyDates.length - 2];
          sel1.value = taMonthlyDates[taMonthlyDates.length - 1];
        }
      }
      // Populate daily date inputs
      var inp0 = document.getElementById("ta-dly-date0");
      var inp1 = document.getElementById("ta-dly-date1");
      var hint = document.getElementById("ta-dly-date-hint");
      if (inp0 && inp1 && taDailyDates.length > 0) {
        var minD = taDailyDates[0], maxD = taDailyDates[taDailyDates.length - 1];
        inp0.min = minD; inp0.max = maxD;
        inp1.min = minD; inp1.max = maxD;
        // Default start = most recent Thursday strictly before maxD
        inp0.value = _prevThursday(taDailyDates, maxD);
        inp1.value = maxD;
        if (hint) hint.textContent = "(" + minD + " — " + maxD + ")";
      }
      // Render bubble-style filter panels (include MATURITY_BUCKET)
      // Re-use _renderBubFilters — the same function as Cost Analysis bubbles
      _renderTaFilterPanels();
      taDatesLoaded = true;
    } catch(e) { /* silent */ }
  }

  // Panel render'ı ayrı fonksiyonda: sayfaya her girişte yeniden çizilir ki
  // başka sayfada oluşturulan (paylaşılan/ayna) gruplar görünür olsun.
  function _renderTaFilterPanels() {
    if (!taFilterMeta || !Object.keys(taFilterMeta).length) return;
    _renderBubFilters("ta-mon-filters", taFilterMeta, taMonBubState, taMonBubMerges,
      function() { fetchTenorMonthly(); });
    _renderBubFilters("ta-dly-filters", taFilterMeta, taDlyBubState, taDlyBubMerges,
      function() { fetchTenorDaily(); });
  }

  // ── TENOR ↔ DTM mod anahtarı ────────────────────────────────────────────────
  // Tenor Analysis'teki TÜM grafikler bu moda göre çalışır: TENOR = orijinal
  // vade (MATURITY_*), DTM = vadeye kalan gün (REMAINING_MTRTY_* / DTM).
  var taTenorMode = "tenor";   // "tenor" | "dtm"

  function _setTaTenorMode(mode) {
    if (mode !== "tenor" && mode !== "dtm") return;
    if (taTenorMode === mode) return;
    taTenorMode = mode;
    ["ta-mon-mode-switch", "ta-dly-mode-switch"].forEach(function(id) {
      var sw = document.getElementById(id);
      if (!sw) return;
      sw.classList.toggle("is-right", mode === "dtm");
      sw.querySelectorAll(".hm-lbl").forEach(function(l) {
        l.classList.toggle("active", l.dataset.mode === mode);
      });
    });
    _syncTaModeLabels();
    if (taTab === "monthly-averages") fetchTenorMonthly();
    else fetchTenorDaily();
  }

  // KPI kart etiketlerini aktif moda göre güncelle (etiket div'i, değer div'inin
  // hemen önündeki kardeş elemandır).
  function _syncTaModeLabels() {
    var w = (taTenorMode === "dtm") ? "DTM" : "Tenor";
    var t0m = document.getElementById("ta-mon-wat-t0");
    var t1m = document.getElementById("ta-mon-wat-t1");
    var dlm = document.getElementById("ta-mon-wat-delta");
    if (t0m && t0m.previousElementSibling) t0m.previousElementSibling.innerHTML = "Weighted Avg " + w + " (t<sub>0</sub>)";
    if (t1m && t1m.previousElementSibling) t1m.previousElementSibling.innerHTML = "Weighted Avg " + w + " (t<sub>1</sub>)";
    if (dlm && dlm.previousElementSibling) dlm.previousElementSibling.textContent = "Δ " + w;
    var wShort = (taTenorMode === "dtm") ? "WDTM" : "WAT";
    var t0d = document.getElementById("ta-dly-wat-t0");
    var t1d = document.getElementById("ta-dly-wat-t1");
    var dld = document.getElementById("ta-dly-wat-delta");
    if (t0d && t0d.previousElementSibling) t0d.previousElementSibling.innerHTML = wShort + " (t<sub>0</sub>)";
    if (t1d && t1d.previousElementSibling) t1d.previousElementSibling.innerHTML = wShort + " (t<sub>1</sub>)";
    if (dld && dld.previousElementSibling) dld.previousElementSibling.textContent = "Δ " + w;
  }

  async function fetchTenorMonthly() {
    var sel0 = document.getElementById("ta-mon-date0");
    var sel1 = document.getElementById("ta-mon-date1");
    if (!sel0 || !sel1) return;
    var d0 = sel0.value, d1 = sel1.value;
    if (!d0 || !d1 || d0 === d1) return;
    try {
      var url = "/api/tenor_monthly?date_0=" + encodeURIComponent(d0)
              + "&date_1=" + encodeURIComponent(d1)
              + "&mode=" + encodeURIComponent(taTenorMode)
              + _tenorBubStateToQuery(taMonBubState, taMonBubMerges);
      var r = await fetch(url);
      var data = await r.json();
      if (!data.ok) return;
      taMonPayload = data;
      taMonWfSlide = 0;
      _renderTenorSnapshot(data, "ta-mon", false, d0, d1);
    } catch(e) { /* silent */ }
  }

  async function fetchTenorDaily() {
    var inp0 = document.getElementById("ta-dly-date0");
    var inp1 = document.getElementById("ta-dly-date1");
    if (!inp0 || !inp1) return;
    var d0 = inp0.value, d1 = inp1.value;
    if (!d0 || !d1 || d0 === d1) return;
    if (taDailyDateSet && taDailyDateSet.size > 0 && (!taDailyDateSet.has(d0) || !taDailyDateSet.has(d1))) {
      _showTenorDlyWarning("One of the selected dates is not in the dataset.");
      return;
    } else { _showTenorDlyWarning(""); }
    try {
      var url = "/api/tenor_daily?date_0=" + encodeURIComponent(d0)
              + "&date_1=" + encodeURIComponent(d1)
              + "&mode=" + encodeURIComponent(taTenorMode)
              + _tenorBubStateToQuery(taDlyBubState, taDlyBubMerges);
      var r = await fetch(url);
      var data = await r.json();
      if (!data.ok) return;
      taDlyPayload = data;
      taDlyWfSlide = 0;
      _renderTenorSnapshot(data, "ta-dly", true, d0, d1);
    } catch(e) { /* silent */ }
  }

  function _showTenorDlyWarning(msg) {
    var w = document.getElementById("ta-dly-warning");
    if (!w) return;
    if (!msg) { w.classList.add("hidden"); w.textContent = ""; return; }
    w.classList.remove("hidden"); w.textContent = msg;
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Balance Analysis
  // ──────────────────────────────────────────────────────────────────────────
  function _bubStateToQuery(state, merges, meta, dims) {
    // Convert {DIM: {val: bool}} + merges into &filter_DIM=v1|v2 query params,
    // plus a JSON-encoded &merges=... so the backend can remap underlying
    // raw values to the merged group name before aggregation.
    // Group names in state are expanded to their member values for filter_*.
    var parts = [];
    var activeMerges = {};
    dims.forEach(function(dim) {
      var dimState  = state[dim]  || {};
      var dimMerges = (merges && merges[dim]) || [];
      var allowed = [];
      var activeForDim = [];
      Object.keys(dimState).forEach(function(v) {
        if (dimState[v] === false) return;
        // Is v a merged group name? Expand to member raw values.
        var grp = dimMerges.find(function(g) { return g.name === v; });
        if (grp) {
          grp.members.forEach(function(m) { allowed.push(m); });
          activeForDim.push({ name: grp.name, members: grp.members.slice() });
        } else {
          allowed.push(v);
        }
      });
      if (allowed.length > 0) {
        var allVals = (meta && meta[dim]) || [];
        var missing = allVals.some(function(v) { return !allowed.includes(v); });
        if (missing) {
          parts.push("filter_" + dim + "=" + encodeURIComponent(allowed.join("|")));
        }
      }
      if (activeForDim.length) activeMerges[dim] = activeForDim;
    });
    if (Object.keys(activeMerges).length) {
      parts.push("merges=" + encodeURIComponent(JSON.stringify(activeMerges)));
    }
    return parts.length ? "&" + parts.join("&") : "";
  }

  function _balanceBubStateToQuery(state, merges) {
    return _bubStateToQuery(state, merges, baFilterMeta, BA_DIMS);
  }

  // Cost Analysis bubble filter panel state → query string (rate heatmap +
  // rate drill'ler aynı filtrelerle çalışsın diye).
  // Rate Type seçicisi (Outstanding Cost): simple | on | compound.
  function _rateConvOf(prefix) {
    var el = document.getElementById(prefix + "-rate-conv");
    return (el && el.value) || "simple";
  }
  function _rateConvQS(prefix) {
    var rc = _rateConvOf(prefix);
    return (rc && rc !== "simple") ? "&rate_conv=" + encodeURIComponent(rc) : "";
  }

  // VADESİZ (demand) etkisi query'si — YALNIZ sunumda (Slide 2/4) checkbox açıkken.
  // Dashboard'daki aynı endpoint çağrılarına SIZMAZ: _bsc yoksa (sunum kapalı) ""
  // döner. KGH/BTH O/N ürünlerine sıfır-faizli vadesiz varsayımı (backend uygular).
  function _bscDemandQS() {
    if (_bsc && _bsc.demandOn && _bsc.demandPct > 0) {
      return "&demand_pct=" + encodeURIComponent(_bsc.demandPct);
    }
    return "";
  }

  function _caBubQS(prefix) {
    var isMon = (prefix === "ca-mon");
    var state  = isMon ? caMonBubFilter : dddBubFilter;
    var merges = isMon ? caMonBubMerges : dddBubMerges;
    var meta   = isMon ? caMonBubMeta   : dddBubMeta;
    // rate_conv burada taşınır → heatmap + hm_product_bar (rate drill/breakdown)
    // çağrıları sayfadaki Rate Type'a otomatik uyar.
    return _bubStateToQuery(state || {}, merges || {}, meta || {}, Object.keys(meta || {}))
           + _rateConvQS(prefix);
  }

  // ── Tam-ekran SUBPRODUCT chip'i (Cost bubble overlay'i) ─────────────────────
  // Overlay'e, sayfadaki filtre paneliyle AYNI state/merges objelerine bağlı
  // ikinci bir SUBPRODUCT chip'i render edilir → overlay'deki değişiklik hem
  // açık grafiği anında etkiler hem de sayfa filtresinin state'ini günceller.
  // Refetch state objesini YENİLEDİĞİNDE (Rate Type değişimi vb.) fetch'ler bu
  // fonksiyonu yeniden çağırıp chip'i taze objelere bağlar.
  function _costBubOnChange(prefix) {
    if (prefix === "ca-mon") { _renderCaMonBubbles(); _fetchCaRateHeatmap("ca-mon"); }
    else { _renderDddBubbles(); _fetchCaRateHeatmap("ddd"); }
  }
  function _renderFsSubprodChip(prefix) {
    var host = document.getElementById("chart-fs-subprod");
    if (!host || host.dataset.prefix !== prefix) return;
    var isMon = (prefix === "ca-mon");
    var meta = isMon ? caMonBubMeta : dddBubMeta;
    if (!meta || !meta.SUBPRODUCT) { host.innerHTML = ""; return; }
    _renderBubFilters("chart-fs-subprod", { SUBPRODUCT: meta.SUBPRODUCT },
                      isMon ? caMonBubFilter : dddBubFilter,
                      isMon ? caMonBubMerges : dddBubMerges,
                      function() { _costBubOnChange(prefix); });
  }
  // Overlay kapanınca sayfa filtre panelini tazele — chip etiketi/checkbox'ları
  // overlay'de yapılan değişiklikleri yansıtsın (state zaten aynı obje).
  function _rebuildCostBubFilterPanel(prefix) {
    var isMon = (prefix === "ca-mon");
    _renderBubFilters(isMon ? "ca-mon-bub-filters" : "ddd-bub-filters",
                      isMon ? caMonBubMeta : dddBubMeta,
                      isMon ? caMonBubFilter : dddBubFilter,
                      isMon ? caMonBubMerges : dddBubMerges,
                      function() { _costBubOnChange(prefix); });
  }

  // ── Tam-ekran TARİH SLIDER'ı + ▶ PLAY (Cost bubble overlay'i) ────────────────
  // Min-size barının altına "Tarih" slider'ı gelir: aralık Date(Start)→son mevcut
  // tarih (monthly: son ay, daily: son gün), default = sayfadaki Date(End).
  // Kaydırınca bubble'lar o tarihin verisine göre güncellenir; BIRAKINCA sayfadaki
  // Date(End) de o tarihe çekilir (tek tam refetch — commit). ▶ slider başından
  // seçili tarihe adım adım oynatır (yalnız bubble'lar güncellenir; adım süresi
  // = clamp(10sn/adım_sayısı, 0.5sn, 2sn)). Eksen/renk kuralı: HAREKET sırasında
  // (play/drag) tüm adımların union aralığı sabit; durunca mevcut tarihe auto-fit.
  // 🔒 açıksa union durunca da korunur. Veri: /api/bubble_series TEK istekle
  // prefetch edilir (adım başına ağ isteği YOK) → tempo deterministik.
  var _bubTL = null;   // { prefix, dates, cur, key, cache, playing, inMotion, timer, lock, union, els }
  function _tlDatesFor(prefix) {
    if (prefix === "ca-mon") {
      var s0 = document.getElementById("ca-mon-date0");
      var s1 = document.getElementById("ca-mon-date1");
      var d0 = s0 ? s0.value : "";
      var all = s1 ? Array.prototype.map.call(s1.options, function(o) { return o.value; }) : [];
      return { d0: d0, dates: all.filter(function(d) { return d && d > d0; }).sort() };
    }
    var i0 = document.getElementById("ddd-date0");
    var dd0 = i0 ? i0.value : "";
    return { d0: dd0,
             dates: Array.from(dddDateSet || []).sort().filter(function(d) { return d > dd0; }) };
  }
  // ≤cap tarihe indir: son tarih + sayfadaki mevcut Date(End) her zaman korunur,
  // kalanlar eşit aralıkla örneklenir (backend limiti 120).
  function _tlSample(dates, cap, keepDate) {
    var n = dates.length;
    if (n <= cap) return dates.slice();
    var pick = {};
    pick[dates[n - 1]] = 1;
    if (keepDate && dates.indexOf(keepDate) >= 0) pick[keepDate] = 1;
    var need = cap - Object.keys(pick).length;
    for (var i = 0; i < need; i++) pick[dates[Math.round(i * (n - 1) / (need - 1 || 1))]] = 1;
    return Object.keys(pick).sort().slice(0, cap);
  }
  function _tlFigsOf(prefix) { return (prefix === "ca-mon" ? caMonFigs : dddFigs) || {}; }
  function _tlCtx(prefix) {
    var isMon = (prefix === "ca-mon");
    return {
      state:   isMon ? caMonBubFilter : dddBubFilter,
      merges:  isMon ? caMonBubMerges : dddBubMerges,
      members: isMon ? caMonBubAggMembers : dddBubAggMembers,
      activeDims: isMon ? DD_DIMS.filter(function(d) { return caMonDims[d]; })
                        : DDD_DIMS.filter(function(d) { return dddDims[d]; }),
    };
  }
  // i. adım için sentetik kaynak balFig — _extractFullBubData'nın okuduğu şema:
  // text=ürünler, y=r1(%), customdata=[b0_m, b1_m, r0(%), ad]. Layout gerçek
  // figürden gelir (başlık/eksen stilleri aynı kalsın).
  function _tlSynthBal(i) {
    var tl = _bubTL, c = tl.cache, st = c.steps[i];
    var figs = _tlFigsOf(tl.prefix);
    return {
      data: [{ text: c.products,
               y: st.r1.map(function(r) { return r * 100; }),
               customdata: c.products.map(function(p, k) {
                 return [c.b0[k], st.b1[k], c.r0[k] * 100, p];
               }) }],
      layout: (figs.bubble_balance && figs.bubble_balance.layout) || {},
    };
  }
  // Union aralığı: TÜM adımlar mevcut filtre/merge/split ile client-side
  // aggregate edilir; iki grafiğin x/y uçları + renk max'ları toplanır. Renk
  // çapası split'siz agregasyondan alınır (mevcut renk-sabitleme kuralıyla aynı).
  function _tlComputeUnion() {
    var tl = _bubTL, ctx = _tlCtx(tl.prefix);
    var splitMap = _bubSplit[tl.prefix] || null;
    var hasSplit = !!(splitMap && Object.keys(splitMap).length);
    var exB = { x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity };
    var exR = { x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity };
    var mB = 0, mR = 0;
    var scan = function(p, colorsToo) {
      var dx = p.b1_m - p.b0_m, yy = p.r1 * 100, rx = (p.r1 - p.r0) * 10000.0;
      if (dx < exB.x0) exB.x0 = dx; if (dx > exB.x1) exB.x1 = dx;
      if (yy < exB.y0) exB.y0 = yy; if (yy > exB.y1) exB.y1 = yy;
      if (rx < exR.x0) exR.x0 = rx; if (rx > exR.x1) exR.x1 = rx;
      if (yy < exR.y0) exR.y0 = yy; if (yy > exR.y1) exR.y1 = yy;
      if (colorsToo) {
        if (Math.abs(dx) > mB) mB = Math.abs(dx);
        if (Math.abs(rx) > mR) mR = Math.abs(rx);
      }
    };
    for (var i = 0; i < tl.cache.steps.length; i++) {
      var synth = _tlSynthBal(i);
      _aggregateBubbles(synth, ctx.state, ctx.merges, tl.cache.product_dims,
                        ctx.activeDims, splitMap).points.forEach(function(p) { scan(p, !hasSplit); });
      if (hasSplit) {
        _aggregateBubbles(synth, ctx.state, ctx.merges, tl.cache.product_dims,
                          ctx.activeDims, null).points.forEach(function(p) { scan(p, true); });
      }
    }
    // %10 pay: bubble yarıçapları autorange'in yaptığı gibi hesaba katılamaz,
    // sabit pay en büyük bubble'ın kenarda kırpılmasını önler.
    var pad = function(a, b) {
      if (!isFinite(a) || !isFinite(b)) return null;
      var w = (b - a) || Math.abs(a) || 1;
      return [a - w * 0.1, b + w * 0.1];
    };
    return {
      ax: { bal:  { x: pad(exB.x0, exB.x1), y: pad(exB.y0, exB.y1) },
            rate: { x: pad(exR.x0, exR.x1), y: pad(exR.y0, exR.y1) } },
      colorMaxBal: mB || undefined,
      colorMaxRate: mR || undefined,
    };
  }
  function _tlApplyMotion(trans) {
    var tl = _bubTL;
    if (!tl || !tl.union) return;
    _bubMotion[tl.prefix] = { ax: tl.union.ax, colorMaxBal: tl.union.colorMaxBal,
                              colorMaxRate: tl.union.colorMaxRate, trans: trans || 0 };
  }
  function _tlBeginMotion() {
    var tl = _bubTL;
    if (!tl || tl.inMotion) return;
    tl.inMotion = true;
    tl.union = _tlComputeUnion();   // her hareket başında taze (filtre değişmiş olabilir)
    _tlApplyMotion(0);
  }
  function _tlRenderStep(i, trans) {
    var tl = _bubTL;
    if (!tl || !tl.cache || i < 0 || i >= tl.dates.length) return;
    if (_bubMotion[tl.prefix]) _bubMotion[tl.prefix].trans = trans || 0;
    var ctx = _tlCtx(tl.prefix);
    var figs = _tlFigsOf(tl.prefix);
    // Rate kaynağı yalnız layout için kullanılır (_renderBubbles sözleşmesi).
    var synthRate = { data: [{}], layout: (figs.bubble_rate && figs.bubble_rate.layout) || {} };
    _renderBubbles(tl.prefix + "-bub-bal", tl.prefix + "-bub-rate",
                   _tlSynthBal(i), synthRate,
                   ctx.state, ctx.merges, tl.cache.product_dims, ctx.members,
                   tl.prefix, ctx.activeDims);
    tl.els.range.value = String(i);
    tl.els.date.textContent = tl.dates[i];
  }
  function _tlStopTimer() {
    var tl = _bubTL;
    if (!tl) return;
    if (tl.timer) { clearTimeout(tl.timer); tl.timer = null; }
    tl.playing = false;
    if (tl.els && tl.els.play) tl.els.play.textContent = "▶";
  }
  // Durma: hareket kilidi kalkar (🔒 kapalıysa) → seçili tarihe auto-fit render;
  // commit=true ise sayfadaki Date(End) slider tarihine çekilir (tek tam refetch).
  function _tlSettle(commit) {
    var tl = _bubTL;
    if (!tl) return;
    _tlStopTimer();
    tl.inMotion = false;
    if (!tl.lock) { delete _bubMotion[tl.prefix]; tl.union = null; }
    else if (_bubMotion[tl.prefix]) _bubMotion[tl.prefix].trans = 0;
    if (tl.cache && tl.cur >= 0) _tlRenderStep(tl.cur, 0);
    if (commit) _tlCommit();
  }
  function _tlCommit() {
    var tl = _bubTL;
    if (!tl || !tl.cache || tl.cur < 0) return;
    var d = tl.dates[tl.cur];
    var el = document.getElementById(tl.prefix + "-date1");
    if (!el || el.value === d) return;
    el.value = d;   // ca-mon: select (option değerleri zaten bu listeden); ddd: date input
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function _tlTogglePlay() {
    var tl = _bubTL;
    if (!tl || !tl.cache || !tl.dates.length) return;
    if (tl.playing) { _tlSettle(true); return; }   // ⏸ = durdur + commit
    var target = Math.max(0, Math.min(tl.dates.length - 1, Number(tl.els.range.value) || 0));
    var nSteps = target + 1;
    var stepDur = Math.max(500, Math.min(2000, 10000 / nSteps));
    var trans = Math.round(stepDur * 0.6);
    tl.playing = true;
    tl.els.play.textContent = "⏸";
    _tlBeginMotion();
    var i = 0;
    var tick = function() {
      if (!_bubTL || _bubTL !== tl || !tl.playing) return;
      tl.cur = i;
      _tlRenderStep(i, trans);
      if (i >= target) { tl.timer = setTimeout(function() { _tlSettle(true); }, stepDur); return; }
      i++;
      tl.timer = setTimeout(tick, stepDur);
    };
    tick();
  }
  // Prefetch. Aynı (date_0, tarih listesi, rate_conv) anahtarına sahipse ağa
  // çıkmadan yalnız slider konumunu sayfadaki Date(End)'e senkronlar (commit
  // sonrası refetch'in tetiklediği invalidate böylece ücretsizdir).
  async function _tlLoad(prefix) {
    var tl = _bubTL;
    if (!tl || tl.prefix !== prefix) return;
    var info = _tlDatesFor(prefix);
    if (!info.d0 || !info.dates.length) { tl.els.date.textContent = "No dates"; return; }
    var dates = _tlSample(info.dates, 120, _tlPageEnd(prefix));
    var key = info.d0 + "|" + dates.join(",") + "|" + _rateConvOf(prefix);
    if (tl.key === key && tl.cache) { _tlSyncToPage(); return; }
    tl.key = key;
    tl.cache = null;
    tl.union = null;
    tl.els.range.disabled = true;
    tl.els.date.textContent = "Loading…";
    try {
      var r = await fetch("/api/bubble_series?source=" + (prefix === "ca-mon" ? "monthly" : "daily")
                          + "&date_0=" + encodeURIComponent(info.d0)
                          + "&dates=" + encodeURIComponent(dates.join(","))
                          + "&rate_conv=" + encodeURIComponent(_rateConvOf(prefix)));
      var data = await r.json();
      if (!_bubTL || _bubTL !== tl || tl.key !== key) return;   // bu arada invalidate/teardown
      if (!data.ok || !data.steps || !data.steps.length) { tl.els.date.textContent = "Series could not be loaded"; return; }
      tl.cache = data;
      tl.dates = data.steps.map(function(s) { return s.date; });
      tl.els.range.max = String(tl.dates.length - 1);
      tl.els.range.disabled = false;
      _tlSyncToPage();
    } catch (e) {
      if (_bubTL === tl) tl.els.date.textContent = "Series could not be loaded";
    }
  }
  function _tlPageEnd(prefix) {
    var el = document.getElementById(prefix + "-date1");
    return (el && el.value) || "";
  }
  function _tlSyncToPage() {
    var tl = _bubTL;
    if (!tl || !tl.cache) return;
    var curEnd = _tlPageEnd(tl.prefix);
    var i = tl.dates.indexOf(curEnd);
    if (i < 0) {   // birebir yoksa Date(End)'i geçmeyen en son tarih
      i = 0;
      for (var k = 0; k < tl.dates.length; k++) if (tl.dates[k] <= curEnd) i = k;
    }
    tl.cur = i;
    tl.els.range.value = String(i);
    tl.els.date.textContent = tl.dates[i] || "—";
  }
  // Refetch sonrası çağrılır (fetch fonksiyonlarından): tarih listesi / rate_conv /
  // Date(Start) değişmiş olabilir → anahtar farklıysa yeniden prefetch.
  function _tlInvalidate(prefix) {
    if (!_bubTL || _bubTL.prefix !== prefix) return;
    _tlStopTimer();
    _bubTL.inMotion = false;
    _tlLoad(prefix);
  }
  function _tlBuildBar(prefix) {
    var bar = document.createElement("div");
    bar.className = "bub-minsize-bar";
    bar.id = prefix + "-bub-timeline-bar";
    // Kenarlık tema değişkeni: rgba(255,255,255,…) light modda görünmezdi.
    var btnCss = "background:none;border:1px solid var(--border-strong,rgba(128,128,128,0.4));"
               + "border-radius:4px;color:inherit;cursor:pointer;padding:2px 8px;font-size:12px;line-height:1.3;";
    bar.innerHTML =
      '<span class="bub-minsize-label">Date</span>' +
      '<button type="button" id="' + prefix + '-tl-play" title="Play from the slider start to the selected date" style="' + btnCss + '">▶</button>' +
      '<input type="range" class="bub-minsize-range" id="' + prefix + '-tl-range" min="0" max="0" step="1" value="0" disabled>' +
      '<span class="bub-minsize-val" id="' + prefix + '-tl-date">Loading…</span>' +
      '<button type="button" id="' + prefix + '-tl-lock" title="Lock axes to the union of the full range (kept after settling)" style="' + btnCss + 'opacity:0.45;">🔒</button>';
    return bar;
  }
  // Overlay'e bar kur + prefetch başlat. _open (fullscreen) çağırır; bar overlay
  // ile birlikte yaşar (kapanınca DOM'dan gider), _tlTeardown state'i temizler.
  function _tlInit(prefix, overlay) {
    _tlTeardown(false);
    var bar = _tlBuildBar(prefix);
    overlay.appendChild(bar);
    _bubTL = { prefix: prefix, dates: [], cur: -1, key: null, cache: null,
               playing: false, inMotion: false, timer: null, lock: false, union: null,
               _raf: 0,
               els: { bar: bar,
                      play:  bar.querySelector("#" + prefix + "-tl-play"),
                      range: bar.querySelector("#" + prefix + "-tl-range"),
                      date:  bar.querySelector("#" + prefix + "-tl-date"),
                      lock:  bar.querySelector("#" + prefix + "-tl-lock") } };
    var tl = _bubTL;
    tl.els.play.addEventListener("click", function() { _tlTogglePlay(); });
    tl.els.lock.addEventListener("click", function() {
      tl.lock = !tl.lock;
      tl.els.lock.style.opacity = tl.lock ? "1" : "0.45";
      if (tl.playing || !tl.cache || tl.cur < 0) return;
      if (tl.lock) { tl.union = tl.union || _tlComputeUnion(); _tlApplyMotion(0); }
      else if (!tl.inMotion) { delete _bubMotion[tl.prefix]; tl.union = null; }
      _tlRenderStep(tl.cur, 0);
    });
    // Sürükleme: hareket başlat (union kilidi) + rAF ile o tarihi çiz.
    tl.els.range.addEventListener("input", function() {
      if (!tl.cache) return;
      if (tl.playing) _tlStopTimer();
      _tlBeginMotion();
      tl.cur = Math.max(0, Math.min(tl.dates.length - 1, Number(this.value) || 0));
      tl.els.date.textContent = tl.dates[tl.cur] || "—";
      if (tl._raf) return;
      tl._raf = requestAnimationFrame(function() { tl._raf = 0; _tlRenderStep(tl.cur, 0); });
    });
    // Bırakma: settle (auto-fit) + commit (sayfa Date(End) senkronu).
    tl.els.range.addEventListener("change", function() { if (tl.cache) _tlSettle(true); });
    _tlLoad(prefix);
  }
  // Fullscreen kapanışı: timer durur, hareket kilidi silinir; bekleyen tarih
  // seçimi (play/drag ortasında kapatma) sayfaya commit edilir. _bubTL commit'ten
  // ÖNCE null'lanır — commit'in refetch'i _tlInvalidate'i tetikler, ölü referans kalmaz.
  function _tlTeardown(commitPending) {
    var tl = _bubTL;
    if (!tl) return;
    _tlStopTimer();
    delete _bubMotion[tl.prefix];
    _bubTL = null;
    if (tl.els && tl.els.bar && tl.els.bar.parentNode) tl.els.bar.remove();
    if (commitPending !== false && tl.cache && tl.cur >= 0) {
      var d = tl.dates[tl.cur];
      var el = document.getElementById(tl.prefix + "-date1");
      if (el && el.value !== d) {
        el.value = d;
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  }

  async function ensureBalanceDatesLoaded() {
    if (baDatesLoaded) return;
    try {
      var r = await fetch("/api/balance_dates");
      var data = await r.json();
      if (!data.ok) return;
      baMonthlyDates = data.monthly_dates || [];
      baDailyDates   = data.daily_dates   || [];
      baDailyDateSet = new Set(baDailyDates);
      baFilterMeta   = data.filter_meta   || {};
      // Monthly dropdowns
      var sel0 = document.getElementById("ba-mon-date0");
      var sel1 = document.getElementById("ba-mon-date1");
      if (sel0 && sel1) {
        sel0.innerHTML = ""; sel1.innerHTML = "";
        baMonthlyDates.forEach(function(d) {
          sel0.appendChild(new Option(d, d));
          sel1.appendChild(new Option(d, d));
        });
        if (baMonthlyDates.length >= 2) {
          sel0.value = baMonthlyDates[baMonthlyDates.length - 2];
          sel1.value = baMonthlyDates[baMonthlyDates.length - 1];
        }
      }
      // Daily date inputs
      var inp0 = document.getElementById("ba-dly-date0");
      var inp1 = document.getElementById("ba-dly-date1");
      var hint = document.getElementById("ba-dly-date-hint");
      if (inp0 && inp1 && baDailyDates.length > 0) {
        var minD = baDailyDates[0], maxD = baDailyDates[baDailyDates.length - 1];
        inp0.min = minD; inp0.max = maxD;
        inp1.min = minD; inp1.max = maxD;
        // Default start = most recent Thursday strictly before maxD
        inp0.value = _prevThursday(baDailyDates, maxD);
        inp1.value = maxD;
        if (hint) hint.textContent = "(" + minD + " — " + maxD + ")";
      }
      _renderBaFilterPanels();
      baDatesLoaded = true;
    } catch(e) { /* silent */ }
  }

  // Panel render'ı ayrı fonksiyonda: sayfaya her girişte yeniden çizilir ki
  // başka sayfada (ör. NP AUM_BAND aynası) oluşturulan gruplar görünür olsun.
  // ── Sector Comparison — sektör mevduat faiz oranı kontrol tablosu (AG Grid) ──
  var _sectorGridApi = null;
  // ── Sektör faiz oranı combo grafiği (tablo verisinden, TP) ──────────────────
  // Üst panel (line): Banka / Toplam Sektör / Özel Sektör yıllık faiz oranları
  // ("Faiz Oranı (yıllık %)" kolonu; banka oranı TP-only olduğundan grafik TP).
  // Alt panel (bar, yan yana): ay bazında Banka−Toplam ve Banka−Özel farkları
  // (puan). Renk eşleşmesi: fark barı, karşılaştırılan sektör çizgisinin rengini
  // taşır (hangi barın hangi segmente ait olduğu lejantsız da okunur).
  var _sectorRateRows = null;   // son fetch'in satırları (dropdown değişiminde yeniden çiz)
  var _sectorOsMonRows = null;  // aylık outstanding satırları (combo'nun TCMB serileri)

  // Combo grafiğin outstanding-tabanlı ek serileri: TCMB (Bank Mix) ve TCMB
  // (BDDK Mix) kesikli çizgiler + Bank − TCMB spread barları (Monthly
  // Outstanding Summary satırlarından; buradaki Bank = outstanding kitabın
  // compound oranı — tablo ile birebir aynı sayılar). DEFAULT GİZLİ
  // (visible:"legendonly" → legend'den tıklayınca açılır). Outstanding serisi
  // BDDK maliyet verisinden daha güncel aylara uzanır → kategori ekseni
  // kendiliğinden uzar (categoryorder ascending kronolojiyi korur).
  function _sectorRateOsTraces(mode, lblPct, barHover) {
    var os = _sectorOsMonRows;
    if (!os || !os.length) return [];
    var mEnd = function(ay) {          // "YYYY-MM" → ay-sonu "YYYY-MM-DD" (BDDK x'iyle hizalı)
      var pp = ay.split("-");
      var dt = new Date(Number(pp[0]), Number(pp[1]), 0);
      return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0")
           + "-" + String(dt.getDate()).padStart(2, "0");
    };
    var F = mode === "on"
      ? { t: "tcmb_on_pct", b: "tcmb_bddk_on_pct", st: "spread_on_pct", sb: "spread_bank_bddk_on_pct" }
      : mode === "compound"
      ? { t: "tcmb_pct", b: "tcmb_bddk_pct", st: "spread_comp_pct", sb: null }
      : { t: "tcmb_simple_pct", b: "tcmb_bddk_simple_pct", st: "spread_simple_pct", sb: "spread_bank_bddk_pct" };
    var byM = {};
    os.forEach(function(r) { byM[mEnd(r.ay)] = r; });
    var oM = Object.keys(byM).sort();
    var pick = function(f) { return oM.map(function(mo) { var v = byM[mo][f]; return v == null ? null : v; }); };
    var yT = pick(F.t), yB = pick(F.b), sT = pick(F.st);
    // Compound uzayında Bank−BDDK kolonu yok → satırdan türet (bank_comp − bddk_comp).
    var sB = F.sb ? pick(F.sb) : oM.map(function(mo) {
      var r = byM[mo];
      return (r.bank_comp_pct != null && r.tcmb_bddk_pct != null)
        ? Math.round((r.bank_comp_pct - r.tcmb_bddk_pct) * 100) / 100 : null;
    });
    var osHover = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f}%<extra></extra>";
    var C_T = "#5C6478", C_B = "#8B7BA8";   // PRISMA: nötr gri-mavi + lavanta
    return [
      { type: "scatter", mode: "lines+markers+text", name: "TCMB (Bank Mix)",
        x: oM, y: yT, text: lblPct(yT), textposition: "top center",
        textfont: { size: 10 }, cliponaxis: false, visible: "legendonly",
        line: { color: C_T, width: 2, dash: "dash" }, marker: { size: 5 },
        hovertemplate: osHover, yaxis: "y" },
      { type: "scatter", mode: "lines+markers+text", name: "TCMB (BDDK Mix)",
        x: oM, y: yB, text: lblPct(yB), textposition: "bottom center",
        textfont: { size: 10 }, cliponaxis: false, visible: "legendonly",
        line: { color: C_B, width: 2, dash: "dash" }, marker: { size: 5 },
        hovertemplate: osHover, yaxis: "y" },
      { type: "bar", name: "Bank − TCMB (Bank Mix)",
        x: oM, y: sT, marker: { color: C_T, opacity: 0.8 }, visible: "legendonly",
        text: lblPct(sT), textposition: "outside", textfont: { size: 10 },
        cliponaxis: false, hovertemplate: barHover, yaxis: "y2" },
      { type: "bar", name: "Bank − TCMB (BDDK Mix)",
        x: oM, y: sB, marker: { color: C_B, opacity: 0.8 }, visible: "legendonly",
        text: lblPct(sB), textposition: "outside", textfont: { size: 10 },
        cliponaxis: false, hovertemplate: barHover, yaxis: "y2" },
    ];
  }

  function _renderSectorRateChart(rows) {
    if (rows) _sectorRateRows = rows;
    rows = _sectorRateRows;
    var host = document.getElementById("sector-rate-chart");
    if (!host || !rows) return;
    var mode = (document.getElementById("sector-rate-conv") || {}).value || "simple";
    // Mod → satırdaki alanlar (backend tenorlarla önceden çevirir; banka kendi
    // aylık wavg tenoru, sektör segment-bazlı BDDK_VADE tenoru ile).
    var bankF = mode === "on" ? "bank_rate_on_pct"
              : mode === "compound" ? "bank_rate_comp_pct" : "bank_rate_pct";
    var secF  = mode === "on" ? "rate_on_pct"
              : mode === "compound" ? "rate_comp_pct" : "rate_pct";
    var modeLbl = mode === "on" ? "O/N Equivalent"
                : mode === "compound" ? "Annual Compound" : "Simple";
    // Kullanıcının legend'den açtığı/kapattığı seriler mod değişiminde (yeniden
    // çizimde) korunur — mevcut görünürlük ad bazında taşınır.
    var prevVis = {};
    if (host.data) host.data.forEach(function(t) { if (t.name) prevVis[t.name] = t.visible; });
    var tp = (rows || []).filter(function(r) { return String(r.ccy).indexOf("TP") === 0; });
    if (!tp.length) return;
    var months = [];
    var bank = {}, toplam = {}, ozel = {}, bTen = {}, sTenTop = {}, sTenOzl = {};
    tp.forEach(function(r) {
      if (months.indexOf(r.month) < 0) months.push(r.month);
      if (r[bankF] != null) bank[r.month] = r[bankF];
      if (r.bank_tenor_gun != null) bTen[r.month] = r.bank_tenor_gun;
      if (r.segment === "Total Sector" && r[secF] != null) {
        toplam[r.month] = r[secF];
        if (r.sektor_tenor_gun != null) sTenTop[r.month] = r.sektor_tenor_gun;
      }
      if (r.segment === "Private Sector" && r[secF] != null) {
        ozel[r.month] = r[secF];
        if (r.sektor_tenor_gun != null) sTenOzl[r.month] = r.sektor_tenor_gun;
      }
    });
    months.sort();
    var pick = function(m) { return function(mo) { return m[mo] != null ? m[mo] : null; }; };
    var yBank = months.map(pick(bank)), yTop = months.map(pick(toplam)), yOzl = months.map(pick(ozel));
    var dTop = months.map(function(mo, i) {
      return (yBank[i] != null && yTop[i] != null) ? Math.round((yBank[i] - yTop[i]) * 100) / 100 : null;
    });
    var dOzl = months.map(function(mo, i) {
      return (yBank[i] != null && yOzl[i] != null) ? Math.round((yBank[i] - yOzl[i]) * 100) / 100 : null;
    });
    // KALICI seviye etiketleri (hover beklenmez): çizgi noktaları üstte %,
    // fark barları dışa doğru puan. 2 haneli kompakt format.
    var lblPct = function(arr) {
      return arr.map(function(v) { return v == null ? "" : v.toFixed(2); });
    };
    var tenTxt = function(m) {
      return function(mo) { return m[mo] != null ? Math.round(m[mo]) + " days" : "—"; };
    };
    // PRISMA çizgi paleti: amber (birincil/banka), denim (Toplam), adaçayı (Özel).
    var C_BANK = "#D4A574", C_TOP = "#4A6B8A", C_OZL = "#7A9B7E";
    var lnHover = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f}%<br>wavg tenor: %{customdata}<extra></extra>";
    var barHover = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f} pts<extra></extra>";
    var lnTextCfg = { textposition: "top center", textfont: { size: 10 }, cliponaxis: false };
    // Sektör etiketleri çakışmasın: her ay üstte kalan çizginin etiketi üste,
    // alttakininki alta (sıra ay ay değişebilir → nokta bazında konum dizisi).
    var posTop = months.map(function(mo, i) {
      return (yTop[i] != null && yOzl[i] != null && yOzl[i] > yTop[i])
        ? "bottom center" : "top center";
    });
    var posOzl = months.map(function(mo, i) {
      return (yTop[i] != null && yOzl[i] != null && yOzl[i] > yTop[i])
        ? "top center" : "bottom center";
    });
    renderPlotlyFig("sector-rate-chart", {
      data: [
        Object.assign({ type: "scatter", mode: "lines+markers+text", name: "Bank",
          x: months, y: yBank, text: lblPct(yBank), customdata: months.map(tenTxt(bTen)),
          line: { color: C_BANK, width: 2.5 }, marker: { size: 6 },
          hovertemplate: lnHover, yaxis: "y" }, lnTextCfg),
        { type: "scatter", mode: "lines+markers+text", name: "Total Sector",
          x: months, y: yTop, text: lblPct(yTop), customdata: months.map(tenTxt(sTenTop)),
          line: { color: C_TOP, width: 2 }, marker: { size: 5 },
          hovertemplate: lnHover, yaxis: "y",
          textposition: posTop, textfont: { size: 10 }, cliponaxis: false },
        { type: "scatter", mode: "lines+markers+text", name: "Private Sector",
          x: months, y: yOzl, text: lblPct(yOzl), customdata: months.map(tenTxt(sTenOzl)),
          line: { color: C_OZL, width: 2 }, marker: { size: 5 },
          hovertemplate: lnHover, yaxis: "y",
          textposition: posOzl, textfont: { size: 10 }, cliponaxis: false },
        { type: "bar", name: "Bank − Total Sector",
          x: months, y: dTop, marker: { color: C_TOP, opacity: 0.8 },
          text: lblPct(dTop), textposition: "outside", textfont: { size: 10 },
          cliponaxis: false, hovertemplate: barHover, yaxis: "y2" },
        { type: "bar", name: "Bank − Private Sector",
          x: months, y: dOzl, marker: { color: C_OZL, opacity: 0.8 },
          text: lblPct(dOzl), textposition: "outside", textfont: { size: 10 },
          cliponaxis: false, hovertemplate: barHover, yaxis: "y2" },
      ].concat(_sectorRateOsTraces(mode, lblPct, barHover)).map(function(t) {
        if (t.name && prevVis[t.name] !== undefined) t.visible = prevVis[t.name];
        return t;
      }),
      layout: {
        title: { text: "Deposit Interest Rate — Bank vs Sector (TP, annualized)  ·  " + modeLbl },
        barmode: "group",
        xaxis:  { domain: [0, 1], anchor: "y2", type: "category",
                  categoryorder: "category ascending" },
        yaxis:  { domain: [0.36, 1], title: { text: "Rate (annual %)", font: { size: 11 } } },
        yaxis2: { domain: [0, 0.28], title: { text: "Bank − Sector (pts)", font: { size: 11 } },
                  zeroline: true },
        legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom" },
        margin: { l: 56, r: 24, t: 88, b: 44 },
        bargap: 0.25,
      },
    }, 520);
  }

  // Rate Type dropdown'ı — refetch YOK, son satırlarla client-side yeniden çizim.
  (function _wireSectorRateConv() {
    var sel = document.getElementById("sector-rate-conv");
    if (sel) sel.addEventListener("change", function() {
      _renderSectorRateChart(null);
      // Sunum Slide 4'ün NP grafiği ve üst tabloları da aynı seçimi kullanır.
      try { _bscRenderNpChart(); } catch (e) {}
      try { _bscRenderNpTables(); } catch (e) {}
    });
  })();

  async function fetchSectorRates() {
    var host = document.getElementById("sector-cmp-grid");
    if (!host || typeof agGrid === "undefined") return;
    try {
      var r = await fetch("/api/sector_deposit_rates?_=1" + _bscDemandQS());
      var data = await r.json();
      if (!data.ok) { showError("Sector Comparison: " + (data.error || "no data received")); return; }
      var rows = (data.rows || []).map(function(x) {
        return {
          month:       x.month,
          segment:     x.segment,
          ccy:         x.ccy === "TP" ? "TP (TRY)" : (x.ccy === "YP" ? "YP (FX)" : x.ccy),
          bank_rate_pct:   x.bank_rate_pct,
          rate_pct:        x.rate_pct,
          rate_rees_pct:   x.rate_rees_pct,
          // Combo grafiğin Rate Type dönüşümleri (backend tenorlarla çevirir).
          bank_tenor_gun:     x.bank_tenor_gun,
          bank_rate_on_pct:   x.bank_rate_on_pct,
          bank_rate_comp_pct: x.bank_rate_comp_pct,
          sektor_tenor_gun:   x.sektor_tenor_gun,
          rate_on_pct:        x.rate_on_pct,
          rate_comp_pct:      x.rate_comp_pct,
          fg_month:        x.fg_month,
          fg_cum:          x.fg_cum,
          fg_prev_cum:     x.fg_prev_cum,
          ort_bakiye:      x.ort_bakiye,
          ort_bakiye_rees: x.ort_bakiye_rees,
          bakiye_end:      x.bakiye_end,
          bakiye_prev:     x.bakiye_prev,
        };
      });
      var num0 = function(p) {
        return p.value == null ? "" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 0 });
      };
      var num0dash = function(p) {   // Ocak'ta önceki-ay kümülatif yok → "—"
        return p.value == null ? "—" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 0 });
      };
      var pct2 = function(p) {
        return p.value == null ? "" :
          Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
      };
      var colDefs = [
        { field: "month",       headerName: "Month (Month-End)",          width: 128, pinned: "left" },
        { field: "segment",     headerName: "Segment",               width: 138, cellStyle: { fontWeight: 600 } },
        { field: "ccy",         headerName: "Para Birimi",           width: 108 },
        { field: "bank_rate_pct", headerName: "Bank Rate (annual %)",    width: 190, type: "numericColumn",
          valueFormatter: pct2, cellStyle: { fontWeight: 700, color: "var(--accent)" } },
        { field: "rate_pct",      headerName: "Rate (annual %)",          width: 168, type: "numericColumn",
          valueFormatter: pct2, cellStyle: { fontWeight: 700, color: "var(--accent)" } },
        { field: "rate_rees_pct", headerName: "Rate (annual, accrual %)", width: 200, type: "numericColumn",
          valueFormatter: pct2, cellStyle: { fontWeight: 700, color: "var(--accent)" } },
        { field: "fg_month",        headerName: "Interest Expense",          width: 150, type: "numericColumn", valueFormatter: num0 },
        { field: "fg_cum",          headerName: "Interest Expense (Cum.)",   width: 158, type: "numericColumn", valueFormatter: num0 },
        { field: "fg_prev_cum",     headerName: "Prev. Month (Cum.)",     width: 150, type: "numericColumn", valueFormatter: num0dash },
        { field: "ort_bakiye",      headerName: "Avg. Balance",          width: 146, type: "numericColumn", valueFormatter: num0 },
        { field: "ort_bakiye_rees", headerName: "Avg. Balance (+Accr.)", width: 168, type: "numericColumn", valueFormatter: num0 },
        { field: "bakiye_end",      headerName: "Balance (Month-End)",     width: 158, type: "numericColumn", valueFormatter: num0 },
        { field: "bakiye_prev",     headerName: "Balance (Prev. Month)",   width: 158, type: "numericColumn", valueFormatter: num0 },
      ];
      // Combo grafik tablo ile aynı satırlardan beslenir (grid güncellemesinde de).
      _renderSectorRateChart(rows);
      if (_sectorGridApi) {
        _sectorGridApi.setGridOption("rowData", rows);
        return;
      }
      _sectorGridApi = agGrid.createGrid(host, {
        columnDefs: colDefs,
        rowData: rows,
        defaultColDef: { sortable: true, resizable: true },
        headerHeight: 42,
        rowHeight: 30,
        enableCellTextSelection: true,
      });
    } catch (e) {
      showError("Sector Comparison error: " + (e.message || String(e)));
    }
  }

  // ── TCMB vade-bazlı faiz tablosu (Sector Comparison ikinci tablo) ──────────
  var _tcmbGridApi = null;
  async function fetchTcmbRates() {
    var host = document.getElementById("tcmb-rate-grid");
    if (!host || typeof agGrid === "undefined") return;
    try {
      var r = await fetch("/api/tcmb_rate_table");
      var data = await r.json();
      if (!data.ok) { showError("TCMB tablosu: " + (data.error || "no data received")); return; }
      var pct2 = function(p) {
        return p.value == null ? "" :
          Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
      };
      var colDefs = [
        { field: "date", headerName: "Date", width: 128, pinned: "left" },
        { field: "cur",  headerName: "Currency", width: 92,  cellStyle: { fontWeight: 600, color: "var(--accent)" } },
      ];
      (data.buckets || []).forEach(function(b) {
        colDefs.push({ field: b.key, headerName: b.label, width: 118,
                       type: "numericColumn", valueFormatter: pct2 });
      });
      if (_tcmbGridApi) {
        _tcmbGridApi.setGridOption("rowData", data.rows || []);
        return;
      }
      _tcmbGridApi = agGrid.createGrid(host, {
        columnDefs: colDefs,
        rowData: data.rows || [],
        defaultColDef: { sortable: true, resizable: true },
        headerHeight: 42,
        rowHeight: 30,
        enableCellTextSelection: true,
      });
    } catch (e) {
      showError("TCMB table error: " + (e.message || String(e)));
    }
  }

  // ── Sektör Blotter tablosu (Sector Comparison üçüncü tablo) ────────────────
  var _blotterGridApi = null;
  async function fetchSectorBlotter() {
    var host = document.getElementById("sector-blotter-grid");
    if (!host || typeof agGrid === "undefined") return;
    try {
      var r = await fetch("/api/sector_blotter");
      var data = await r.json();
      if (!data.ok) { showError("Sector Blotter: " + (data.error || "no data received")); return; }
      var noteEl = document.getElementById("sector-blotter-note");
      if (noteEl) {
        noteEl.textContent = data.dq_note || "";
        noteEl.style.display = data.dq_note ? "" : "none";
      }
      var num2 = function(p) {
        return p.value == null ? "" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 2 });
      };
      var num1 = function(p) {
        return p.value == null ? "" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 1 });
      };
      var pct2 = function(p) {
        return p.value == null ? "—" :
          Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
      };
      var colDefs = [
        { field: "val_dt",        headerName: "VAL_DT",            width: 122, pinned: "left" },
        { field: "vade_bucket",   headerName: "Maturity Bucket",       width: 122, cellStyle: { fontWeight: 600 } },
        { field: "bakiye",        headerName: "Balance (₺M)",       width: 140, type: "numericColumn", valueFormatter: num2 },
        { field: "wavg_dtm",      headerName: "WAVG DTM (days)",    width: 140, type: "numericColumn", valueFormatter: num1 },
        { field: "wavg_comp_pct", headerName: "Rate (Compound %)", width: 158, type: "numericColumn",
          valueFormatter: pct2, cellStyle: { fontWeight: 700, color: "var(--accent)" } },
        { field: "tcmb_rate_pct", headerName: "TCMB (%)",          width: 122, type: "numericColumn", valueFormatter: pct2 },
        { field: "tcmb_date",     headerName: "TCMB Date",        width: 122 },
      ];
      if (_blotterGridApi) {
        _blotterGridApi.setGridOption("rowData", data.rows || []);
        return;
      }
      _blotterGridApi = agGrid.createGrid(host, {
        columnDefs: colDefs,
        rowData: data.rows || [],
        defaultColDef: { sortable: true, resizable: true, filter: true },
        headerHeight: 42,
        rowHeight: 30,
        enableCellTextSelection: true,
      });
    } catch (e) {
      showError("Sector Blotter error: " + (e.message || String(e)));
    }
  }

  // ── Gün-gün outstanding tablosu (Sector Comparison dördüncü tablo) ─────────
  var _sectorOsGridApi = null;
  async function fetchSectorOutstanding() {
    var host = document.getElementById("sector-os-grid");
    if (!host || typeof agGrid === "undefined") return;
    try {
      var r = await fetch("/api/sector_outstanding");
      var data = await r.json();
      if (!data.ok) { showError("Outstanding tablosu: " + (data.error || "no data received")); return; }
      var noteEl = document.getElementById("sector-os-note");
      if (noteEl) {
        noteEl.textContent = data.dq_note || "";
        noteEl.style.display = data.dq_note ? "" : "none";
      }
      var num2 = function(p) {
        return p.value == null ? "" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 2 });
      };
      var pct2 = function(p) {
        return p.value == null ? "—" :
          Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
      };
      var num1t = function(p) {
        return p.value == null ? "" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 1 });
      };
      // Spread: işaretli, bps'e değil % puana göre; pozitif → banka TCMB üstünde.
      var spread2 = function(p) {
        return p.value == null ? "—" :
          (p.value > 0 ? "+" : "") +
          Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
      };
      var _spreadStyle = function(p) {
        if (p.value == null) return null;
        return { fontWeight: 600, color: p.value >= 0 ? "#7A9B7E" : "#B8826B" };
      };
      var colDefs = [
        { field: "tarih",             headerName: "Date",                      width: 118, pinned: "left" },
        { field: "bakiye",            headerName: "Total Balance (₺M)",         width: 160, type: "numericColumn", valueFormatter: num2 },
        { field: "wavg_tenor",        headerName: "WAVG Tenor (days)",           width: 145, type: "numericColumn", valueFormatter: num1t },
        { field: "bank_comp_pct",     headerName: "Bank Outstanding (Comp %)", width: 190, type: "numericColumn",
          valueFormatter: pct2, cellStyle: { fontWeight: 700, color: "var(--accent)" } },
        { field: "tcmb_pct",          headerName: "TCMB Outstanding (%)",       width: 165, type: "numericColumn", valueFormatter: pct2 },
        { field: "spread_comp_pct",   headerName: "Spread (Comp)",              width: 135, type: "numericColumn",
          valueFormatter: spread2, cellStyle: _spreadStyle },
        { field: "bank_simple_pct",   headerName: "Bank Simple (%)",           width: 150, type: "numericColumn",
          valueFormatter: pct2, cellStyle: { fontWeight: 700, color: "var(--accent)" } },
        { field: "tcmb_simple_pct",   headerName: "TCMB Simple (%)",            width: 145, type: "numericColumn", valueFormatter: pct2 },
        { field: "spread_simple_pct", headerName: "Spread (Simple)",            width: 140, type: "numericColumn",
          valueFormatter: spread2, cellStyle: _spreadStyle },
        { field: "tcmb_bddk_pct",        headerName: "TCMB (BDDK Mix %)",       width: 158, type: "numericColumn", valueFormatter: pct2 },
        { field: "tcmb_bddk_simple_pct", headerName: "TCMB BDDK Simple (%)",    width: 172, type: "numericColumn", valueFormatter: pct2 },
        { field: "spread_bank_bddk_pct", headerName: "Spread (Bank − BDDK Mix)", width: 190, type: "numericColumn",
          valueFormatter: spread2, cellStyle: _spreadStyle },
        { field: "mix_simple_pct",       headerName: "Mix Effect (Simple)",     width: 158, type: "numericColumn",
          valueFormatter: spread2, cellStyle: _spreadStyle },
        { field: "bddk_w_ay",            headerName: "BDDK Mix Month",            width: 122,
          valueFormatter: function(p) { return p.value == null ? "—" : p.value; } },
        { field: "bddk_w_kapsam",        headerName: "Mix Coverage (%)",          width: 132, type: "numericColumn",
          valueFormatter: function(p) { return p.value == null ? "—" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 1 }) + " %"; } },
      ];
      if (_sectorOsGridApi) {
        _sectorOsGridApi.setGridOption("rowData", data.rows || []);
        return;
      }
      _sectorOsGridApi = agGrid.createGrid(host, {
        columnDefs: colDefs,
        rowData: data.rows || [],
        defaultColDef: { sortable: true, resizable: true },
        headerHeight: 42,
        rowHeight: 30,
        enableCellTextSelection: true,
      });
    } catch (e) {
      showError("Outstanding table error: " + (e.message || String(e)));
    }
  }

  // ── Aylık outstanding özeti (Sector Comparison beşinci tablo) ──────────────
  var _sectorOsMonGridApi = null;
  async function fetchSectorOutstandingMonthly() {
    var host = document.getElementById("sector-os-mon-grid");
    if (!host || typeof agGrid === "undefined") return;
    try {
      var r = await fetch("/api/sector_outstanding_monthly");
      var data = await r.json();
      if (!data.ok) { showError("Monthly outstanding: " + (data.error || "no data received")); return; }
      // Combo grafiğin outstanding-tabanlı TCMB serileri bu satırlardan beslenir;
      // faiz tablosu önce yüklendiyse grafik yeni serilerle yeniden çizilir.
      _sectorOsMonRows = data.rows || [];
      _renderSectorRateChart(null);
      var num2 = function(p) {
        return p.value == null ? "" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 2 });
      };
      var num1t = function(p) {
        return p.value == null ? "" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 1 });
      };
      var pct2 = function(p) {
        return p.value == null ? "—" :
          Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
      };
      var spread2 = function(p) {
        return p.value == null ? "—" :
          (p.value > 0 ? "+" : "") +
          Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
      };
      var _spreadStyle = function(p) {
        if (p.value == null) return null;
        return { fontWeight: 600, color: p.value >= 0 ? "#7A9B7E" : "#B8826B" };
      };
      var colDefs = [
        { field: "ay",                headerName: "Month",                        width: 108, pinned: "left" },
        { field: "bakiye",            headerName: "Avg. Balance (₺M)",          width: 160, type: "numericColumn", valueFormatter: num2 },
        { field: "wavg_tenor",        headerName: "WAVG Tenor (days)",          width: 145, type: "numericColumn", valueFormatter: num1t },
        { field: "bank_comp_pct",     headerName: "Bank Outstanding (Comp %)", width: 190, type: "numericColumn",
          valueFormatter: pct2, cellStyle: { fontWeight: 700, color: "var(--accent)" } },
        { field: "tcmb_pct",          headerName: "TCMB Outstanding (%)",       width: 165, type: "numericColumn", valueFormatter: pct2 },
        { field: "spread_comp_pct",   headerName: "Spread (Comp)",              width: 135, type: "numericColumn",
          valueFormatter: spread2, cellStyle: _spreadStyle },
        { field: "bank_simple_pct",   headerName: "Bank Simple (%)",           width: 150, type: "numericColumn",
          valueFormatter: pct2, cellStyle: { fontWeight: 700, color: "var(--accent)" } },
        { field: "tcmb_simple_pct",   headerName: "TCMB Simple (%)",            width: 145, type: "numericColumn", valueFormatter: pct2 },
        { field: "spread_simple_pct", headerName: "Spread (Simple)",            width: 140, type: "numericColumn",
          valueFormatter: spread2, cellStyle: _spreadStyle },
        { field: "tcmb_bddk_pct",        headerName: "TCMB (BDDK Mix %)",       width: 158, type: "numericColumn", valueFormatter: pct2 },
        { field: "tcmb_bddk_simple_pct", headerName: "TCMB BDDK Simple (%)",    width: 172, type: "numericColumn", valueFormatter: pct2 },
        { field: "spread_bank_bddk_pct", headerName: "Spread (Bank − BDDK Mix)", width: 190, type: "numericColumn",
          valueFormatter: spread2, cellStyle: _spreadStyle },
        { field: "mix_simple_pct",       headerName: "Mix Effect (Simple)",     width: 158, type: "numericColumn",
          valueFormatter: spread2, cellStyle: _spreadStyle },
        { field: "bddk_w_ay",            headerName: "BDDK Mix Month",            width: 122,
          valueFormatter: function(p) { return p.value == null ? "—" : p.value; } },
        { field: "bddk_w_kapsam",        headerName: "Mix Coverage (%)",          width: 132, type: "numericColumn",
          valueFormatter: function(p) { return p.value == null ? "—" : Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 1 }) + " %"; } },
        // Sektör tenoru + O/N eşlenikleri (bileşikten 365 günle, vadeden bağımsız).
        { field: "bddk_tenor",              headerName: "BDDK Tenor (days)",              width: 145, type: "numericColumn", valueFormatter: num1t },
        { field: "bank_on_pct",             headerName: "Bank O/N Eq. (%)",               width: 150, type: "numericColumn", valueFormatter: pct2 },
        { field: "tcmb_on_pct",             headerName: "TCMB O/N Eq. (%)",               width: 150, type: "numericColumn", valueFormatter: pct2 },
        { field: "tcmb_bddk_on_pct",        headerName: "TCMB BDDK O/N Eq. (%)",          width: 175, type: "numericColumn", valueFormatter: pct2 },
        { field: "spread_on_pct",           headerName: "Spread O/N (Bank − TCMB)",       width: 185, type: "numericColumn",
          valueFormatter: pct2, cellStyle: _spreadStyle },
        { field: "spread_bank_bddk_on_pct", headerName: "Spread O/N (Bank − BDDK Mix)",   width: 205, type: "numericColumn",
          valueFormatter: pct2, cellStyle: _spreadStyle },
      ];
      if (_sectorOsMonGridApi) {
        _sectorOsMonGridApi.setGridOption("rowData", data.rows || []);
        return;
      }
      _sectorOsMonGridApi = agGrid.createGrid(host, {
        columnDefs: colDefs,
        rowData: data.rows || [],
        defaultColDef: { sortable: true, resizable: true },
        headerHeight: 42,
        rowHeight: 30,
        enableCellTextSelection: true,
      });
    } catch (e) {
      showError("Monthly outstanding error: " + (e.message || String(e)));
    }
  }

  // ── Mix etkisi kova ayrıştırması (Sector Comparison — grafik + tablo) ──────
  // Simple/Compound iOS-toggle'ı: katkılar/oranlar seçili uzayda gösterilir.
  // Veri tek fetch'te iki uzayı da taşır (katki_bps/katki_s_bps, r_b/r_b_s);
  // toggle YALNIZ client-side yeniden render eder.
  var _sectorMixGridApi = null;
  var _sectorMixRows = null;
  var _sectorMixMode = "comp";   // "comp" | "simple"

  function _setSectorMixMode(mode) {
    if (mode !== "comp" && mode !== "simple") return;
    if (_sectorMixMode === mode) return;
    _sectorMixMode = mode;
    var sw = document.getElementById("sector-mix-mode-switch");
    if (sw) {
      sw.classList.toggle("is-right", mode === "simple");
      sw.querySelectorAll(".hm-lbl").forEach(function(l) {
        l.classList.toggle("active", l.dataset.mode === mode);
      });
    }
    _renderSectorMix();
  }
  window._setSectorMixMode = _setSectorMixMode;
  // Track (knob) tıklaması için flip — inline onclick fallback'i bunu çağırır.
  window._toggleSectorMixMode = function() {
    _setSectorMixMode(_sectorMixMode === "comp" ? "simple" : "comp");
  };

  function _renderSectorMix() {
    var chartHost = document.getElementById("sector-mix-chart");
    var gridHost = document.getElementById("sector-mix-grid");
    if (!chartHost || !gridHost || !_sectorMixRows) return;
    var simple = (_sectorMixMode === "simple");
    var modeLbl = simple ? "Simple" : "Compound";
    // BEKÇİ: Simple seçildi ama satırların hiçbirinde katki_s_bps yok → çalışan
    // backend ESKİ demektir (Flask debug modda ŞABLONU otomatik yeniler ama
    // Python prosesini yenilemez → yeni UI + eski endpoint). Sessizce sıfır
    // çizmek yerine açık uyarı ver.
    if (simple && _sectorMixRows.length &&
        _sectorMixRows.every(function(x) { return x.katki_s_bps == null; })) {
      showError("Mix Effect (Simple): backend does not return katki_s_bps — the running " +
                "Flask process is running old code. After updating the files, FULLY restart " +
                "Flask (templates auto-reload in debug mode, Python code does not).");
      return;
    }
    // Moda göre seçilmiş alanlarla görüntü satırları (grid kolonları sabit kalır).
    var rows = _sectorMixRows.map(function(x) {
      return {
        ay: x.ay, kova: x.kova, kova_key: x.kova_key,
        w_bank: x.w_bank, w_sektor: x.w_sektor, dw_pp: x.dw_pp,
        r_b:       simple ? x.r_b_s : x.r_b,
        katki_bps: simple ? x.katki_s_bps : x.katki_bps,
      };
    });
    // ── Stacked bar (kova katkıları) + toplam çizgisi ──────────────────────
    var months = []; rows.forEach(function(x) { if (months.indexOf(x.ay) < 0) months.push(x.ay); });
    months.sort();
    var kovaKeys = []; var kovaLbl = {};
    rows.forEach(function(x) { if (kovaKeys.indexOf(x.kova_key) < 0) { kovaKeys.push(x.kova_key); kovaLbl[x.kova_key] = x.kova; } });
    var byKM = {}; rows.forEach(function(x) { byKM[x.kova_key + "|" + x.ay] = x.katki_bps; });
    var traces = kovaKeys.map(function(k, i) {
      return {
        type: "bar", name: kovaLbl[k],
        x: months,
        y: months.map(function(m) { var v = byKM[k + "|" + m]; return v == null ? 0 : v; }),
        marker: { color: _PRISMA_CAT[i % _PRISMA_CAT.length] },
        hovertemplate: kovaLbl[k] + ": %{y:.1f} bps<extra></extra>",
      };
    });
    var totals = months.map(function(m) {
      var s = 0; kovaKeys.forEach(function(k) { var v = byKM[k + "|" + m]; if (v != null) s += v; });
      return Math.round(s * 100) / 100;
    });
    traces.push({
      type: "scatter", mode: "lines+markers", name: "Total Mix Effect",
      x: months, y: totals,
      line: { color: _plotInk(), width: 2, dash: "dot" },
      marker: { size: 7, color: _plotInk() },
      hovertemplate: "Total: %{y:.1f} bps<extra></extra>",
    });
    renderPlotlyFig("sector-mix-chart", {
      data: traces,
      layout: {
        title: { text: "Mix Effect — Bucket Contributions (" + modeLbl + ", bps)", font: { size: 13 } },
        barmode: "relative",
        yaxis: { title: { text: "Contribution (bps)" } },
        legend: { orientation: "h", y: -0.18 },
        margin: { t: 40, r: 20, b: 60, l: 55 },
      },
    }, 380);
    // ── Detay tablosu ──────────────────────────────────────────────────────
    var pctw = function(p) {
      return p.value == null ? "—" :
        (p.value * 100).toLocaleString("tr-TR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " %";
    };
    var bps1 = function(p) {
      return p.value == null ? "—" :
        (p.value > 0 ? "+" : "") + Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    };
    var _bpsStyle = function(p) {
      if (p.value == null) return null;
      // Pozitif katkı = maliyet YÜKSELTİCİ (dezavantaj) → terracotta; negatif → adaçayı.
      return { fontWeight: 600, color: p.value > 0 ? "#B8826B" : "#7A9B7E" };
    };
    var colDefs = [
      { field: "ay",        headerName: "Month",            width: 100, pinned: "left" },
      { field: "kova",      headerName: "Maturity Bucket",   width: 120, cellStyle: { fontWeight: 600 } },
      { field: "w_bank",    headerName: "Bank W",       width: 110, type: "numericColumn", valueFormatter: pctw },
      { field: "w_sektor",  headerName: "Sector W",      width: 110, type: "numericColumn", valueFormatter: pctw },
      { field: "dw_pp",     headerName: "ΔW (puan)",     width: 112, type: "numericColumn",
        valueFormatter: function(p) { return p.value == null ? "—" : (p.value > 0 ? "+" : "") + Number(p.value).toLocaleString("tr-TR", { maximumFractionDigits: 1 }); } },
      { field: "r_b",       headerName: "R_b (" + modeLbl + " %)", width: 130, type: "numericColumn",
        valueFormatter: function(p) { return p.value == null ? "—" : Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %"; } },
      { field: "katki_bps", headerName: "Contribution (" + modeLbl + ", bps)", width: 148, type: "numericColumn",
        valueFormatter: bps1, cellStyle: _bpsStyle },
    ];
    if (_sectorMixGridApi) {
      _sectorMixGridApi.setGridOption("columnDefs", colDefs);
      _sectorMixGridApi.setGridOption("rowData", rows);
      return;
    }
    _sectorMixGridApi = agGrid.createGrid(gridHost, {
      columnDefs: colDefs,
      rowData: rows,
      defaultColDef: { sortable: true, resizable: true, filter: true },
      headerHeight: 42,
      rowHeight: 30,
      enableCellTextSelection: true,
    });
  }

  // ── Vade Dağılımı: Banka vs Sektör (BDDK_VADE) ──────────────────────────────
  // Üst panel: kova bazında toplam vadeli mevduatın payı (banka vs sektör, %).
  // Alt panel (bitişik, ortak x): Banka − Sektör farkı (puan). Tarih BDDK
  // ay-sonlarından seçilir; mod banka/sektör bakiyesinin hesabını değiştirir
  // (bkz. /api/sector_vade_mix docstring'i). Veri her değişimde refetch edilir.
  var _sectorVadeMode = "monthly";   // "monthly" | "daily"

  function _setSectorVadeMode(mode) {
    if (mode !== "monthly" && mode !== "daily") return;
    if (_sectorVadeMode === mode) return;
    _sectorVadeMode = mode;
    var sw = document.getElementById("sector-vade-mode-switch");
    if (sw) {
      sw.classList.toggle("is-right", mode === "daily");
      sw.querySelectorAll(".hm-lbl").forEach(function(l) {
        l.classList.toggle("active", l.dataset.mode === mode);
      });
    }
    fetchSectorVadeMix();
  }
  window._setSectorVadeMode = _setSectorVadeMode;
  window._toggleSectorVadeMode = function() {
    _setSectorVadeMode(_sectorVadeMode === "monthly" ? "daily" : "monthly");
  };

  function _renderSectorVadeMix(data) {
    var buckets = data.buckets || [];
    var bank = data.bank_pct;        // null olabilir (banka verisi yok)
    var sector = data.sector_pct || [];
    var diff = data.diff_pp;
    var note = document.getElementById("sector-vade-note");
    if (note) {
      var msgs = data.notes || [];
      note.style.display = msgs.length ? "block" : "none";
      note.textContent = msgs.join("  •  ");
    }
    var pctHover = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f}%<extra></extra>";
    var traces = [];
    if (bank) {
      traces.push({ type: "bar", name: "Bank", x: buckets, y: bank,
                    marker: { color: "#D4A574", opacity: 0.9 }, hovertemplate: pctHover,
                    xaxis: "x", yaxis: "y" });
    }
    traces.push({ type: "bar", name: "Sector", x: buckets, y: sector,
                  marker: { color: "#4A6B8A", opacity: 0.9 }, hovertemplate: pctHover,
                  xaxis: "x", yaxis: "y" });
    if (diff) {
      traces.push({ type: "bar", name: "Bank − Sector", x: buckets, y: diff,
                    marker: { color: diff.map(function(v) { return v >= 0 ? "#7A9B7E" : "#B8826B"; }) },
                    hovertemplate: "<b>%{x}</b><br>Bank − Sector: %{y:.2f} pts<extra></extra>",
                    showlegend: false, xaxis: "x", yaxis: "y2" });
    }
    var subTitle = "Maturity Mix — Bank vs Sector  ·  " + (data.date || "")
                 + "  ·  " + (_sectorVadeMode === "monthly" ? "Monthly Averages" : "Daily Evolution");
    renderPlotlyFig("sector-vade-chart", {
      data: traces,
      layout: {
        title: { text: subTitle },
        barmode: "group",
        // Üst panel: paylar; alt panel: fark. Ortak x (alt panel x'e anchor'lı,
        // tık etiketleri yalnız altta görünür — bitişik iki grafik hissi).
        xaxis:  { domain: [0, 1], anchor: "y2" },
        yaxis:  { domain: [0.34, 1], title: { text: "Share (%)", font: { size: 11 } } },
        yaxis2: { domain: [0, 0.26], title: { text: "Difference (pts)", font: { size: 11 } },
                  zeroline: true },
        legend: { orientation: "h", x: 0, y: 1.06 },
        margin: { l: 56, r: 24, t: 56, b: 44 },
        bargap: 0.25,
      },
    }, 560);
  }

  async function fetchSectorVadeMix() {
    var chartHost = document.getElementById("sector-vade-chart");
    if (!chartHost) return;
    var dsel = document.getElementById("sector-vade-date");
    var qs = "?mode=" + encodeURIComponent(_sectorVadeMode)
           + (dsel && dsel.value ? "&date=" + encodeURIComponent(dsel.value) : "");
    try {
      var r = await fetch("/api/sector_vade_mix" + qs);
      var data = await r.json();
      if (!data.ok) { showError("Maturity mix: " + (data.error || "no data received")); return; }
      // Dropdown'ı ilk yanıttan doldur (BDDK ay-sonları); seçim korunur.
      if (dsel && !dsel.options.length && (data.dates || []).length) {
        data.dates.forEach(function(d) {
          var o = document.createElement("option");
          o.value = d; o.textContent = d;
          dsel.appendChild(o);
        });
        dsel.value = data.date;
        dsel.addEventListener("change", fetchSectorVadeMix);
      }
      _renderSectorVadeMix(data);
    } catch (e) {
      showError("Maturity mix error: " + (e.message || String(e)));
    }
  }

  // ── BSC Presentation — tam-ekran sunum motoru ───────────────────────────────
  // Mimari: slide'lar ilgili dashboard bölümlerinin SEÇİLİ parçalarının
  // placeholder bırakılarak sunum kabuğuna TAŞINMASIYLA kurulur (klon değil —
  // tüm listener/davranışlar aynı elemanlarla birebir gelir). Slide değişince /
  // Back'te parçalar placeholder'larına geri döner. Global Monthly/Daily + tarih
  // kontrolleri aktif slide'ın bölüm inputlarına yazıp bölümün fetch'ini çağırır.
  var _bsc = null;   // { slide, mode, moved:[{node,ph}], savedDims, dimsDefaulted, monD0..dlyD1 }
  var _BSC_TITLES = ["Balance Analysis", "Balance to Cost", "Cost Analysis",
                     "Sector Cost Comparison", "Tenor Analysis"];

  function _bscPrefix(slide, mode) {
    var mon = (mode === "monthly");
    return slide === 0 ? (mon ? "ba-mon" : "ba-dly")
         : slide === 4 ? (mon ? "ta-mon" : "ta-dly")
         : slide === 3 ? null   // Sector Cost Comparison — bölüm tarihi/prefix'i yok
         : (mon ? "ca-mon" : "ddd");
  }
  function _bscEl(id) { return document.getElementById(id); }
  function _bscMove(node, host) {
    if (!node || !host) return;
    var inline = /^(SPAN|LABEL|BUTTON|SELECT|INPUT)$/.test(node.tagName);
    var ph = document.createElement(inline ? "span" : "div");
    ph.style.display = "none";
    node.parentNode.insertBefore(ph, node);
    host.appendChild(node);
    _bsc.moved.push({ node: node, ph: ph });
  }
  function _bscRestoreMoved() {
    (_bsc.moved || []).forEach(function(m) {
      if (m.ph.parentNode) { m.ph.parentNode.insertBefore(m.node, m.ph); m.ph.remove(); }
    });
    _bsc.moved = [];
    // Sunuma ait artıklar (drill paneli, tarih slider barı) temizlenir —
    // taşınan her şey yukarıda placeholder'ına döndü, kalan sunumundur.
    ["bsc-ctrls", "bsc-filters", "bsc-content"].forEach(function(id) {
      var h = _bscEl(id);
      if (h) h.innerHTML = "";
    });
  }

  // Slide 2/3 dim default'u: yalnız SUBPRODUCT + CUSTOMER_TYPE (kullanıcı spec'i).
  // Dashboard'ın seçimi saklanır, Back'te geri yüklenir.
  function _bscSyncDimBtns() {
    document.querySelectorAll(".ca-mon-dim-btn").forEach(function(b) {
      b.classList.toggle("active", !!caMonDims[b.dataset.dim]);
    });
    document.querySelectorAll(".ddd-dim-btn").forEach(function(b) {
      b.classList.toggle("active", !!dddDims[b.dataset.dim]);
    });
  }
  function _bscApplyCostDimDefaults() {
    if (_bsc.dimsDefaulted) return;
    _bsc.dimsDefaulted = true;
    _bsc.savedDims = { caMon: Object.assign({}, caMonDims), ddd: Object.assign({}, dddDims) };
    DD_DIMS.forEach(function(d)  { caMonDims[d] = (d === "SUBPRODUCT" || d === "CUSTOMER_TYPE"); });
    DDD_DIMS.forEach(function(d) { dddDims[d]   = (d === "SUBPRODUCT" || d === "CUSTOMER_TYPE"); });
    _bscSyncDimBtns();
  }
  function _bscRestoreCostDims() {
    if (!_bsc || !_bsc.savedDims) return;
    Object.assign(caMonDims, _bsc.savedDims.caMon);
    Object.assign(dddDims, _bsc.savedDims.ddd);
    _bsc.savedDims = null;
    _bscSyncDimBtns();
  }

  function _bscDates(mode) {
    if (mode === "monthly") {
      var s = _bscEl("ca-mon-date0");
      return s ? Array.prototype.map.call(s.options, function(o) { return o.value; }).filter(Boolean) : [];
    }
    return Array.from(dddDateSet || []).sort();
  }
  function _bscD0() { return _bsc.mode === "monthly" ? _bscEl("bsc-date0-mon").value : _bscEl("bsc-date0-dly").value; }
  function _bscD1() { return _bsc.mode === "monthly" ? _bscEl("bsc-date1-mon").value : _bscEl("bsc-date1-dly").value; }
  function _bscStoreGlobalDates() {
    if (!_bsc) return;
    if (_bsc.mode === "monthly") { _bsc.monD0 = _bscEl("bsc-date0-mon").value; _bsc.monD1 = _bscEl("bsc-date1-mon").value; }
    else { _bsc.dlyD0 = _bscEl("bsc-date0-dly").value; _bsc.dlyD1 = _bscEl("bsc-date1-dly").value; }
  }
  function _bscSyncModeInputs() {
    var mon = (_bsc.mode === "monthly");
    _bscEl("bsc-date0-mon").classList.toggle("hidden", !mon);
    _bscEl("bsc-date1-mon").classList.toggle("hidden", !mon);
    _bscEl("bsc-date0-dly").classList.toggle("hidden", mon);
    _bscEl("bsc-date1-dly").classList.toggle("hidden", mon);
    var sw = _bscEl("bsc-mode-switch");
    sw.classList.toggle("is-right", !mon);
    sw.querySelectorAll(".hm-lbl").forEach(function(l) {
      l.classList.toggle("active", l.dataset.mode === _bsc.mode);
    });
  }
  function _bscFillGlobalDates() {
    var months = _bscDates("monthly");
    ["bsc-date0-mon", "bsc-date1-mon"].forEach(function(id) {
      var sel = _bscEl(id);
      sel.innerHTML = "";
      months.forEach(function(d) {
        var o = document.createElement("option");
        o.value = d; o.textContent = d;
        sel.appendChild(o);
      });
    });
    if (_bsc.monD0) _bscEl("bsc-date0-mon").value = _bsc.monD0;
    if (_bsc.monD1) _bscEl("bsc-date1-mon").value = _bsc.monD1;
    var dl = _bscDates("daily");
    ["bsc-date0-dly", "bsc-date1-dly"].forEach(function(id) {
      var inp = _bscEl(id);
      if (dl.length) { inp.min = dl[0]; inp.max = dl[dl.length - 1]; }
    });
    _bscEl("bsc-date0-dly").value = _bsc.dlyD0;
    _bscEl("bsc-date1-dly").value = _bsc.dlyD1;
    _bscSyncModeInputs();
  }

  // Global tarihleri aktif slide'ın bölüm inputlarına yaz + bölümün fetch'i.
  function _bscApplyDates() {
    if (!_bsc) return;
    var pfx = _bscPrefix(_bsc.slide, _bsc.mode);
    if (pfx) {   // Sector Cost Comparison (pfx=null) tarih almaz — tüm seri
      var d0 = _bscD0(), d1 = _bscD1();
      var e0 = _bscEl(pfx + "-date0"), e1 = _bscEl(pfx + "-date1");
      if (e0 && d0) e0.value = d0;
      if (e1 && d1) e1.value = d1;
    }
    _bscFetchSlide();
  }
  function _bscFetchSlide() {
    var mon = (_bsc.mode === "monthly");
    if (_bsc.slide === 0) { if (mon) fetchBalanceMonthly(); else fetchBalanceDaily(); }
    else if (_bsc.slide === 3) {
      // Sector Cost Comparison: veri yüklüyse client-side yeniden çiz, değilse fetch.
      if (_sectorRateRows) _renderSectorRateChart(null); else fetchSectorRates();
      // Kesikli TCMB serileri için outstanding aylık özeti de gereklidir.
      if (!_sectorOsMonRows) fetchSectorOutstandingMonthly();
    } else if (_bsc.slide === 4) {
      if (mon) fetchTenorMonthly(); else fetchTenorDaily();
      _bscFetchVade();
    } else { if (mon) fetchCaMonWaterfalls(); else fetchDailyDepositWaterfalls(); }
  }

  // ── Slide 2: Delta Interest Rate ↔ Delta Balance toggle'ı ──────────────────
  // Default "rate" (Interest Rate Evolution). "bal" seçilirse gösterilen kart
  // Balance Evolution bubble'ına döner (X = Δ balance, başlık backend'den) —
  // her iki grafik de zaten her render'da güncellendiğinden fetch GEREKMEZ,
  // yalnız hangi kartın sunumda olduğu değişir. Tam-ekran açıkken toggle
  // overlay içindeki grafiği yerinde değiştirir (_chartFsSwap).
  function _bscSyncBubMetricSwitch(sw) {
    if (!sw || !_bsc) return;
    sw.classList.toggle("is-right", _bsc.bubMetric === "bal");
    sw.querySelectorAll(".hm-lbl").forEach(function(l) {
      l.classList.toggle("active", l.dataset.mode === _bsc.bubMetric);
    });
  }
  function _bscBuildBubMetricSwitch() {
    var d = document.createElement("div");
    d.className = "hm-switch";
    d.id = "bsc-bub-metric-switch";
    d.style.cssText = "display:flex;align-items:center;gap:8px;margin-left:12px;";
    d.innerHTML =
      '<span class="hm-lbl" data-mode="rate">Delta Interest Rate</span>' +
      '<div class="hm-toggle"><div class="hm-knob"></div></div>' +
      '<span class="hm-lbl" data-mode="bal">Delta Balance</span>';
    d.addEventListener("click", function(ev) {
      var lbl = ev.target.closest(".hm-lbl");
      var m = (lbl && lbl.dataset.mode)
        || (_bsc && _bsc.bubMetric === "rate" ? "bal" : "rate");
      _bscSetBubMetric(m);
    });
    _bscSyncBubMetricSwitch(d);
    return d;
  }

  // ── "Apply Demand Effect" — KGH/BTH O/N'a sıfır-faizli vadesiz varsayımı ────
  // Slide 2 (bubble + wavg) ve Slide 4 (Deposit Interest Rate — Bank vs Sector +
  // Monthly New Business Rate 0-1 M) kontrollerine eklenir. Durum PAYLAŞILIR
  // (_bsc.demandOn / demandPct) — iki slide aynı varsayımı kullanır.
  function _bscBuildDemandCtrl() {
    var wrap = document.createElement("div");
    wrap.className = "bsc-demand-ctrl";
    wrap.style.cssText = "display:inline-flex;align-items:center;gap:8px;margin-left:16px;";
    var lab = document.createElement("label");
    lab.style.cssText = "display:inline-flex;align-items:center;gap:7px;cursor:pointer;margin:0;";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "bsc-demand-cb";
    cb.checked = !!(_bsc && _bsc.demandOn);
    var txt = document.createElement("span");
    txt.textContent = "Apply Demand Effect";
    lab.appendChild(cb); lab.appendChild(txt);
    var inp = document.createElement("input");
    inp.type = "number";
    inp.className = "prisma-select bsc-demand-pct";
    inp.step = "0.1"; inp.min = "0"; inp.max = "100";
    inp.value = (_bsc && _bsc.demandPct != null) ? _bsc.demandPct : 11.4;
    inp.style.width = "64px";
    var pct = document.createElement("span");
    pct.className = "bsc-demand-suffix";
    pct.textContent = "%";
    var _showInp = function(on) {
      inp.style.display = on ? "" : "none";
      pct.style.display = on ? "" : "none";
    };
    _showInp(cb.checked);
    wrap.appendChild(lab); wrap.appendChild(inp); wrap.appendChild(pct);
    cb.addEventListener("change", function() {
      _bsc.demandOn = cb.checked;
      _showInp(cb.checked);
      _bscApplyDemandChange();
    });
    var _commit = function() {
      var v = parseFloat(inp.value);
      if (!isFinite(v) || v < 0) v = 0;
      if (v > 100) v = 100;
      inp.value = v;
      _bsc.demandPct = v;
      if (_bsc.demandOn) _bscApplyDemandChange();
    };
    inp.addEventListener("change", _commit);
    inp.addEventListener("keydown", function(e) {
      if (e.key === "Enter") { e.preventDefault(); inp.blur(); }
    });
    return wrap;
  }
  // Demand durumu değişince AKTİF slide'ın etkilenen verilerini yeniden çek
  // (cache invalidasyonu ile). Slide 2 → bubble; Slide 4 → sector combo + NP tablo.
  function _bscApplyDemandChange() {
    if (!_bsc) return;
    if (_bsc.slide === 1) {
      if (_bsc.mode === "monthly") fetchCaMonWaterfalls();
      else fetchDailyDepositWaterfalls();
    } else if (_bsc.slide === 3) {
      _sectorRateRows = null;
      fetchSectorRates();
      _bscNpTblData = null;
      _bscFetchNpTables();
    }
  }
  // Sunum kartını seçili metriğe eşitle: yanlış kart içerideyse iade edilir,
  // doğrusu taşınır (tam-ekran kapanışında da çağrılır — fs içinde toggle
  // yapıldıysa kart senkronu buraya kalır).
  function _bscSyncBubCard() {
    if (!_bsc || _bsc.slide !== 1) return;
    var pfx = _bscPrefix(1, _bsc.mode);
    var wantEl  = _bscEl(pfx + (_bsc.bubMetric === "bal" ? "-bub-bal" : "-bub-rate"));
    var otherEl = _bscEl(pfx + (_bsc.bubMetric === "bal" ? "-bub-rate" : "-bub-bal"));
    var cont = _bscEl("bsc-content");
    var wantCard  = wantEl && wantEl.closest(".card");
    var otherCard = otherEl && otherEl.closest(".card");
    if (otherCard && cont.contains(otherCard)) {
      var idx = -1;
      _bsc.moved.forEach(function(m, k) { if (m.node === otherCard) idx = k; });
      if (idx >= 0) {
        var m = _bsc.moved[idx];
        if (m.ph.parentNode) { m.ph.parentNode.insertBefore(m.node, m.ph); m.ph.remove(); }
        _bsc.moved.splice(idx, 1);
      }
    }
    if (wantCard && !cont.contains(wantCard)) _bscMove(wantCard, cont);
  }
  function _bscSetBubMetric(metric) {
    if (!_bsc || (metric !== "rate" && metric !== "bal") || _bsc.bubMetric === metric) return;
    _bsc.bubMetric = metric;
    _bscSyncBubMetricSwitch(_bscEl("bsc-bub-metric-switch"));
    var pfx = _bscPrefix(1, _bsc.mode);
    var newEl = _bscEl(pfx + (metric === "bal" ? "-bub-bal" : "-bub-rate"));
    var oldEl = _bscEl(pfx + (metric === "bal" ? "-bub-rate" : "-bub-bal"));
    var ov = document.querySelector(".chart-fs-overlay");
    if (ov && oldEl && ov.contains(oldEl) && window._chartFsSwap) {
      window._chartFsSwap(newEl);       // tam-ekranda: overlay içeriği değişir
    } else {
      _bscSyncBubCard();                // slide görünümünde: kart değişir
    }
    // Yeni görünen grafik yeni kabının boyutuna otursun (client-side, fetch yok).
    var rf = (pfx === "ca-mon") ? _renderCaMonBubbles : _renderDddBubbles;
    requestAnimationFrame(function() { try { rf(); } catch (e) {} });
  }

  // Slide 2'nin tarih slider'ı — tam-ekrandaki modülün aynısı, sunum içinde.
  function _bscInitTimeline() {
    if (!_bsc || _bsc.slide !== 1) return;
    var pfx = _bscPrefix(1, _bsc.mode);
    var host = _bscEl("bsc-content");
    try {
      _tlInit(pfx, host);
      // Bar, min-size barının hemen ALTINA (rate kartının üstüne) alınır.
      var bar = _bscEl(pfx + "-bub-timeline-bar");
      var ms = _bscEl(pfx + "-bub-minsize-bar");
      if (bar && ms && ms.parentNode === host) ms.insertAdjacentElement("afterend", bar);
    } catch (e) {}
  }
  // Grafik tam-ekranı sunum slider'ını söker (_tlInit tekil) — kapanınca geri
  // kur; tam-ekranda metrik toggle'ı kullanıldıysa kartı da senkronla.
  function _bscOnFsClose() {
    if (!_bsc || _bsc.slide !== 1) return;
    _bscSyncBubCard();
    _bscInitTimeline();
  }

  function _bscMoveCostCtrls(pfx, ctrls) {
    var bd = _bscEl(pfx + "-break-dim");
    if (!bd) return;
    var row = bd.parentNode;
    // Satırdaki her şey taşınır — Date(Start)/Date(End) label'ları HARİÇ
    // (tarihleri sunumun global kontrolleri sürer).
    Array.prototype.slice.call(row.children).forEach(function(ch) {
      if (ch.querySelector && ch.querySelector("#" + pfx + "-date0, #" + pfx + "-date1")) return;
      if (ch.id === pfx + "-date0" || ch.id === pfx + "-date1") return;
      _bscMove(ch, ctrls);
    });
  }

  function _bscLeaveSlide() {
    try { _tlTeardown(false); } catch (e) {}
    _bscRestoreMoved();
    var vc = _bscEl("bsc-vade-card");
    if (vc) vc.classList.add("hidden");
    var npc = _bscEl("bsc-np-card");
    if (npc) npc.classList.add("hidden");
    var ntc = _bscEl("bsc-np-tbl-card");
    if (ntc) ntc.classList.add("hidden");
    // Rate Type dropdown'ı yerine döndüyse grafik-köşesi konumunu geri al.
    var rt = _bscEl("sector-rate-conv");
    if (rt && rt.closest("#sector-rate-wrap")) {
      rt.style.cssText = "position:absolute;top:6px;right:10px;z-index:5;";
    }
  }
  function _bscEnterSlide(i) {
    var pfx = _bscPrefix(i, _bsc.mode);
    var ctrls = _bscEl("bsc-ctrls"), filt = _bscEl("bsc-filters"), cont = _bscEl("bsc-content");
    if (i === 0) {
      // Balance Analysis: Decomposition + Detail Dim, filtreler, KPI, Bridge.
      var dsel = _bscEl(pfx + "-decomp"), ssel = _bscEl(pfx + "-second");
      if (dsel) _bscMove(dsel.closest("label"), ctrls);
      if (ssel) _bscMove(ssel.closest("label"), ctrls);
      _bscMove(_bscEl(pfx + "-filters"), filt);
      _bscMove(_bscEl(pfx + "-kpi"), cont);
      var acc = _bscEl("acc-btn-" + pfx + "-bridge");
      if (acc) _bscMove(acc.closest(".accordion"), cont);
    } else if (i === 1) {
      // Balance to Cost: dim butonları + Detailed Dim + Rate Type + bubble
      // filtre paneli + min-size + YALNIZ Interest Rate Evolution bubble'ı +
      // tarih slider'ı (tam-ekran özellik seti).
      _bscApplyCostDimDefaults();
      _bscMoveCostCtrls(pfx, ctrls);
      // Rate Type'ın sağına Delta Interest Rate ↔ Delta Balance toggle'ı
      // (bsc-ctrls her slide çıkışında temizlendiğinden her girişte kurulur).
      ctrls.appendChild(_bscBuildBubMetricSwitch());
      ctrls.appendChild(_bscBuildDemandCtrl());   // Apply Demand Effect (KGH/BTH)
      _bscMove(_bscEl(pfx + "-bub-filters"), filt);
      _bscMove(_bscEl(pfx + "-bub-minsize-bar"), cont);
      _bscSyncBubCard();   // seçili metriğin kartı (default: Interest Rate Evolution)
      _bscInitTimeline();
    } else if (i === 2) {
      // Cost Analysis: aynı üst kontroller + Deposit Rate Waterfall bloğu.
      _bscApplyCostDimDefaults();
      _bscMoveCostCtrls(pfx, ctrls);
      var wfBtn = _bscEl("acc-btn-" + pfx + "-wf");
      if (wfBtn) {
        _bscMove(wfBtn.closest(".accordion"), cont);
        wfBtn.classList.add("open");
        var body = _bscEl("acc-body-" + pfx + "-wf");
        if (body) { body.style.maxHeight = "none"; body.style.overflow = "visible"; }
      }
    } else if (i === 3) {
      // Sector Cost Comparison: combo grafik sarmalayıcısı taşınır; grafiğin
      // köşesindeki Rate Type dropdown'ı slide'ın ÜST kontrol şeridine alınır
      // (iki grafiği de yönetir), yanına NP grafiği için Decomp Dim eklenir.
      var rtLab = document.createElement("label");
      rtLab.textContent = "Rate Type:";
      ctrls.appendChild(rtLab);
      var rtSel = _bscEl("sector-rate-conv");
      if (rtSel) {
        rtSel.style.cssText = "";   // köşe konumu (absolute) şeritte sıfırlanır
        _bscMove(rtSel, ctrls);
      }
      var dcLab = document.createElement("label");
      dcLab.textContent = "Decomp Dim:";
      dcLab.style.marginLeft = "12px";
      ctrls.appendChild(dcLab);
      var dcSel = document.createElement("select");
      dcSel.id = "bsc-np-decomp";
      [["SUB_SEGMENT", "Segment"], ["CUST_TP", "Customer Type"],
       ["TENOR_GRP", "Tenor"], ["AUM_BAND", "AUM Band"]].forEach(function(o) {
        var op = document.createElement("option");
        op.value = o[0]; op.textContent = o[1];
        dcSel.appendChild(op);
      });
      dcSel.value = _bsc.npDecomp || "SUB_SEGMENT";
      dcSel.addEventListener("change", function() {
        _bsc.npDecomp = this.value;
        _bscFetchNpSeries();
      });
      ctrls.appendChild(dcSel);
      // AUM filtresi + gruplama chip'i (ilk NP yanıtından kurulur, sonra kalıcı).
      var aumHost = document.createElement("span");
      aumHost.id = "bsc-np-aum-host";
      aumHost.style.cssText = "display:inline-flex;align-items:center;margin-left:12px;";
      ctrls.appendChild(aumHost);
      _bscRenderNpAumChip();
      ctrls.appendChild(_bscBuildDemandCtrl());   // Apply Demand Effect (KGH/BTH)
      _bscMove(_bscEl("sector-rate-wrap"), cont);
      var npc = _bscEl("bsc-np-card");
      if (npc) npc.classList.remove("hidden");
      var ntc = _bscEl("bsc-np-tbl-card");
      if (ntc) ntc.classList.remove("hidden");
      _bscFetchNpSeries();
      _bscFetchNpTables();
    } else {
      // Tenor Analysis: TENOR/DTM toggle + filtreler + Balance vs Hedge
      // (Maturity Ladder kartı) + sunum varyantı vade dağılımı.
      _bscMove(_bscEl(pfx + "-mode-switch"), ctrls);
      _bscMove(_bscEl(pfx + "-filters"), filt);
      // Grafiklerin ÜSTÜNDE dashboard'daki KPI kartı (WAT t0 → t1, Δ Tenor).
      _bscMove(_bscEl(pfx + "-wat"), cont);
      // Ladder + Δ Balance kartları ortak sarmalayıcıya taşınır — 'Balance vs
      // Hedge' başlığının tam ekranı bu sarmalayıcıyı komple götürür (ikisi
      // birlikte; dashboard'daki accordion gövdesinin sunum muadili).
      var ladWrap = document.createElement("div");
      ladWrap.id = "bsc-ladder-wrap";
      cont.appendChild(ladWrap);
      var lad = _bscEl(pfx + "-ladder");
      if (lad) _bscMove(lad.closest(".card"), ladWrap);
      // Dashboard'daki gibi hemen altında bitişik Δ Balance bar'ı da taşınır.
      var ladD = _bscEl(pfx + "-ladder-delta");
      if (ladD) _bscMove(ladD.closest(".card"), ladWrap);
      var vc = _bscEl("bsc-vade-card");
      if (vc) vc.classList.remove("hidden");
    }
    _bscApplyDates();
    // Taşınan Plotly/Apex grafikleri yeni kabın genişliğine otursun.
    requestAnimationFrame(function() {
      try { window.dispatchEvent(new Event("resize")); } catch (e) {}
      cont.querySelectorAll(".js-plotly-plot").forEach(function(p) {
        try { Plotly.Plots.resize(p); } catch (e) {}
      });
    });
  }
  function _bscShowSlide(i) {
    if (!_bsc) return;
    _bscLeaveSlide();
    _bsc.slide = i;
    _bscEl("bsc-pres").dataset.slide = String(i);
    _bscEl("bsc-slide-label").textContent = (i + 1) + " / 5";
    _bscEl("bsc-slide-title").textContent = (i + 1) + ". " + _BSC_TITLES[i];
    _bscEl("bsc-prev").disabled = (i === 0);
    _bscEl("bsc-next").disabled = (i === 4);
    _bscEnterSlide(i);
  }

  async function _bscOpen() {
    if (_bsc) return;
    _bsc = { slide: 0, mode: "monthly", moved: [], savedDims: null, dimsDefaulted: false,
             bubMetric: "rate",   // Slide 2: "rate" (Interest Rate Evo) | "bal" (Balance Evo)
             npDecomp: "AUM_BAND",   // Slide 4 NP grafiği kırılım boyutu (default AUM)
             npTblBucket: "m0_1",    // Slide 4 tablo grafiği: 0-1 M | 1-3 M
             demandOn: false, demandPct: 11.4,   // Apply Demand Effect (Slide 2/4 paylaşımlı)
             monD0: "", monD1: "", dlyD0: "", dlyD1: "" };
    _bscEl("bsc-pres").classList.remove("hidden");
    // Tema toggle'ını (fixed, z 9999) üst şeride TAŞI — dark/light sunumda da
    // değiştirilebilir; fixed konumu geçici olarak statik yapılır.
    var tt = _bscEl("theme-toggle");
    var tth = _bscEl("bsc-theme-host");
    if (tt && tth) {
      _bsc._ttPh = document.createElement("span");
      _bsc._ttPh.style.display = "none";
      tt.parentNode.insertBefore(_bsc._ttPh, tt);
      _bsc._ttCss = tt.style.cssText;
      tt.style.position = "static";
      tth.appendChild(tt);
    }
    // Tüm bölümlerin tarih listeleri yüklü olsun (selects/options dolu).
    try {
      await Promise.all([ensureCaMonDatesLoaded(), ensureBalanceDatesLoaded(),
                         ensureTenorDatesLoaded(), loadDailyDepositDates()]);
    } catch (e) {}
    if (!_bsc) return;   // yükleme sürerken kapatıldı
    _bsc.monD0 = (_bscEl("ca-mon-date0") || {}).value || "";
    _bsc.monD1 = (_bscEl("ca-mon-date1") || {}).value || "";
    var dl = _bscDates("daily");
    _bsc.dlyD1 = dl[dl.length - 1] || "";
    _bsc.dlyD0 = dl.length > 1 ? dl[dl.length - 2] : _bsc.dlyD1;
    _bscFillGlobalDates();
    _bscShowSlide(0);
  }
  function _bscClose() {
    if (!_bsc) return;
    _bscStoreGlobalDates();
    _bscLeaveSlide();
    _bscRestoreCostDims();
    // Tema toggle'ını sabit köşesine geri koy (stil + DOM konumu).
    var tt = _bscEl("theme-toggle");
    if (tt && _bsc._ttPh && _bsc._ttPh.parentNode) {
      tt.style.cssText = _bsc._ttCss || "";
      _bsc._ttPh.parentNode.insertBefore(tt, _bsc._ttPh);
      _bsc._ttPh.remove();
    }
    _bsc = null;
    _bscEl("bsc-pres").classList.add("hidden");
    // Dashboard'a dönüşte grafikler eski kaplarının genişliğine otursun.
    requestAnimationFrame(function() {
      try { window.dispatchEvent(new Event("resize")); } catch (e) {}
    });
  }
  function _bscSetMode(mode) {
    if (!_bsc || (mode !== "monthly" && mode !== "daily") || _bsc.mode === mode) return;
    _bscStoreGlobalDates();
    _bscLeaveSlide();
    _bsc.mode = mode;
    _bscSyncModeInputs();
    _bscEnterSlide(_bsc.slide);
  }

  // ── Slide 4 (Sector Cost Comparison) alt grafiği: NP faizleri + TCMB + TLREF ──
  // Oran dönüşüm yardımcıları (yüzde giriş/çıkış; act/365 — bubble Rate Type
  // zinciriyle aynı formüller). t = bankanın haftalık wavg NP vadesi (gün).
  function _rcS2C(s, t) {   // simple → yıllık bileşik
    if (s == null || !t || t <= 0) return null;
    var base = 1 + (s / 100) * t / 365;
    if (base <= 0) return null;
    return (Math.pow(base, 365 / t) - 1) * 100;
  }
  function _rcC2S(c, t) {   // yıllık bileşik → simple
    if (c == null || !t || t <= 0) return null;
    return (Math.pow(1 + c / 100, t / 365) - 1) * 365 / t * 100;
  }
  function _rcC2On(c) {     // yıllık bileşik → O/N eşleniği (vadeden bağımsız)
    if (c == null) return null;
    return (Math.pow(1 + c / 100, 1 / 365) - 1) * 365 * 100;
  }
  function _rcOn2C(o) {     // O/N → yıllık bileşik
    if (o == null) return null;
    return (Math.pow(1 + (o / 100) / 365, 365) - 1) * 100;
  }

  var _bscNpData = null;    // /api/bsc_np_rate_series payload'ı (decomp bazlı)
  // AUM chip'i (filtre + Group Selected): _renderBubFilters bileşeniyle. Default
  // merge = 200M üzeri her band "200M+" grubunda; ilk yanıttaki aum_values ile
  // bir kez kurulur, sunum oturumu boyunca yaşar.
  var _bscNpAumMeta = null;      // {AUM: [band,...]}
  var _bscNpAumState = {};       // {AUM: {değer/grup: bool}}
  var _bscNpAumMerges = {};      // {AUM: [{name, members}]}

  function _bscNpAumQS() {
    if (!_bscNpAumMeta) return "";
    var st = _bscNpAumState.AUM || {};
    var groups = (_bscNpAumMerges.AUM || []);
    var byName = {};
    groups.forEach(function(g) { byName[g.name] = g; });
    var allowed = [];
    Object.keys(st).forEach(function(v) {
      if (st[v] === false) return;
      if (byName[v]) byName[v].members.forEach(function(m) { allowed.push(m); });
      else allowed.push(v);
    });
    var qs = "";
    var all = _bscNpAumMeta.AUM || [];
    if (allowed.length && all.some(function(v) { return allowed.indexOf(v) < 0; })) {
      qs += "&filter_AUM_BAND=" + encodeURIComponent(allowed.join("|"));
    }
    var activeGroups = groups.filter(function(g) { return st[g.name] !== false; });
    if (groups.length) {
      qs += "&merges=" + encodeURIComponent(JSON.stringify({ AUM_BAND: groups.map(function(g) {
        return { name: g.name, members: g.members.slice() };
      }) }));
    }
    return qs;
  }
  function _bscRenderNpAumChip() {
    var host = _bscEl("bsc-np-aum-host");
    if (!host || !_bscNpAumMeta) return;
    _renderBubFilters("bsc-np-aum-host", _bscNpAumMeta, _bscNpAumState, _bscNpAumMerges,
                      function() { _bscNpData = null; _bscFetchNpSeries(); });
  }

  async function _bscFetchNpSeries() {
    if (!_bsc || _bsc.slide !== 3) return;
    var dec = _bsc.npDecomp || "AUM_BAND";
    var qs = "?decomp=" + encodeURIComponent(dec) + _bscNpAumQS();
    if (_bscNpData && _bscNpData._qs === qs) { _bscRenderNpChart(); return; }
    try {
      var r = await fetch("/api/bsc_np_rate_series" + qs);
      var d = await r.json();
      if (!_bsc || _bsc.slide !== 3) return;
      if (!d.ok) { showError("NP rate series: " + (d.error || "no data received")); return; }
      d._qs = qs;
      _bscNpData = d;
      // İlk yanıt: AUM chip'ini kur (default: 200M üzeri bandlar '200M+' grubu).
      if (!_bscNpAumMeta && (d.aum_values || []).length) {
        _bscNpAumMeta = { AUM: d.aum_values.slice() };
        var big = d.aum_values.filter(function(v) {
          return ["200M-500M", "500M-1B", "1B+"].indexOf(v) >= 0;
        });
        if (big.length) _bscNpAumMerges.AUM = [{ name: "200M+", members: big }];
        _bscRenderNpAumChip();
        // Default gruplama sorguyu değiştirir → gruplu seriyle yeniden çek.
        _bscNpData = null;
        _bscFetchNpSeries();
        return;
      }
      _bscRenderNpChart();
    } catch (e) {
      showError("NP rate series error: " + (e.message || String(e)));
    }
  }

  function _bscRenderNpChart() {
    var d = _bscNpData;
    var host = _bscEl("bsc-np-chart");
    if (!d || !host || !_bsc || _bsc.slide !== 3) return;
    var mode = (_bscEl("sector-rate-conv") || {}).value || "simple";
    var modeLbl = mode === "on" ? "O/N Equivalent"
                : mode === "compound" ? "Annual Compound" : "Simple";
    var ten = d.tenor || [];
    var r4 = function(v) { return v == null ? null : Math.round(v * 10000) / 10000; };
    // Banka NP: SIMPLE gelir → seçime çevrilir (kendi wavg NP vadesiyle).
    var convBank = function(sv, i) {
      if (sv == null) return null;
      if (mode === "simple") return sv;
      var c = _rcS2C(sv, ten[i]);
      return r4(mode === "on" ? _rcC2On(c) : c);
    };
    // TCMB 1-3M: COMPOUND gelir.
    var convTcmb = function(cv, i) {
      if (cv == null) return null;
      if (mode === "compound") return cv;
      return r4(mode === "on" ? _rcC2On(cv) : _rcC2S(cv, ten[i]));
    };
    // TLREF: O/N gelir.
    var convTlref = function(ov, i) {
      if (ov == null) return null;
      if (mode === "on") return ov;
      var c = _rcOn2C(ov);
      return r4(mode === "compound" ? c : _rcC2S(c, ten[i]));
    };
    var PRISMA_EXT = ["#4A6B8A", "#7A9B7E", "#B8946A", "#8B7BA8", "#6B8FA8",
                      "#9BAE8A", "#A06B6B", "#7B6B95", "#8B95A7", "#D4A574"];
    var tenCD = (d.dates || []).map(function(_, i) {
      return ten[i] != null ? Math.round(ten[i]) + " days" : "—";
    });
    var hov = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f}%<br>wavg NP tenor: %{customdata}<extra></extra>";
    // Önceki görünürlük (legend seçimleri) yeniden çizimde korunur; ilk açılışta
    // AUM kırılımında yalnız "200M+" aktif, diğer bandlar legend'den açılır.
    var prevVis = {};
    if (host.data) host.data.forEach(function(t) { if (t.name) prevVis[t.name] = t.visible; });
    var traces = (d.bands || []).map(function(b, bi) {
      var vis = prevVis[b] !== undefined ? prevVis[b]
              : (d.decomp === "AUM_BAND" ? (b === "200M+" ? true : "legendonly") : true);
      return { type: "scatter", mode: "lines", name: b,
               x: d.dates, y: (d.rates[b] || []).map(convBank),
               visible: vis,
               line: { color: PRISMA_EXT[bi % PRISMA_EXT.length], width: 1.8 },
               customdata: tenCD, hovertemplate: hov, connectgaps: true };
    });
    traces.push({ type: "scatter", mode: "lines", name: "TCMB 1-3M",
                  x: d.dates, y: (d.tcmb_comp || []).map(convTcmb),
                  line: { color: "#5C6478", width: 2.5, dash: "dash" },
                  customdata: tenCD, hovertemplate: hov, connectgaps: true });
    traces.push({ type: "scatter", mode: "lines", name: "TLREF",
                  x: d.dates, y: (d.tlref_on || []).map(convTlref),
                  line: { color: "#B8826B", width: 2.5, dash: "dot" },
                  customdata: tenCD, hovertemplate: hov, connectgaps: true });
    renderPlotlyFig("bsc-np-chart", {
      data: traces,
      layout: {
        title: { text: "New Business Interest Rate — Bank vs TCMB 1-3M vs TLREF  ·  " + modeLbl },
        yaxis: { title: { text: "Rate (%)", font: { size: 11 } } },
        // Legend başlığın ALTINA (bir çıt aşağı) — üst marj legend'a yer açar.
        legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom" },
        margin: { l: 56, r: 24, t: 88, b: 44 },
        // Not: x-unified hover kaldırıldı — beyaz referans çubuğu PRISMA dışıydı;
        // standart hover kutusu tema (koyu panel) stiliyle gelir.
      },
    }, 520);
  }

  // ── Slide 4 üst tabloları: aylık Banka NP vs TCMB (0-1 M / 1-3 M) ──────────
  var _bscNpTblData = null;
  var _bscNpTblApis = {};   // gridId → agGrid api

  async function _bscFetchNpTables() {
    if (!_bsc || _bsc.slide !== 3) return;
    if (_bscNpTblData) { _bscRenderNpTables(); return; }
    try {
      var r = await fetch("/api/bsc_np_monthly_table?_=1" + _bscDemandQS());
      var d = await r.json();
      if (!_bsc || _bsc.slide !== 3) return;
      if (!d.ok) { showError("NP monthly table: " + (d.error || "no data received")); return; }
      _bscNpTblData = d;
      _bscRenderNpTables();
    } catch (e) {
      showError("NP monthly table error: " + (e.message || String(e)));
    }
  }

  // API v2 sarmalını çözer: {rows, tcmb_last} veya düz dizi (eski app.py) kabul.
  function _bscNpTblRows(key) {
    var d = _bscNpTblData;
    var v = d && d[key];
    if (!v) return [];
    return Array.isArray(v) ? v : (v.rows || []);
  }

  function _bscRenderNpTables() {
    var d = _bscNpTblData;
    if (!d || typeof agGrid === "undefined") return;
    var mode = (_bscEl("sector-rate-conv") || {}).value || "simple";
    var mkRows = function(rows) {
      return (rows || []).map(function(r) {
        var bank = null;
        if (r.bank_simple != null) {
          if (mode === "simple") bank = r.bank_simple;
          else {
            var c = _rcS2C(r.bank_simple, r.bank_tenor);
            bank = (mode === "on") ? _rcC2On(c) : c;
          }
        }
        var sec = null;
        if (r.tcmb_comp != null) {
          if (mode === "compound") sec = r.tcmb_comp;
          else sec = (mode === "on") ? _rcC2On(r.tcmb_comp) : _rcC2S(r.tcmb_comp, r.bank_tenor);
        }
        return {
          month: r.month,
          bank_rate: bank == null ? null : Math.round(bank * 10000) / 10000,
          sector_rate: sec == null ? null : Math.round(sec * 10000) / 10000,
          spread: (bank != null && sec != null)
            ? Math.round((bank - sec) * 10000) / 10000 : null,
          bank_upto: r.bank_upto || null,
        };
      });
    };
    var pct2 = function(p) {
      return p.value == null ? "—" :
        Number(p.value).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
    };
    var spreadStyle = function(p) {
      if (p.value == null) return null;
      return { fontWeight: 600, color: p.value >= 0 ? "var(--positive,#7A9B7E)" : "var(--negative,#B8826B)" };
    };
    var colDefs = [
      { field: "month",       headerName: "Date",            width: 110, pinned: "left",
        // Kapsam denetimi: kısmi ayda bankanın hangi güne kadar dahil olduğunu gösterir.
        tooltipValueGetter: function(p) {
          return (p.data && p.data.bank_upto)
            ? "Bank data through " + p.data.bank_upto : null;
        } },
      { field: "bank_rate",   headerName: "Bank Rate (%)",   flex: 1, type: "numericColumn", valueFormatter: pct2 },
      { field: "sector_rate", headerName: "Sector Rate (%)", flex: 1, type: "numericColumn", valueFormatter: pct2 },
      { field: "spread",      headerName: "Spread",          flex: 1, type: "numericColumn",
        valueFormatter: pct2, cellStyle: spreadStyle },
    ];
    _bscRenderNpTblChart(mkRows);
    [["bsc-np-tbl-01", _bscNpTblRows("m0_1")],
     ["bsc-np-tbl-13", _bscNpTblRows("m1_3")]].forEach(function(pair) {
      var host = _bscEl(pair[0]);
      if (!host) return;
      var rows = mkRows(pair[1]);
      if (_bscNpTblApis[pair[0]]) {
        _bscNpTblApis[pair[0]].setGridOption("rowData", rows);
        return;
      }
      _bscNpTblApis[pair[0]] = agGrid.createGrid(host, {
        columnDefs: colDefs,
        rowData: rows,
        defaultColDef: { sortable: true, resizable: true },
        headerHeight: 38,
        rowHeight: 28,
        enableCellTextSelection: true,
        tooltipShowDelay: 300,
      });
    });
  }

  function _bscSetNpTblBucket(b) {
    if (!_bsc || (b !== "m0_1" && b !== "m1_3") || _bsc.npTblBucket === b) return;
    _bsc.npTblBucket = b;
    var sw = _bscEl("bsc-np-tbl-switch");
    if (sw) {
      sw.classList.toggle("is-right", b === "m1_3");
      sw.querySelectorAll(".hm-lbl").forEach(function(l) {
        l.classList.toggle("active", l.dataset.mode === b);
      });
    }
    _bscRenderNpTables();   // grafik dahil yeniden çizer (fetch yok)
  }

  // Tabloların altındaki combo grafik: seçili kovanın oran çizgileri + spread barı.
  // mkRows, _bscRenderNpTables'ın mod-çevrimli satır üreticisidir (aynı sayılar).
  function _bscRenderNpTblChart(mkRows) {
    var d = _bscNpTblData;
    var host = _bscEl("bsc-np-tbl-chart");
    if (!d || !host || !_bsc || _bsc.slide !== 3) return;
    var bucket = _bsc.npTblBucket || "m0_1";
    var bucketLbl = bucket === "m1_3" ? "1-3 M" : "0-1 M";
    var mode = (_bscEl("sector-rate-conv") || {}).value || "simple";
    var modeLbl = mode === "on" ? "O/N Equivalent"
                : mode === "compound" ? "Annual Compound" : "Simple";
    var rows = mkRows(_bscNpTblRows(bucket)).slice().reverse();  // ay artan
    var months = rows.map(function(r) { return r.month; });
    var yB = rows.map(function(r) { return r.bank_rate; });
    var yS = rows.map(function(r) { return r.sector_rate; });
    var sp = rows.map(function(r) { return r.spread; });
    var lbl2 = function(arr) {
      return arr.map(function(v) { return v == null ? "" : v.toFixed(2); });
    };
    var pctHover = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f}%<extra></extra>";
    renderPlotlyFig("bsc-np-tbl-chart", {
      data: [
        { type: "scatter", mode: "lines+markers+text", name: "Bank",
          x: months, y: yB, text: lbl2(yB), textposition: "top center",
          textfont: { size: 10 }, cliponaxis: false,
          line: { color: "#D4A574", width: 2.5 }, marker: { size: 6 },
          hovertemplate: pctHover, yaxis: "y" },
        { type: "scatter", mode: "lines+markers+text", name: "Sector (TCMB)",
          x: months, y: yS, text: lbl2(yS), textposition: "bottom center",
          textfont: { size: 10 }, cliponaxis: false,
          line: { color: "#4A6B8A", width: 2 }, marker: { size: 5 },
          hovertemplate: pctHover, yaxis: "y" },
        { type: "bar", name: "Bank − Sector",
          x: months, y: sp,
          marker: { color: sp.map(function(v) { return v != null && v >= 0 ? "#7A9B7E" : "#B8826B"; }),
                    opacity: 0.85 },
          text: lbl2(sp), textposition: "outside", textfont: { size: 10 },
          cliponaxis: false, showlegend: false,
          hovertemplate: "<b>%{x}</b><br>Bank − Sector: %{y:.2f}<extra></extra>", yaxis: "y2" },
      ],
      layout: {
        title: { text: "Monthly New Business Rate — Bank vs TCMB (" + bucketLbl + ")  ·  " + modeLbl },
        xaxis:  { domain: [0, 1], anchor: "y2", type: "category",
                  categoryorder: "category ascending" },
        yaxis:  { domain: [0.36, 1], title: { text: "Rate (%)", font: { size: 11 } } },
        yaxis2: { domain: [0, 0.26], title: { text: "Spread", font: { size: 11 } }, zeroline: true },
        legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom" },
        margin: { l: 56, r: 24, t: 88, b: 44 },
        bargap: 0.35,
      },
    }, 500);
  }

  // Slide 4 — sunum varyantı vade dağılımı (Sektör + Banka×2 tarih).
  async function _bscFetchVade() {
    if (!_bsc || _bsc.slide !== 4) return;
    var qs = "?mode=" + encodeURIComponent(_bsc.mode)
           + "&date_end=" + encodeURIComponent(_bscD1() || "");
    try {
      var r = await fetch("/api/sector_vade_mix_pres" + qs);
      var data = await r.json();
      if (!_bsc || _bsc.slide !== 4) return;
      if (!data.ok) { showError("Maturity mix (presentation): " + (data.error || "no data received")); return; }
      _bscRenderVade(data);
    } catch (e) {
      showError("Maturity mix (presentation) error: " + (e.message || String(e)));
    }
  }
  function _bscRenderVade(data) {
    var note = _bscEl("bsc-vade-note");
    if (note) {
      var msgs = data.notes || [];
      note.style.display = msgs.length ? "block" : "none";
      note.textContent = msgs.join("  •  ");
    }
    var buckets = data.buckets || [];
    var pctHover = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f}%<extra></extra>";
    var dHover = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f} pts<extra></extra>";
    // KALICI bar etiketleri (hover beklenmez) — 1 hane, dışa doğru.
    var lbl1 = function(arr) {
      return (arr || []).map(function(v) { return v == null ? "" : Number(v).toFixed(1); });
    };
    var lblCfg = { textposition: "outside", textfont: { size: 10 }, cliponaxis: false };
    // Banka barlarının hover eki: kovanın payını sektöre eşitlemek için gereken
    // mevduat (milyar TL; + almalı / − çıkarmalı). Bu Plotly derlemesi
    // %{...:+.1f} format'ını ayrıştıramadığından string'e ÖN-formatlanır.
    var fmtGap = function(arr) {
      return (arr || []).map(function(v) {
        if (v == null || isNaN(v)) return "—";
        return (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1) + " billion TRY";
      });
    };
    var gapLine = "<br>To match sector share: %{customdata}";
    var pctHoverGap = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f}%" + gapLine + "<extra></extra>";
    var dHoverGap = "<b>%{x}</b><br>%{fullData.name}: %{y:.2f} puan" + gapLine + "<extra></extra>";
    // PRISMA paleti: A = sektör tarihli banka (soluk amber --cat-5), B = Date(End)
    // banka (amber accent), S = sektör (denim --chart-absolute). İki temada da okunur.
    var CB_A = "#B8946A", CB_B = "#D4A574", CS = "#4A6B8A";
    var traces = [];
    if (data.bank_a_pct) {
      traces.push(Object.assign({ type: "bar", name: "Bank (" + (data.bank_a_info || data.sector_date) + ")",
                    x: buckets, y: data.bank_a_pct, marker: { color: CB_A, opacity: 0.9 },
                    customdata: fmtGap(data.gap_a_bn), text: lbl1(data.bank_a_pct),
                    hovertemplate: pctHoverGap, yaxis: "y" }, lblCfg));
    }
    if (data.bank_b_pct) {
      traces.push(Object.assign({ type: "bar", name: "Bank (" + (data.bank_b_info || data.date_end) + ")",
                    x: buckets, y: data.bank_b_pct, marker: { color: CB_B, opacity: 0.9 },
                    customdata: fmtGap(data.gap_b_bn), text: lbl1(data.bank_b_pct),
                    hovertemplate: pctHoverGap, yaxis: "y" }, lblCfg));
    }
    traces.push(Object.assign({ type: "bar", name: "Sector (" + (data.sector_date_info || data.sector_date || "") + ")",
                  x: buckets, y: data.sector_pct || [], marker: { color: CS, opacity: 0.9 },
                  text: lbl1(data.sector_pct),
                  hovertemplate: pctHover, yaxis: "y" }, lblCfg));
    if (data.diff_a_pp) {
      traces.push(Object.assign({ type: "bar", name: "Bank(" + (data.bank_a_info || "") + ") − Sector",
                    x: buckets, y: data.diff_a_pp, marker: { color: CB_A, opacity: 0.8 },
                    customdata: fmtGap(data.gap_a_bn), text: lbl1(data.diff_a_pp),
                    hovertemplate: dHoverGap, showlegend: false, yaxis: "y2" }, lblCfg));
    }
    if (data.diff_b_pp) {
      traces.push(Object.assign({ type: "bar", name: "Bank(" + (data.bank_b_info || "") + ") − Sector",
                    x: buckets, y: data.diff_b_pp, marker: { color: CB_B, opacity: 0.8 },
                    customdata: fmtGap(data.gap_b_bn), text: lbl1(data.diff_b_pp),
                    hovertemplate: dHoverGap, showlegend: false, yaxis: "y2" }, lblCfg));
    }
    renderPlotlyFig("bsc-vade-chart", {
      data: traces,
      layout: {
        title: { text: "Maturity Mix — Bank vs Sector  ·  Date(End) " + (data.date_end || "") },
        barmode: "group",
        xaxis:  { domain: [0, 1], anchor: "y2" },
        yaxis:  { domain: [0.34, 1], title: { text: "Share (%)", font: { size: 11 } } },
        yaxis2: { domain: [0, 0.26], title: { text: "Difference (pts)", font: { size: 11 } }, zeroline: true },
        legend: { orientation: "h", x: 0, y: 1.07 },
        margin: { l: 56, r: 24, t: 56, b: 44 },
        bargap: 0.25,
      },
    }, 520);
  }

  // Kabuk kontrolleri (statik HTML — init'te bir kez bağlanır).
  (function _bscWire() {
    var back = _bscEl("bsc-back");
    if (!back) return;
    back.addEventListener("click", _bscClose);
    _bscEl("bsc-prev").addEventListener("click", function() {
      if (_bsc && _bsc.slide > 0) _bscShowSlide(_bsc.slide - 1);
    });
    _bscEl("bsc-next").addEventListener("click", function() {
      if (_bsc && _bsc.slide < 4) _bscShowSlide(_bsc.slide + 1);
    });
    ["bsc-date0-mon", "bsc-date1-mon", "bsc-date0-dly", "bsc-date1-dly"].forEach(function(id) {
      var el = _bscEl(id);
      if (el) el.addEventListener("change", function() {
        if (_bsc) { _bscStoreGlobalDates(); _bscApplyDates(); }
      });
    });
    // Bölüm Date(End)'ine dışarıdan gelen değişimi (bubble tarih slider'ı
    // commit'i) sunumun global Date(End)'ine yansıt.
    document.addEventListener("change", function(ev) {
      if (!_bsc || !ev.target || !ev.target.id) return;
      var pfx = _bscPrefix(_bsc.slide, _bsc.mode);
      if (ev.target.id !== pfx + "-date1") return;
      var v = ev.target.value;
      if (_bsc.mode === "monthly") {
        var s = _bscEl("bsc-date1-mon");
        if (s && v) s.value = v;
        _bsc.monD1 = v;
      } else {
        _bscEl("bsc-date1-dly").value = v;
        _bsc.dlyD1 = v;
      }
      if (_bsc.slide === 4) _bscFetchVade();
    });
    // Esc sunumu kapatır — ama grafik tam-ekranı açıksa ÖNCE o kapanır.
    document.addEventListener("keydown", function(ev) {
      if (ev.key === "Escape" && _bsc && !document.querySelector(".chart-fs-overlay")) _bscClose();
    });
  })();

  async function fetchSectorMixAttribution() {
    var chartHost = document.getElementById("sector-mix-chart");
    if (!chartHost || typeof agGrid === "undefined") return;
    try {
      var r = await fetch("/api/sector_mix_attribution");
      var data = await r.json();
      if (!data.ok) { showError("Mix attribution: " + (data.error || "no data received")); return; }
      _sectorMixRows = data.rows || [];
      // BEKÇİLER (manuel dosya dağıtımı teşhisi — sessiz kalma):
      // 1) Sayfada bölüm mükerrer ise render GÖRÜNMEYEN ilk kopyaya gider.
      if (document.querySelectorAll("#sector-mix-mode-switch").length > 1) {
        showError("index.html contains the 'sector-mix' section MORE THAN ONCE (possibly a " +
                  "manual-merge leftover) — replace the file entirely with the current GitHub version.");
      }
      // 2) Backend simple alanlarını döndürmüyorsa app.py/engine eski demektir.
      if (_sectorMixRows.length &&
          _sectorMixRows.every(function(x) { return x.katki_s_bps == null; })) {
        showError("Mix attribution: backend does not return katki_s_bps — also update " +
                  "app.py and engine/sector_data.py to their CURRENT versions and " +
                  "restart Flask (index.html alone is not enough).");
      }
      _renderSectorMix();
    } catch (e) {
      showError("Mix attribution error: " + (e.message || String(e)));
    }
  }

  function _renderBaFilterPanels() {
    if (!baFilterMeta || !Object.keys(baFilterMeta).length) return;
    _renderBubFilters("ba-mon-filters", baFilterMeta, baMonBubState, baMonBubMerges,
      function() { fetchBalanceMonthly(); });
    _renderBubFilters("ba-dly-filters", baFilterMeta, baDlyBubState, baDlyBubMerges,
      function() { fetchBalanceDaily(); });
  }

  async function fetchBalanceMonthly() {
    var sel0 = document.getElementById("ba-mon-date0");
    var sel1 = document.getElementById("ba-mon-date1");
    var dsel = document.getElementById("ba-mon-decomp");
    if (!sel0 || !sel1 || !dsel) return;
    var d0 = sel0.value, d1 = sel1.value, decomp = dsel.value;
    if (!d0 || !d1 || d0 === d1) return;
    try {
      var url = "/api/balance_monthly?date_0=" + encodeURIComponent(d0)
              + "&date_1=" + encodeURIComponent(d1)
              + "&decomp=" + encodeURIComponent(decomp)
              + "&decomp2=" + encodeURIComponent(_baDecomp2("ba-mon"))
              + _balanceBubStateToQuery(baMonBubState, baMonBubMerges);
      var r = await fetch(url);
      var data = await r.json();
      if (!data.ok) return;
      baMonPayload = data;
      _renderBalanceSnapshot(data, "ba-mon", false, d0, d1);
    } catch(e) { /* silent */ }
  }

  async function fetchBalanceDaily() {
    var inp0 = document.getElementById("ba-dly-date0");
    var inp1 = document.getElementById("ba-dly-date1");
    var dsel = document.getElementById("ba-dly-decomp");
    if (!inp0 || !inp1 || !dsel) return;
    var d0 = inp0.value, d1 = inp1.value, decomp = dsel.value;
    if (!d0 || !d1 || d0 === d1) return;
    if (baDailyDateSet && baDailyDateSet.size > 0 &&
        (!baDailyDateSet.has(d0) || !baDailyDateSet.has(d1))) {
      _showBalanceDlyWarning("One of the selected dates is not in the dataset.");
      return;
    } else { _showBalanceDlyWarning(""); }
    try {
      var url = "/api/balance_daily?date_0=" + encodeURIComponent(d0)
              + "&date_1=" + encodeURIComponent(d1)
              + "&decomp=" + encodeURIComponent(decomp)
              + "&decomp2=" + encodeURIComponent(_baDecomp2("ba-dly"))
              + _balanceBubStateToQuery(baDlyBubState, baDlyBubMerges);
      var r = await fetch(url);
      var data = await r.json();
      if (!data.ok) return;
      baDlyPayload = data;
      _renderBalanceSnapshot(data, "ba-dly", true, d0, d1);
    } catch(e) { /* silent */ }
  }

  function _showBalanceDlyWarning(msg) {
    var w = document.getElementById("ba-dly-warning");
    if (!w) return;
    if (!msg) { w.classList.add("hidden"); w.textContent = ""; return; }
    w.classList.remove("hidden"); w.textContent = msg;
  }

  function _fmtSignedM(v, decimals) {
    if (v == null) return "–";
    decimals = decimals == null ? 1 : decimals;
    var sign = v >= 0 ? "+" : "";
    return sign + v.toLocaleString("tr-TR", {minimumFractionDigits: decimals, maximumFractionDigits: decimals});
  }

  // Attach a double-click detector (two single clicks ≤ 450 ms) on a Plotly chart.
  // getValFn(point) → category string | null
  // drillOpts: { drillDim, extraDimFn(pt), extraValueFn(pt), labelFn(pt) }
  function _attachBalancePlotlyDblClick(elId, prefix, getValFn, drillOpts) {
    var domEl = document.getElementById(elId);
    if (!domEl || typeof domEl.on !== "function") return;
    try { domEl.removeAllListeners("plotly_click"); } catch(e) {}
    var lastMs = 0, lastVal = null;
    domEl.on("plotly_click", function(ev) {
      if (!ev || !ev.points || !ev.points.length) return;
      var pt  = ev.points[0];
      var val = getValFn(pt);
      if (!val || val === "Other") return;
      var now = Date.now();
      if (now - lastMs < 450 && lastVal === val) {
        lastMs = 0; lastVal = null;
        var opts = drillOpts ? {
          drillDim:   drillOpts.drillDim,
          extraDim:   drillOpts.extraDimFn   ? drillOpts.extraDimFn(pt)   : "",
          extraValue: drillOpts.extraValueFn ? drillOpts.extraValueFn(pt) : "",
          label:      drillOpts.labelFn      ? drillOpts.labelFn(pt)      : val,
        } : {};
        _showBalanceDrill(val, prefix, elId, opts);
      } else {
        lastMs = now; lastVal = val;
      }
    });
  }

  // Fetch and render a daily balance line chart below the clicked chart's card.
  // opts: { drillDim, extraDim, extraValue, label }
  async function _showBalanceDrill(drillValue, prefix, anchorId, opts) {
    opts = opts || {};
    var isMonthly = prefix === "ba-mon";
    var d0El = document.getElementById(prefix + "-date0");
    var d1El = document.getElementById(prefix + "-date1");
    if (!d0El || !d1El) return;

    var d0 = d0El.value, d1 = d1El.value;
    if (!d0 || !d1) return;

    // Monthly: expand to full month boundaries — first day of d0's month
    // → last day of d1's month. Robust to "YYYY-MM-DD" and "YYYY-MM" formats.
    if (isMonthly) {
      var m0 = String(d0).match(/^(\d{4})-(\d{2})/);
      var m1 = String(d1).match(/^(\d{4})-(\d{2})/);
      if (m0 && m1) {
        var y0 = m0[1], mo0 = m0[2];
        var y1 = parseInt(m1[1], 10), mo1 = parseInt(m1[2], 10);
        // Date(y, monthIndex+1, 0) → last day of monthIndex's month
        var lastDay = new Date(y1, mo1, 0).getDate();
        d0 = y0 + "-" + mo0 + "-01";
        d1 = m1[1] + "-" + m1[2] + "-" + String(lastDay).padStart(2, "0");
      }
    }

    var decompEl   = document.getElementById(prefix + "-decomp");
    var drillDim   = opts.drillDim   || (decompEl ? decompEl.value : "SEGMENT");
    var extraDim   = opts.extraDim   || "";
    var extraValue = opts.extraValue || "";
    var label      = opts.label      || drillValue;
    // Alt-kırılım boyutu = Second Dim (opts ile override edilebilir). Kırılım
    // metriği: "balance" (varsayılan) veya "customer" (Customer Number heatmap).
    var breakDim   = opts.breakDim   || _baSecond(prefix);
    var barKind    = opts.barKind    || "balance";
    var labelFull  = label + (extraValue ? " × " + extraValue : "");
    var dateRange  = d0 + " → " + d1;

    var bubState  = isMonthly ? baMonBubState : baDlyBubState;
    var bubMerges = isMonthly ? baMonBubMerges : baDlyBubMerges;

    var drillId = "ba-drill-" + anchorId.replace(/[^a-zA-Z0-9]/g, "-");
    var prevEl  = document.getElementById(drillId);
    if (prevEl) prevEl.remove();

    var anchorEl = document.getElementById(anchorId);
    if (!anchorEl) return;
    var cardEl = anchorEl.closest(".card") || anchorEl;

    // Alt-kırılım Second Dim'e göre yapılır; drill boyutu ile aynıysa tek bar
    // olacağından atlanır.
    var showBar = !!opts.showProductBar && drillDim !== breakDim;
    var breakTitle = _baDimLabel(breakDim) + " Breakdown"
      + (barKind === "customer" ? " (Customer Number)" : "");

    // Derive (segment, aum, cust_tp) filters for the product-bar endpoint from the
    // drill context if the caller did not pass them explicitly. Heatmap callers set
    // barSeg/barAum directly; other handlers (bridge/ranked/mix/...) rely on the
    // drillDim → drillValue mapping below. null = no filter for that dim.
    var _segCandidates = ["SEGMENT"];
    var _aumCandidates = ["AUM"];
    var _custCandidates = ["CUSTOMER_TYPE", "CUST_TP"];
    function _resolveFilter(dimNames) {
      if (dimNames.indexOf(drillDim) >= 0) return drillValue;
      if (extraDim && dimNames.indexOf(extraDim) >= 0) return extraValue;
      return null;
    }
    var barSegResolved = opts.hasOwnProperty("barSeg")
      ? opts.barSeg : _resolveFilter(_segCandidates);
    var barAumResolved = opts.hasOwnProperty("barAum")
      ? opts.barAum : _resolveFilter(_aumCandidates);
    var barCustResolved = opts.hasOwnProperty("barCustTp")
      ? opts.barCustTp : _resolveFilter(_custCandidates);
    // PRODUCT / SUBPRODUCT satır boyutu drill'i → o değere filtrele (breakdown
    // Second Dim'e göre bu hücre içinde kırılır).
    var barProdResolved = opts.hasOwnProperty("barProd")
      ? opts.barProd : _resolveFilter(["PRODUCT"]);
    var barSubpResolved = opts.hasOwnProperty("barSubp")
      ? opts.barSubp : _resolveFilter(["SUBPRODUCT"]);
    var drillRow = document.createElement("div");
    drillRow.id = drillId;
    drillRow.className = "card";
    drillRow.style.cssText = "position:relative;margin-top:14px;padding:16px;width:100%;box-sizing:border-box;";
    drillRow.innerHTML =
      '<button onclick="document.getElementById(\'' + drillId + '\').remove()" '
      + 'style="position:absolute;top:8px;right:10px;background:none;border:none;'
      + 'cursor:pointer;font-size:16px;color:var(--text-secondary);z-index:2;" title="Kapat">✕</button>'
      + '<div style="font-size:12px;font-weight:600;color:var(--text-primary);margin-bottom:8px;">'
      + 'Daily Balance — <span style="color:var(--accent);">' + labelFull + '</span>'
      + ' <span style="font-size:11px;color:var(--text-secondary);font-weight:400;">(' + dateRange + ')</span></div>'
      + '<div id="' + drillId + '-chart" style="height:260px;width:100%;display:flex;align-items:center;'
      + 'justify-content:center;color:var(--text-secondary);font-size:13px;">Loading…</div>'
      + (showBar
          ? '<div style="height:1px;background:rgba(255,255,255,0.07);margin:10px 0;"></div>'
            + '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">' + breakTitle + '</div>'
            + '<div id="' + drillId + '-bar" style="min-height:80px;width:100%;display:flex;align-items:center;'
            + 'justify-content:center;color:var(--text-secondary);font-size:12px;">Loading…</div>'
          : "");
    cardEl.insertAdjacentElement("afterend", drillRow);
    drillRow.scrollIntoView({ behavior: "smooth", block: "nearest" });

    var url = "/api/balance_drill"
      + "?date_0="      + encodeURIComponent(d0)
      + "&date_1="      + encodeURIComponent(d1)
      + "&drill_dim="   + encodeURIComponent(drillDim)
      + "&drill_value=" + encodeURIComponent(drillValue)
      + (extraDim   ? "&extra_dim="   + encodeURIComponent(extraDim)   : "")
      + (extraValue ? "&extra_value=" + encodeURIComponent(extraValue) : "")
      + _balanceBubStateToQuery(bubState, bubMerges);

    try {
      var r    = await fetch(url);
      var data = await r.json();
      var chartEl = document.getElementById(drillId + "-chart");
      if (!data.ok || !data.dates || !data.dates.length) {
        if (chartEl) chartEl.textContent = "No daily data found for this selection.";
      } else {
        chartEl.style.cssText = "height:260px;width:100%;";
        chartEl.innerHTML = "";

        // Sensitive y-axis: padded around min/max instead of anchored at zero.
        // Pad %15 + smoothing 0.65: spline eğrisi tepe noktalarda overshoot
        // yapar; %8 pad'de eğri ekseni aşıp kırpılıyordu.
        var yMin = Math.min.apply(null, data.balance_m);
        var yMax = Math.max.apply(null, data.balance_m);
        var ySpan = yMax - yMin;
        var yPad = ySpan > 0 ? ySpan * 0.15 : Math.max(Math.abs(yMax) * 0.02, 1);
        var yLo  = yMin - yPad;
        var yHi  = yMax + yPad;

        Plotly.react(chartEl, [{
          x: data.dates, y: data.balance_m,
          type: "scatter", mode: "lines+markers",
          name: labelFull,
          line:   { color: "#D4A574", width: 2, shape: "spline", smoothing: 0.65 },
          marker: { size: 4, color: "#D4A574" },
          connectgaps: true,
          hovertemplate: "%{x}<br><b>%{y:,.0f} ₺M</b><extra>" + labelFull + "</extra>",
        }], {
          autosize: true,
          height: 260,
          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
          font: { family: "system-ui,-apple-system,sans-serif", size: 12, color: "#E4E8F0" },
          xaxis: { gridcolor: "rgba(255,255,255,0.06)", color: "#7A8399", automargin: true },
          yaxis: { gridcolor: "rgba(255,255,255,0.06)", color: "#7A8399",
                   title: "Balance (₺M)", tickformat: ",.0f",
                   range: [yLo, yHi], autorange: false, zeroline: false,
                   automargin: true },
          margin: { l: 70, r: 20, t: 8, b: 50 },
          separators: ",.",
        }, _plotlyConfig);
        requestAnimationFrame(function() { try { Plotly.Plots.resize(chartEl); } catch(_) {} });
      }
      // Always render the product bar regardless of line chart success.
      // Preserve null (= Total, no filter) vs "" (= actual empty-string value).
      if (showBar) {
        _renderHmProductBar(drillId, opts.origD0 || d0El.value, opts.origD1 || d1El.value,
          barSegResolved, barAumResolved, barKind, prefix, barCustResolved, breakDim,
          barProdResolved, barSubpResolved);
      }
    } catch(e) {
      var c = document.getElementById(drillId + "-chart");
      if (c) c.textContent = "Data could not be loaded.";
      if (showBar) {
        _renderHmProductBar(drillId, opts.origD0 || d0El.value, opts.origD1 || d1El.value,
          barSegResolved, barAumResolved, barKind, prefix, barCustResolved, breakDim,
          barProdResolved, barSubpResolved);
      }
    }
  }

  function _renderBalanceSnapshot(payload, prefix, isDaily, d0, d1) {
    var cats = payload.categories || [];
    var bal0 = payload.balance_t0_m || [];
    var bal1 = payload.balance_t1_m || [];
    var delt = payload.delta_m      || [];
    var grw  = payload.growth_pct   || [];
    var w0   = payload.weight_t0_pct || [];
    var w1   = payload.weight_t1_pct || [];
    var tot  = payload.totals || {};
    var lbl0 = d0 || "t0";
    var lbl1 = d1 || "t1";
    // Store for toggle re-render
    if (baHmState[prefix]) baHmState[prefix] = { payload: payload, lbl0: lbl0, lbl1: lbl1 };
    var decomp = (document.getElementById(prefix + "-decomp") || {}).value || "SEGMENT";

    var el = function(id) { return document.getElementById(id); };
    // Ordinal boyut (AUM bantlari) siral gradient alir; kategorikler 12-ton palet.
    var palette = (decomp === "AUM") ? _ordinalRamp(cats.length) : _PRISMA_CAT;
    var COLOR_T0 = "#7A8399", COLOR_T1 = "#D4A574";

    // KPI strip
    var fmt0 = function(v) { return v == null ? "–" : Math.round(v).toLocaleString("tr-TR"); };
    if (el(prefix + "-bal-t0")) el(prefix + "-bal-t0").textContent = fmt0(tot.balance_t0_m) + " ₺M";
    if (el(prefix + "-bal-t1")) el(prefix + "-bal-t1").textContent = fmt0(tot.balance_t1_m) + " ₺M";
    var dEl = el(prefix + "-bal-delta");
    if (dEl) {
      dEl.textContent = _fmtSignedM(tot.delta_m, 0) + " ₺M";
      dEl.style.color = (tot.delta_m || 0) > 0 ? "#7A9B7E" : (tot.delta_m || 0) < 0 ? "#B8826B" : "var(--text-primary)";
    }
    var gEl = el(prefix + "-growth");
    if (gEl) {
      gEl.textContent = tot.growth_pct == null ? "– %" :
        (tot.growth_pct >= 0 ? "+" : "") + tot.growth_pct.toFixed(2) + " %";
      gEl.style.color = (tot.growth_pct || 0) > 0 ? "#7A9B7E" : (tot.growth_pct || 0) < 0 ? "#B8826B" : "var(--text-primary)";
    }

    if (cats.length === 0) {
      // "-cust-heatmap" de DAHİL: aksi halde veri boşalınca Balance paneli
      // temizlenir ama Customer paneli eski figürü tutar → slider'la geçince
      // güncel-olmayan (stale) veri görünür.
      ["-bridge","-heatmap","-cust-heatmap","-mix","-mix-delta"].forEach(function(s) {
        var n = el(prefix + s); if (n) Plotly.purge(n);
      });
      return;
    }

    // Chart 1: Balance Bridge (waterfall via renderChart)
    if (payload.bridge) {
      var _bridgeId = prefix + "-bridge";
      destroyChart(_bridgeId);
      (function(bid, pref, dec, dd0, dd1) {
        renderChart(bid, payload.bridge, {
          height: 360,
          onRelativeBarDblClick: function(bar) {
            if (bar.x !== "Other") _showBalanceDrill(bar.x, pref, bid, {
              drillDim: dec, showProductBar: true, origD0: dd0, origD1: dd1,
            });
          },
        });
      })(_bridgeId, prefix, decomp, lbl0, lbl1);
    }

    // Chart 2: Balance / Customer Heatmap (Segment × AUM) — tek kartta kaydırma
    // slider'ıyla seçilir. İki panel de carousel şeridinde tam genişlikte durur,
    // bu yüzden ikisini de render ediyoruz (translateX ile hangisinin görüneceği
    // seçilir; gizli-container boyut sorunu yok).
    _renderBaHeatmap(prefix, payload, lbl0, lbl1);
    _renderBaHeatmap(prefix, payload, lbl0, lbl1, "customer");   // Customer Number Heatmap

    // Composition Evolution başlığını Decomposition Dim'e göre güncelle
    // (Segment / AUM / Product / Sub-Product / Customer Type).
    var _mixLbl = el(prefix + "-mix-dimlabel");
    if (_mixLbl) _mixLbl.textContent = _baDimLabel(decomp);

    // Chart 3: Composition Evolution (horizontal stacked t0 vs t1) — Decomp Dim'e göre
    var mixTraces = [];
    cats.forEach(function(c, i) {
      mixTraces.push({
        y: [lbl0, lbl1], x: [w0[i], w1[i]],
        name: c, orientation: "h", type: "bar",
        marker: { color: palette[i % palette.length] },
        hovertemplate: "<b>" + c + "</b><br>Share: %{x:.2f}%<extra></extra>",
      });
    });
    renderPlotlyFig(prefix + "-mix", {
      data: mixTraces,
      layout: {
        height: 210, barmode: "stack",
        xaxis: { title: "Composition (%)", ticksuffix: "%", range: [0, 100] },
        yaxis: { title: "", type: "category", categoryorder: "array",
                 categoryarray: [lbl1, lbl0], automargin: true },
        margin: { l: 110, r: 20, t: 10, b: 50 },
        legend: { orientation: "h", x: 0, y: -0.5 },
      },
    }, 220);
    // Composition: dbl-click on a segment trace → drill into that category
    _attachBalancePlotlyDblClick(prefix + "-mix", prefix,
      function(pt) { return (pt.data && pt.data.name) || null; },
      { drillDim: decomp, showProductBar: true, origD0: lbl0, origD1: lbl1 }
    );

    // Chart 4b: Composition Δ (vertical bar)
    var mixDelta = w1.map(function(v, i) { return parseFloat((v - w0[i]).toFixed(2)); });
    var mixDeltaCol = mixDelta.map(function(v) { return v >= 0 ? "#7A9B7E" : "#B8826B"; });
    var mixDeltaTxt = mixDelta.map(function(v) { return (v >= 0 ? "+" : "") + v.toFixed(2) + "%"; });
    renderPlotlyFig(prefix + "-mix-delta", {
      data: [{
        x: cats, y: mixDelta, type: "bar",
        marker: { color: mixDeltaCol },
        text: mixDeltaTxt, textposition: "outside", cliponaxis: false,
        textfont: { size: 12, color: _plotInk() },
        hovertemplate: "<b>%{x}</b><br>Δ Share: %{y:+.2f}%<extra></extra>",
      }],
      layout: {
        height: 260,
        xaxis: { tickangle: -30, automargin: true },
        yaxis: { title: "Δ Composition (%)", ticksuffix: "%" },
        margin: { l: 60, r: 20, t: 40, b: 80 },
        title: { text: "Composition Change (" + lbl0 + " → " + lbl1 + ")", font: { size: 14 } },
        shapes: [{ type: "line", x0: -0.5, x1: cats.length - 0.5, y0: 0, y1: 0,
                   line: { color: "rgba(255,255,255,0.25)", width: 1 } }],
      },
    }, 260);
    _attachBalancePlotlyDblClick(prefix + "-mix-delta", prefix,
      function(pt) { return pt.x || null; },
      { drillDim: decomp, showProductBar: true, origD0: lbl0, origD1: lbl1 }
    );

    // (Eski "Composition Evolution by AUM" ve "Ranked Growth" grafikleri
    // kaldırıldı; kompozisyon artık tek grafikte Decomposition Dim'e göre.)
  }

  // ── Balance heatmap state + toggle helpers ──────────────────────────────────
  var baHmState = {
    "ba-mon": { payload: null, lbl0: "", lbl1: "" },
    "ba-dly": { payload: null, lbl0: "", lbl1: "" },
  };

  function _setBaHmMode(prefix, mode) {
    baHmMode[prefix] = mode;
    _syncHmSwitchUi(prefix + "-hm-switch", mode);
    var st = baHmState[prefix];
    if (st && st.payload) _renderBaHeatmap(prefix, st.payload, st.lbl0, st.lbl1);
  }

  function _setBaCustHmMode(prefix, mode) {
    baCustHmMode[prefix] = mode;
    _syncHmSwitchUi(prefix + "-cust-hm-switch", mode);
    var st = baHmState[prefix];
    if (st && st.payload) _renderBaHeatmap(prefix, st.payload, st.lbl0, st.lbl1, "customer");
  }

  function _syncHmSwitchUi(switchId, mode) {
    var sw = document.getElementById(switchId);
    if (!sw) return;
    sw.classList.toggle("is-right", mode === "abs");
    sw.querySelectorAll(".hm-lbl").forEach(function(lbl) {
      lbl.classList.toggle("active", lbl.dataset.mode === mode);
    });
  }

  // Metrik kaydırma slider'ı: grafik şeridini Balance (0) ↔ Customer (1) arasında
  // YATAY kaydırır. Her iki panel de DOM'da tam genişlikte durur (translateX ile
  // kayar) → Plotly ikisini de doğru boyutta render eder, gizli-container sorunu
  // yok. animate=false ise (ör. ilk kurulum) geçiş animasyonsuz uygulanır.
  function _setBaHmMetric(prefix, metric, animate) {
    baHmMetric[prefix] = metric;
    // Metrik değişince açık kalan çift-tık drill panellerini kapat: aksi halde
    // kaydırınca drill görünmez panelde asılı kalır (viewport clip) ve geri
    // dönünce yeniden belirir. Her iki panelin drill'ini de temizle.
    ["-heatmap", "-cust-heatmap"].forEach(function(sfx) {
      var dr = document.getElementById("ba-drill-" + prefix + sfx);
      if (dr) dr.remove();
    });
    var strip = document.getElementById(prefix + "-metric-strip");
    if (strip) {
      if (animate === false) strip.classList.add("no-anim");
      strip.style.transform = (metric === "customer") ? "translateX(-50%)" : "translateX(0)";
      if (animate === false) {
        // reflow → animasyonu bir sonraki geçişte geri aç
        void strip.offsetWidth;
        strip.classList.remove("no-anim");
      }
    }
    // Uçtaki etiketler + range konumunu senkronla.
    var sl = document.getElementById(prefix + "-metric-slider");
    if (sl) sl.querySelectorAll(".hm-ms-lbl").forEach(function(lbl) {
      lbl.classList.toggle("active", lbl.dataset.metric === metric);
    });
    var range = document.getElementById(prefix + "-metric-range");
    if (range) range.value = (metric === "customer") ? "1" : "0";
  }

  // Segment×AUM Plotly heatmap'leri (Balance / Cost) için mode-aware renk seti.
  // Dark = orijinal navy→amber; light = krem→amber (dark elemanlar kremde
  // görünmez olmasın). Yazı: light'ta koyu grafit, dark'ta near-white.
  function _hmPlotlyTheme(isAbs) {
    // PRISMA heatmap disiplini — sequential: navy→amber; diverging:
    // terracotta→navy→amber (dark) / terracotta→krem→amber (light).
    // Eski diverging'te pozitif kol adaçayı/zeytin yeşiline (#9BAE8A/#C7CFA6),
    // negatif kol light'ta gül pembesine (#C98A8A) kayıyordu → hem palet dışı
    // hem colorbar'la (amber) çelişkili. Total metni amber'e alındı (vurgu).
    if (_hmLight()) {
      return {
        mainCs: isAbs
          ? [[0.0,"#F5EFE0"],[0.35,"#EAD9AE"],[0.70,"#D4A574"],[1.0,"#B8860B"]]
          : [[0.0,"#B87F6B"],[0.3,"#E2C7B8"],[0.5,"#F5EFE0"],[0.7,"#E5CFA0"],[1.0,"#B8860B"]],
        mainText: "#2C2A26",
        totCs:    [[0,"#EFE6D2"],[1,"#E7DBBF"]],
        totText:  "#8A5A00",
        title:    "#2C2A26",
        axis:     "#6B6862",
      };
    }
    return {
      mainCs: isAbs
        ? [[0.0,"#131826"],[0.25,"#1F2433"],[0.55,"#4A6B8A"],[1.0,"#D4A574"]]
        : [[0.0,"#B8826B"],[0.3,"#6E4E40"],[0.5,"#1F2433"],[0.7,"#6E5A38"],[1.0,"#D4A574"]],
      mainText: "#E4E8F0",
      totCs:    [[0,"#1B2236"],[1,"#1F2433"]],
      totText:  "#D4A574",
      title:    "#E4E8F0",
      axis:     "#7A8399",
    };
  }

  function _renderBaHeatmap(prefix, payload, lbl0, lbl1, kind) {
    // kind: "balance" (default) | "customer". Customer heatmap Balance Heatmap ile
    // AYNI yapı/render — yalnız veri kaynağı (customer_heatmap), element id, mode
    // state, etiket/birim (adet vs ₺M) değişir.
    var isCust = (kind === "customer");
    var _rowLbl = _baDimLabel(_baDecomp(prefix));    // Y ekseni = Decomposition Dim
    var _colLbl = _baDimLabel(_baDecomp2(prefix));   // X ekseni = Second Dec. Dim
    var hm = isCust ? payload.customer_heatmap : payload.heatmap;
    var elBase = prefix + (isCust ? "-cust-heatmap" : "-heatmap");
    var hmEl = document.getElementById(elBase);
    if (!hm || !hm.rows || !hm.rows.length || !hm.cols.length) {
      if (hmEl) Plotly.purge(hmEl);
      return;
    }
    var mode = (isCust ? baCustHmMode[prefix] : baHmMode[prefix]) || "delta";
    var isAbs = (mode === "abs");
    var UNIT   = isCust ? "" : " ₺M";      // customer = adet (birimsiz)
    var METRIC = isCust ? "Customers" : "Balance";
    var absZ   = isCust ? "%{z:.0f}" : "%{z:.1f}";
    var deltaZ = isCust ? "%{z:+.0f}" : "%{z:+.1f}";

    var nR = hm.rows.length, nC = hm.cols.length;
    var allX = hm.cols.concat(["Total"]);
    // Total prepended at index 0 (Plotly puts y[0] at the bottom of a categorical axis)
    var allY = ["Total"].concat(hm.rows);

    var dataZ   = isAbs ? hm.balance_t1   : hm.delta_m;
    var colTots = isAbs ? hm.col_total_balance_t1 : hm.col_total_delta_m;
    var rowTots = isAbs ? hm.row_total_balance_t1 : hm.row_total_delta_m;
    var grandTot = isAbs ? hm.grand_total_balance_t1 : hm.grand_total_delta_m;

    function fmtV(v) {
      if (v == null) return "";
      return isAbs
        ? Math.round(v).toLocaleString("tr-TR")
        : (v >= 0 ? "+" : "") + Math.round(v).toLocaleString("tr-TR");
    }
    // Verisiz DATA hücresi boş bırakılmaz — nötr "—" gösterilir (yalnız main
    // trace; total trace'in transparan iç hücreleri boş kalmalı).
    function fmtCell(v) { var s = fmtV(v); return v == null ? "—" : s; }

    // ── Main trace: data cells only; Total row/col are null (transparent) ──
    // Row 0 of mainZ corresponds to allY[0]="Total" → all nulls.
    var mainZ = [new Array(nC + 1).fill(null)];
    var mainText = [new Array(nC + 1).fill("")];
    for (var i = 0; i < nR; i++) {
      mainZ.push(dataZ[i].concat([null]));
      mainText.push(dataZ[i].map(fmtCell).concat([""]));
    }

    var _th = _hmPlotlyTheme(isAbs);
    var mainCs = _th.mainCs, cbTitle, zMid = null;
    if (isAbs) {
      cbTitle = isCust ? "adet" : "₺M";
    } else {
      cbTitle = isCust ? "Δ adet" : "Δ ₺M";
      zMid    = 0;
    }

    var mainTrace = {
      type: "heatmap",
      x: allX, y: allY, z: mainZ,
      text: mainText, texttemplate: "%{text}",
      textfont: { size: 12, color: _th.mainText },
      colorscale: mainCs,
      showscale: true,
      colorbar: { title: cbTitle, thickness: 12, len: 0.8 },
      hovertemplate: isAbs
        ? "<b>%{y}</b> × <b>%{x}</b><br>" + METRIC + " " + lbl1 + ": " + absZ + UNIT + "<extra></extra>"
        : "<b>%{y}</b> × <b>%{x}</b><br>Δ " + METRIC + ": " + deltaZ + UNIT + "<extra></extra>",
    };
    if (zMid !== null) mainTrace.zmid = zMid;

    if (!isAbs) {
      // Customdata aligned with mainZ: row 0 = Total (blank), rows 1..nR = data
      var cdMain = [new Array(nC + 1).fill(["", ""])];
      for (var ii = 0; ii < nR; ii++) {
        var row = hm.cols.map(function(_, c) {
          return [
            hm.growth_pct[ii][c] == null ? "–" : (hm.growth_pct[ii][c] >= 0 ? "+" : "") + hm.growth_pct[ii][c].toFixed(1) + "%",
            (hm.balance_t1[ii][c] || 0).toLocaleString("tr-TR", {maximumFractionDigits: isCust ? 0 : 1}),
          ];
        });
        row.push(["", ""]);
        cdMain.push(row);
      }
      mainTrace.customdata = cdMain;
      mainTrace.hovertemplate = "<b>%{y}</b> × <b>%{x}</b><br>Δ " + METRIC + ": " + deltaZ + UNIT
        + "<br>Growth: %{customdata[0]}<br>" + METRIC + " " + lbl1 + ": %{customdata[1]}" + UNIT + "<extra></extra>";
    }

    // ── Total trace: slate-gray cells at Total row & column ──────────────────
    // Row 0 of totActual matches allY[0]="Total" → all row totals + grand total.
    var totActual = [(rowTots || []).concat([grandTot != null ? grandTot : null])];
    for (var ji = 0; ji < nR; ji++) {
      totActual.push(new Array(nC).fill(null).concat([colTots ? colTots[ji] : null]));
    }

    var totZ = totActual.map(function(row) {
      return row.map(function(v) { return v != null ? 1 : null; });
    });
    var totText = totActual.map(function(row) { return row.map(fmtV); });

    var totTrace = {
      type: "heatmap",
      x: allX, y: allY, z: totZ,
      text: totText, texttemplate: "%{text}",
      textfont: { size: 12, color: _th.totText },
      colorscale: _th.totCs,
      zmin: 0, zmax: 1,
      showscale: false,
      hovertemplate: "<b>%{y}</b> × <b>%{x}</b><br>Total: %{text}<extra></extra>",
    };

    var hmSpec = {
      data: [mainTrace, totTrace],
      layout: {
        height: Math.max(280, allY.length * 36 + 80),
        paper_bgcolor: "transparent", plot_bgcolor: "transparent",
        font: { family: "JetBrains Mono, monospace", size: 12, color: _th.axis },
        xaxis: { title: { text: _colLbl, font: { size: 12 } }, tickangle: -30, automargin: true, tickfont: { size: 11 } },
        yaxis: { title: { text: _rowLbl, font: { size: 12 } }, automargin: true, type: "category", tickfont: { size: 12 } },
        coloraxis: { colorbar: { tickfont: { size: 11, color: _th.axis },
                                 title: { font: { size: 11, color: _th.axis } } } },
        margin: { l: 80, r: 40, t: 30, b: 80 },
        title: {
          text: isCust
            ? (isAbs ? "Customer Number at " + lbl1 + " — " + _rowLbl + " × " + _colLbl
                     : "Customer Number Change — " + _rowLbl + " × " + _colLbl + " (" + lbl0 + " → " + lbl1 + ")")
            : (isAbs ? "Balance at " + lbl1 + " — " + _rowLbl + " × " + _colLbl
                     : "Balance Change Heatmap — " + _rowLbl + " × " + _colLbl + " (" + lbl0 + " → " + lbl1 + ")"),
          font: { size: 14, color: _th.title },
        },
        separators: ",.",
      },
    };
    renderPlotlyFig(elBase, hmSpec, Math.max(280, allY.length * 36 + 80));
    _attachHmHoverBorder(elBase);
    _attachBaHmDblClick(elBase, prefix, isCust ? "customer" : "balance");
    // Eksen-başlığı seçimi → gruplama (Y=Decomp, X=Second Dec. Dim).
    (function() {
      var _isMon = (prefix === "ba-mon");
      var _rowDim = _baDecomp(prefix), _colDim = _baDecomp2(prefix);
      _attachHmAxisSelect(elBase, {
        allX: allX, allY: allY, rowVals: hm.rows, colVals: hm.cols,
        rowDim: _rowDim, colDim: _colDim,
        rowNumeric: (_rowDim === "AUM"), colNumeric: (_colDim === "AUM"),
        meta:   function() { return baFilterMeta; },
        state:  function() { return _isMon ? baMonBubState  : baDlyBubState; },
        merges: function() { return _isMon ? baMonBubMerges : baDlyBubMerges; },
        apply:  function() {
          var st = _isMon ? baMonBubState  : baDlyBubState;
          var mg = _isMon ? baMonBubMerges : baDlyBubMerges;
          var pid = _isMon ? "ba-mon-filters" : "ba-dly-filters";
          var rf = _isMon ? fetchBalanceMonthly : fetchBalanceDaily;
          _renderBubFilters(pid, baFilterMeta, st, mg, function() { rf(); });
          rf();
        },
      });
    })();
  }

  // ── Cost Analysis rate heatmap helpers ──────────────────────────────────────
  function _setCaRateHmMode(prefix, mode) {
    caRateHmMode[prefix] = mode;
    _syncHmSwitchUi(prefix + "-rate-hm-switch", mode);
    var figs = (prefix === "ca-mon") ? caMonFigs : dddFigs;
    if (!figs || !figs.rate_heatmap) return;
    var d0Id = (prefix === "ca-mon") ? "ca-mon-date0" : "ddd-date0";
    var d1Id = (prefix === "ca-mon") ? "ca-mon-date1" : "ddd-date1";
    var d0El = document.getElementById(d0Id);
    var d1El = document.getElementById(d1Id);
    _renderCaRateHeatmap(prefix, figs.rate_heatmap, d0El ? d0El.value : "t0", d1El ? d1El.value : "t1");
  }

  // Decomposition Dim değiştiğinde rate heatmap'i o boyuta göre yeniden kur.
  // Sonucu figs.rate_heatmap'e yazar → mode toggle (_setCaRateHmMode) doğru çalışır.
  async function _fetchCaRateHeatmap(prefix) {
    var isMon = (prefix === "ca-mon");
    var d0El = document.getElementById(prefix + "-date0");
    var d1El = document.getElementById(prefix + "-date1");
    if (!d0El || !d1El) return;
    var d0 = d0El.value, d1 = d1El.value;
    if (!d0 || !d1 || d0 === d1) return;
    var decomp = _baDecomp(prefix + "-rate");
    var source = isMon ? "monthly" : "daily";
    try {
      var r = await fetch("/api/cost_rate_heatmap?source=" + encodeURIComponent(source)
        + "&date_0=" + encodeURIComponent(d0) + "&date_1=" + encodeURIComponent(d1)
        + "&decomp=" + encodeURIComponent(decomp)
        + "&decomp2=" + encodeURIComponent(_baDecomp2(prefix + "-rate"))
        + _caBubQS(prefix));
      var data = await r.json();
      if (!data.ok) return;
      var figs = isMon ? caMonFigs : dddFigs;
      if (figs) figs.rate_heatmap = data.rate_heatmap;
      _renderCaRateHeatmap(prefix, data.rate_heatmap, d0, d1);
    } catch(e) { /* silent */ }
  }

  // Decomp SEGMENT, Second Dec. AUM ve filtre yoksa ana fetch'in gömdüğü
  // heatmap'i kullan (ekstra istek yok); aksi halde dedike endpoint'ten çek.
  function _renderCaRateFromState(prefix, figs, d0, d1) {
    if (_baDecomp(prefix + "-rate") === "SEGMENT"
        && _baDecomp2(prefix + "-rate") === "AUM"
        && !_caBubQS(prefix)) {
      if (figs && figs.rate_heatmap) _renderCaRateHeatmap(prefix, figs.rate_heatmap, d0, d1);
    } else {
      _fetchCaRateHeatmap(prefix);
    }
  }

  function _renderCaRateHeatmap(prefix, rh, lbl0, lbl1) {
    var elId = prefix + "-rate-heatmap";
    var hmEl = document.getElementById(elId);
    if (!rh || !rh.rows || !rh.rows.length || !rh.cols.length) {
      if (hmEl) Plotly.purge(hmEl);
      return;
    }
    var mode = caRateHmMode[prefix] || "delta";
    var isAbs = (mode === "abs");
    // Y ekseni = Decomposition Dim, X ekseni = Second Dec. Dim.
    var _rowLbl = _baDimLabel(_baDecomp(prefix + "-rate"));
    var _colLbl = _baDimLabel(_baDecomp2(prefix + "-rate"));

    var nR = rh.rows.length, nC = rh.cols.length;
    var allX = rh.cols.concat(["Total"]);
    // Total prepended at index 0 (Plotly puts y[0] at the bottom of a categorical axis)
    var allY = ["Total"].concat(rh.rows);

    var dataZ   = isAbs ? rh.rate_t1_pct : rh.delta_bps;
    var colTots = isAbs ? rh.col_total_rate_t1_pct : rh.col_total_delta_bps;
    var rowTots = isAbs ? rh.row_total_rate_t1_pct : rh.row_total_delta_bps;
    var grandTot = isAbs ? rh.grand_total_rate_t1_pct : rh.grand_total_delta_bps;

    function fmtV(v) {
      if (v == null) return "";
      return isAbs
        ? v.toFixed(2).replace(".", ",") + "%"
        : (v >= 0 ? "+" : "") + Math.round(v).toLocaleString("tr-TR");
    }
    // Verisiz DATA hücresi "—" (yalnız main trace — bkz. Balance heatmap notu).
    function fmtCell(v) { return v == null ? "—" : fmtV(v); }

    // ── Main trace ────────────────────────────────────────────────────────────
    // Row 0 of mainZ corresponds to allY[0]="Total" → all nulls.
    var mainZ = [new Array(nC + 1).fill(null)];
    var mainText = [new Array(nC + 1).fill("")];
    for (var i = 0; i < nR; i++) {
      mainZ.push(dataZ[i].concat([null]));
      mainText.push(dataZ[i].map(fmtCell).concat([""]));
    }

    // Abs: sequential navy → amber (faiz seviyesi yoğunluğu).
    // Delta: diverging terracotta (negatif Δ bps) → nötr → amber (pozitif Δ bps).
    // Mode-aware: light modda krem→amber (bkz. _hmPlotlyTheme).
    var _th = _hmPlotlyTheme(isAbs);
    var mainCs = _th.mainCs;

    var mainTrace = {
      type: "heatmap",
      x: allX, y: allY, z: mainZ,
      text: mainText, texttemplate: "%{text}",
      textfont: { size: 12, color: _th.mainText },
      colorscale: mainCs,
      showscale: true,
      colorbar: { title: isAbs ? "%" : "Δ bps", thickness: 12, len: 0.8 },
      hovertemplate: isAbs
        ? "<b>%{y}</b> × <b>%{x}</b><br>Rate " + lbl1 + ": %{z:.2f}%<extra></extra>"
        : "<b>%{y}</b> × <b>%{x}</b><br>Δ Rate: %{z:+.0f} bps<extra></extra>",
    };
    if (!isAbs) mainTrace.zmid = 0;

    // ── Total trace: slate-gray cells ─────────────────────────────────────────
    // Row 0 of totActual matches allY[0]="Total" → all row totals + grand total.
    var totActual = [(rowTots || []).concat([grandTot != null ? grandTot : null])];
    for (var ji = 0; ji < nR; ji++) {
      totActual.push(new Array(nC).fill(null).concat([colTots ? colTots[ji] : null]));
    }

    var totZ = totActual.map(function(row) {
      return row.map(function(v) { return v != null ? 1 : null; });
    });
    var totText = totActual.map(function(row) { return row.map(fmtV); });

    var totTrace = {
      type: "heatmap",
      x: allX, y: allY, z: totZ,
      text: totText, texttemplate: "%{text}",
      textfont: { size: 12, color: _th.totText },
      colorscale: _th.totCs,
      zmin: 0, zmax: 1,
      showscale: false,
      hovertemplate: "<b>%{y}</b> × <b>%{x}</b><br>Total: %{text}<extra></extra>",
    };

    var hmSpec = {
      data: [mainTrace, totTrace],
      layout: {
        height: Math.max(280, allY.length * 36 + 80),
        paper_bgcolor: "transparent", plot_bgcolor: "transparent",
        font: { family: "JetBrains Mono, monospace", size: 12, color: _th.axis },
        xaxis: { title: { text: _colLbl, font: { size: 12 } }, tickangle: -30, automargin: true, tickfont: { size: 11 } },
        yaxis: { title: { text: _rowLbl, font: { size: 12 } }, automargin: true, type: "category", tickfont: { size: 12 } },
        margin: { l: 80, r: 40, t: 30, b: 80 },
        title: {
          text: isAbs
            ? "Interest Rate at " + lbl1 + " — " + _rowLbl + " × " + _colLbl + " (%)"
            : "Interest Rate Change — " + _rowLbl + " × " + _colLbl + " (" + lbl0 + " → " + lbl1 + ", bps)",
          font: { size: 14, color: _th.title },
        },
        separators: ",.",
      },
    };
    renderPlotlyFig(elId, hmSpec, Math.max(280, allY.length * 36 + 80));
    _attachHmHoverBorder(elId);
    _attachRateHmDblClick(elId, prefix);
    // Eksen-başlığı seçimi → gruplama (Cost rate heatmap; refetch merge'i honor eder).
    (function() {
      var _isMon = (prefix === "ca-mon");
      var _rowDim = _baDecomp(prefix + "-rate"), _colDim = _baDecomp2(prefix + "-rate");
      _attachHmAxisSelect(elId, {
        allX: allX, allY: allY, rowVals: rh.rows, colVals: rh.cols,
        rowDim: _rowDim, colDim: _colDim,
        rowNumeric: (_rowDim === "AUM"), colNumeric: (_colDim === "AUM"),
        meta:   function() { return _isMon ? caMonBubMeta   : dddBubMeta; },
        state:  function() { return _isMon ? caMonBubFilter : dddBubFilter; },
        merges: function() { return _isMon ? caMonBubMerges : dddBubMerges; },
        apply:  function() {
          var mt = _isMon ? caMonBubMeta   : dddBubMeta;
          var st = _isMon ? caMonBubFilter : dddBubFilter;
          var mg = _isMon ? caMonBubMerges : dddBubMerges;
          var pid = _isMon ? "ca-mon-bub-filters" : "ddd-bub-filters";
          var onCh = _isMon ? function() { _renderCaMonBubbles(); _fetchCaRateHeatmap("ca-mon"); }
                            : function() { _renderDddBubbles();   _fetchCaRateHeatmap("ddd"); };
          _renderBubFilters(pid, mt, st, mg, onCh);
          onCh();
        },
      });
    })();
  }

  // Double-click detector for the rate heatmap. Mirrors _attachBalancePlotlyDblClick
  // but routes to /api/rate_drill via _showRateDrill.
  // Dedicated double-click handler for the balance heatmap.
  // Supports Total row/column: seg="" → all segments, aum="" → all AUMs.
  // kind: "balance" (default) | "customer". Heatmap Y ekseni artık Decomposition
  // Dim; alt-kırılım Second Dim; customer heatmap için metrik = müşteri adedi.
  function _attachBaHmDblClick(elId, prefix, kind) {
    var domEl = document.getElementById(elId);
    if (!domEl || typeof domEl.on !== "function") return;
    try { domEl.removeAllListeners("plotly_click"); } catch(e) {}
    var lastMs = 0, lastKey = null;
    domEl.on("plotly_click", function(ev) {
      if (!ev || !ev.points || !ev.points.length) return;
      var pt  = ev.points[0];
      // null = "Total" (no filter); string (even "") = actual dimension value.
      var rowV = (pt.y === "Total") ? null : (pt.y != null ? String(pt.y) : "");
      var colV = (pt.x === "Total") ? null : (pt.x != null ? String(pt.x) : "");
      var key = (rowV != null ? rowV : "_TOTAL_") + "||" + (colV != null ? colV : "_TOTAL_");
      var now = Date.now();
      if (now - lastMs < 450 && lastKey === key) {
        lastMs = 0; lastKey = null;
        var d0El = document.getElementById(prefix + "-date0");
        var d1El = document.getElementById(prefix + "-date1");
        var origD0 = d0El ? d0El.value : "";
        var origD1 = d1El ? d1El.value : "";
        var rowDim = _baDecomp(prefix);    // Y ekseni = Decomposition Dim
        var colDim = _baDecomp2(prefix);   // X ekseni = Second Dec. Dim
        var rowLbl = _baDimLabel(rowDim);
        var colLbl = _baDimLabel(colDim);
        var drillDim = "", drillValue = "", extraDim = "", extraValue = "", label;
        if (rowV !== null && colV !== null) {
          drillDim = rowDim; drillValue = rowV;
          extraDim = colDim; extraValue = colV;
          label = (rowV || "—") + " × " + (colV || "—");
        } else if (rowV !== null) {
          drillDim = rowDim; drillValue = rowV;
          label = (rowV || "—") + " × All " + colLbl + "s";
        } else if (colV !== null) {
          drillDim = colDim; drillValue = colV;
          label = "All " + rowLbl + "s × " + (colV || "—");
        } else {
          label = "All " + rowLbl + "s × All " + colLbl + "s";
        }
        // Satır/kolon değerlerini kendi boyutlarının bar filtrelerine ata
        // (null = Total → o boyutta filtre yok). rowDim ≠ colDim (mutex).
        var o = { drillDim: drillDim, extraDim: extraDim, extraValue: extraValue,
          label: label, showProductBar: true, origD0: origD0, origD1: origD1,
          barKind: (kind === "customer" ? "customer" : "balance") };
        var rk = _barKey(rowDim), ck = _barKey(colDim);
        if (rk) o[rk] = rowV;
        if (ck) o[ck] = colV;
        _showBalanceDrill(drillValue, prefix, elId, o);
      } else {
        lastMs = now; lastKey = key;
      }
    });
  }

  // Highlight the hovered cell of a heatmap with a rectangular border, by
  // injecting a Plotly shape on plotly_hover and clearing it on plotly_unhover.
  // Works for any heatmap with categorical x/y axes (uses pointIndex → 0.5 padding).
  // Ortak shape yöneticisi: seçim (persistent) + hover border tek relayout'ta
  // birleşir. Hover eskiden tüm shapes'i eziyordu → seçim vurgusu kaybolurdu.
  function _hmApplyShapes(domEl) {
    var base = domEl.__baseShapes || [];   // grafiğin kendi shape'leri (ör. sıfır çizgisi)
    var sel = domEl.__selShapes || [];
    var hov = domEl.__hoverShape ? [domEl.__hoverShape] : [];
    try { Plotly.relayout(domEl, { shapes: base.concat(sel).concat(hov) }); } catch(e) {}
  }

  function _attachHmHoverBorder(elId) {
    var domEl = document.getElementById(elId);
    if (!domEl || typeof domEl.on !== "function") return;
    try { domEl.removeAllListeners("plotly_hover"); } catch(e) {}
    try { domEl.removeAllListeners("plotly_unhover"); } catch(e) {}
    var lastKey = null;
    domEl.on("plotly_hover", function(ev) {
      if (!ev || !ev.points || !ev.points.length) return;
      var pt = null;
      for (var i = 0; i < ev.points.length; i++) {
        if (ev.points[i].pointIndex) { pt = ev.points[i]; break; }
      }
      if (!pt || !pt.pointIndex) return;
      var yIdx = pt.pointIndex[0];
      var xIdx = pt.pointIndex[1];
      var key = yIdx + "|" + xIdx;
      if (key === lastKey) return;
      lastKey = key;
      domEl.__hoverShape = {
        type: "rect", xref: "x", yref: "y",
        x0: xIdx - 0.5, x1: xIdx + 0.5, y0: yIdx - 0.5, y1: yIdx + 0.5,
        line: { color: "#D4A574", width: 2 }, fillcolor: "rgba(0,0,0,0)", layer: "above",
      };
      _hmApplyShapes(domEl);
    });
    domEl.on("plotly_unhover", function() {
      lastKey = null; domEl.__hoverShape = null; _hmApplyShapes(domEl);
    });
  }

  // ── Heatmap eksen-başlığı seçimi → gruplama ─────────────────────────────────
  // Plotly heatmap tick label'larına tıklanabilirlik ekler: tık = satır/kolon'u
  // seç+parlat, tekrar tık = bırak, Ctrl+tık = çoklu seçim, numerik bucket'lı
  // dim'de (AUM) Ctrl+tık aradaki tüm bucket'ları da seçer. Enter → seçimden
  // merge grubu kurar, filtre panelini + grafikleri yeniler.
  var _hmSelActive = null;   // son etkileşilen heatmap elId'i (Enter bunu hedefler)

  // Generic grup KUR (yalnız merges/state'i mutate eder; apply çağıran yapar).
  // NP cfg kendi buildMerge'ini verir (AUM aralık adı / aum_merge farkı).
  function _hmGenericBuild(cfg, dim, vals) {
    var meta = (cfg.meta && cfg.meta()) || {};
    var state = (cfg.state && cfg.state()) || {};
    var merges = (cfg.merges && cfg.merges()) || {};
    var ordered = _sortDimValues(dim, (meta[dim] || []).filter(function(v) { return v !== "" && v != null; }));
    var grp = _buildMergeGroup(dim, ordered.length ? ordered : vals.slice(), vals.slice());
    if (!grp) return;
    merges[dim] = merges[dim] || [];
    if (merges[dim].some(function(g) { return g.name === grp.name; })) {
      grp.name = grp.name + " (" + (merges[dim].length + 1) + ")";
    }
    merges[dim].push(grp);
    state[dim] = state[dim] || {};
    grp.members.forEach(function(m) { if (state[dim][m] === undefined) state[dim][m] = true; });
    state[dim][grp.name] = true;
  }

  // Enter → seçim toggle'ı: seçili değerlerden grup adı olan(lar) çözülür
  // (collapse/ungroup, eski haline döner); geri kalan ham değerler ≥2 ise
  // gruplanır. Tek apply ile tüm grafikler yenilenir.
  function _hmEnterSel(cfg, dim, vals) {
    var merges = (cfg.merges && cfg.merges()) || {};
    var state  = (cfg.state  && cfg.state())  || {};
    var groupNames = (merges[dim] || []).map(function(g) { return g.name; });
    var selGroups = vals.filter(function(v) { return groupNames.indexOf(v) >= 0; });
    var rawVals   = vals.filter(function(v) { return groupNames.indexOf(v) < 0; });
    var changed = false;
    if (selGroups.length) {   // ungroup — grubu sil, üyeleri tekrar görünür yap
      state[dim] = state[dim] || {};
      merges[dim] = (merges[dim] || []).filter(function(g) {
        if (selGroups.indexOf(g.name) >= 0) {
          delete state[dim][g.name];
          (g.members || []).forEach(function(m) { state[dim][m] = true; });
          return false;
        }
        return true;
      });
      changed = true;
    }
    if (rawVals.length >= 2) {   // group — ham değerleri birleştir
      if (cfg.buildMerge) cfg.buildMerge(dim, rawVals);
      else _hmGenericBuild(cfg, dim, rawVals);
      changed = true;
    }
    if (changed && cfg.apply) cfg.apply();
  }

  // Delete → seçili değerleri (ham ya da grup) filtreden çıkar (state=false).
  function _hmDeleteSel(cfg, dim, vals) {
    var state = (cfg.state && cfg.state()) || {};
    state[dim] = state[dim] || {};
    vals.forEach(function(v) { state[dim][v] = false; });
    if (cfg.apply) cfg.apply();
  }

  // cfg: { allX, allY, rowVals, colVals, rowDim, colDim, rowNumeric, colNumeric,
  //        meta(), state(), merges(), apply() }
  function _attachHmAxisSelect(elId, cfg) {
    var domEl = document.getElementById(elId);
    if (!domEl) return;
    domEl.__hmSelCfg = cfg;
    domEl.__axisSel = { axis: null, vals: [] };   // yeni render → seçim sıfır
    domEl.__selShapes = [];
    // Grafiğin kendi shape'lerini (ör. delta grafiklerindeki sıfır çizgisi) sakla
    // → seçim/hover relayout'u bunları ezmesin.
    domEl.__baseShapes = (domEl.layout && domEl.layout.shapes) ? domEl.layout.shapes.slice() : [];

    function order(axis) { return axis === "x" ? (cfg.colVals || []) : (cfg.rowVals || []); }
    function plotArr(axis) { return axis === "x" ? (cfg.allX || []) : (cfg.allY || []); }
    function isNumeric(axis) { return axis === "x" ? cfg.colNumeric : cfg.rowNumeric; }

    function redraw() {
      var sel = domEl.__axisSel, shapes = [];
      var arr = plotArr(sel.axis);
      (sel.vals || []).forEach(function(v) {
        var idx = arr.indexOf(v);
        if (idx < 0) return;
        if (sel.axis === "x") shapes.push({ type: "rect", xref: "x", yref: "paper",
          x0: idx - 0.5, x1: idx + 0.5, y0: 0, y1: 1,
          fillcolor: "rgba(212,165,116,0.30)", line: { color: "#D4A574", width: 1.5 }, layer: "above" });
        else shapes.push({ type: "rect", xref: "paper", yref: "y",
          x0: 0, x1: 1, y0: idx - 0.5, y1: idx + 0.5,
          fillcolor: "rgba(212,165,116,0.30)", line: { color: "#D4A574", width: 1.5 }, layer: "above" });
      });
      domEl.__selShapes = shapes;
      _hmApplyShapes(domEl);
      // Seçili tick label'ları vurgula (accent + bold), diğerlerini orijinaline döndür.
      ["x", "y"].forEach(function(ax) {
        domEl.querySelectorAll("g." + ax + "tick text").forEach(function(t) {
          if (t.__origFill === undefined) t.__origFill = t.style.fill || "";
          var on = (sel.axis === ax && sel.vals.indexOf(t.textContent) >= 0);
          t.style.fill = on ? "#D4A574" : t.__origFill;
          t.style.fontWeight = on ? "700" : "";
        });
      });
    }

    function toggle(axis, val, ctrl) {
      var sel = domEl.__axisSel;
      if (sel.axis !== axis) { sel.axis = axis; sel.vals = [val]; }
      else if (!ctrl) {
        sel.vals = (sel.vals.length === 1 && sel.vals[0] === val) ? [] : [val];
        if (!sel.vals.length) sel.axis = null;
      } else {
        var i = sel.vals.indexOf(val);
        if (i >= 0) sel.vals.splice(i, 1); else sel.vals.push(val);
        if (isNumeric(axis) && sel.vals.length >= 2) {
          var ord = order(axis);
          var idxs = sel.vals.map(function(v) { return ord.indexOf(v); }).filter(function(k) { return k >= 0; });
          if (idxs.length) sel.vals = ord.slice(Math.min.apply(null, idxs), Math.max.apply(null, idxs) + 1);
        }
        if (!sel.vals.length) sel.axis = null;
      }
      _hmSelActive = elId;
      redraw();
    }

    function attachTicks(axis) {
      // Yalnız eşlenebilir dim'i olan eksen tıklanabilir (bar grafiklerde Y =
      // sayısal ₺ ekseni → dim yok → tıklanmaz).
      if (axis === "x" && !cfg.colDim) return;
      if (axis === "y" && !cfg.rowDim) return;
      domEl.querySelectorAll("g." + axis + "tick text").forEach(function(t) {
        var v = t.textContent;
        if (v === "Total" || v === "") return;
        t.style.cursor = "pointer";
        t.style.pointerEvents = "all";
        t.addEventListener("click", function(ev) {
          ev.preventDefault(); ev.stopPropagation();
          toggle(axis, v, ev.ctrlKey || ev.metaKey);
        });
      });
    }
    requestAnimationFrame(function() { attachTicks("x"); attachTicks("y"); });
  }

  // Enter → grup/ungroup toggle; Delete → seçimi filtreden çıkar (tek global binding).
  if (!window.__hmSelEnterBound) {
    document.addEventListener("keydown", function(ev) {
      var isEnter = (ev.key === "Enter");
      var isDelete = (ev.key === "Delete");
      if (!isEnter && !isDelete) return;
      var ae = document.activeElement;
      if (ae && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName)) return;
      var el = _hmSelActive && document.getElementById(_hmSelActive);
      if (!el || !el.__axisSel || !el.__axisSel.vals.length) return;
      var cfg = el.__hmSelCfg, sel = el.__axisSel;
      if (!cfg) return;
      var dim = (sel.axis === "x") ? cfg.colDim : cfg.rowDim;
      if (!dim) return;   // eşlenebilir dim yok (ör. NP tenor)
      ev.preventDefault();
      if (isDelete) _hmDeleteSel(cfg, dim, sel.vals.slice());
      else          _hmEnterSel(cfg, dim, sel.vals.slice());
      el.__axisSel = { axis: null, vals: [] };
    });
    window.__hmSelEnterBound = true;
  }

  function _attachRateHmDblClick(elId, prefix) {
    var domEl = document.getElementById(elId);
    if (!domEl || typeof domEl.on !== "function") return;
    try { domEl.removeAllListeners("plotly_click"); } catch(e) {}
    var lastMs = 0, lastKey = null;
    domEl.on("plotly_click", function(ev) {
      if (!ev || !ev.points || !ev.points.length) return;
      var pt = ev.points[0];
      // null = "Total" (no filter); string (even "") = actual dimension value.
      var rowV = (pt.y === "Total") ? null : (pt.y != null ? String(pt.y) : "");
      var colV = (pt.x === "Total") ? null : (pt.x != null ? String(pt.x) : "");
      var key = (rowV != null ? rowV : "_TOTAL_") + "||" + (colV != null ? colV : "_TOTAL_");
      var now = Date.now();
      if (now - lastMs < 450 && lastKey === key) {
        lastMs = 0; lastKey = null;
        _showRateDrill(prefix, elId, rowV, colV);
      } else {
        lastMs = now; lastKey = key;
      }
    });
  }

  async function _showRateDrill(prefix, anchorId, rowVal, colVal) {
    // rowVal = tıklanan satırın Decomposition Dim değeri; colVal = kolonun
    // Second Dec. Dim değeri. null = Total (o boyutta filtre yok).
    var isMonthly = (prefix === "ca-mon");
    var d0Id = isMonthly ? "ca-mon-date0" : "ddd-date0";
    var d1Id = isMonthly ? "ca-mon-date1" : "ddd-date1";
    var d0El = document.getElementById(d0Id);
    var d1El = document.getElementById(d1Id);
    if (!d0El || !d1El) return;
    var d0 = d0El.value, d1 = d1El.value;
    if (!d0 || !d1) return;
    // Capture ORIGINAL snapshot dates (before any range-expansion) for the
    // exact-date /api/hm_product_bar lookup.
    var origD0 = d0, origD1 = d1;

    // Y = Decomposition Dim, X = Second Dec. Dim; alt kırılım = Detail Dim.
    var rowDim   = _baDecomp(prefix + "-rate");
    var colDim   = _baDecomp2(prefix + "-rate");
    var rowLbl   = _baDimLabel(rowDim);
    var colLbl   = _baDimLabel(colDim);
    // Heatmap breakdown kırılımı artık üstteki TEK "Detailed Dim:" (prefix-break-dim)
    // ile kontrol edilir — kart içindeki ayrı "Detail Dim" (rate-second) kaldırıldı.
    var breakDim = (document.getElementById(prefix + "-break-dim") || {}).value || "PRODUCT";
    var breakLbl = _baDimLabel(breakDim);

    // Monthly: expand to full month boundaries — first day of d0's month →
    // last day of d1's month (mirrors _showBalanceDrill).
    if (isMonthly) {
      var m0 = String(d0).match(/^(\d{4})-(\d{2})/);
      var m1 = String(d1).match(/^(\d{4})-(\d{2})/);
      if (m0 && m1) {
        var lastDay = new Date(parseInt(m1[1], 10), parseInt(m1[2], 10), 0).getDate();
        d0 = m0[1] + "-" + m0[2] + "-01";
        d1 = m1[1] + "-" + m1[2] + "-" + String(lastDay).padStart(2, "0");
      }
    }

    var labelFull;
    if (rowVal !== null && colVal !== null) {
      labelFull = (rowVal || "—") + " × " + (colVal || "—");
    } else if (rowVal !== null) {
      labelFull = (rowVal || "—") + " × All " + colLbl + "s";
    } else if (colVal !== null) {
      labelFull = "All " + rowLbl + "s × " + (colVal || "—");
    } else {
      labelFull = "All " + rowLbl + "s × All " + colLbl + "s";
    }
    var dateRange = d0 + " → " + d1;

    var drillId = "rate-drill-" + anchorId.replace(/[^a-zA-Z0-9]/g, "-");
    var prevEl  = document.getElementById(drillId);
    if (prevEl) prevEl.remove();

    var anchorEl = document.getElementById(anchorId);
    if (!anchorEl) return;
    var cardEl = anchorEl.closest(".card") || anchorEl;

    var drillRow = document.createElement("div");
    drillRow.id = drillId;
    drillRow.className = "card";
    drillRow.style.cssText = "position:relative;margin-top:14px;padding:16px;width:100%;box-sizing:border-box;";
    drillRow.innerHTML =
      '<button onclick="document.getElementById(\'' + drillId + '\').remove()" '
      + 'style="position:absolute;top:8px;right:10px;background:none;border:none;'
      + 'cursor:pointer;font-size:16px;color:var(--text-secondary);z-index:2;" title="Kapat">✕</button>'
      + '<div style="font-size:12px;font-weight:600;color:var(--text-primary);margin-bottom:8px;">'
      + 'Daily Interest Rate — <span style="color:var(--accent);">' + labelFull + '</span>'
      + ' <span style="font-size:11px;color:var(--text-secondary);font-weight:400;">(' + dateRange + ')</span></div>'
      + '<div id="' + drillId + '-chart" style="height:260px;width:100%;display:flex;align-items:center;'
      + 'justify-content:center;color:var(--text-secondary);font-size:13px;">Loading…</div>'
      + '<div style="height:1px;background:rgba(255,255,255,0.07);margin:10px 0;"></div>'
      + '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">' + breakLbl + ' Breakdown</div>'
      + '<div id="' + drillId + '-bar" style="min-height:80px;width:100%;display:flex;align-items:center;'
      + 'justify-content:center;color:var(--text-secondary);font-size:12px;">Loading…</div>';
    cardEl.insertAdjacentElement("afterend", drillRow);
    drillRow.scrollIntoView({ behavior: "smooth", block: "nearest" });

    // Bubble filter panel state de taşınır — drill, heatmap hücresiyle tutarlı kalır.
    var url = "/api/rate_drill"
      + "?date_0="      + encodeURIComponent(d0)
      + "&date_1="      + encodeURIComponent(d1)
      + (rowVal !== null
          ? "&drill_dim=" + encodeURIComponent(rowDim) + "&drill_value=" + encodeURIComponent(rowVal)
          : "")
      + (colVal !== null
          ? "&extra_dim=" + encodeURIComponent(colDim) + "&extra_value=" + encodeURIComponent(colVal)
          : "")
      + _caBubQS(prefix);

    // Satır/kolon değerlerini kendi boyutlarının bar filtrelerine ata
    // (null = Total → filtre yok); kırılım breakDim (Detail Dim).
    var _barVals = { barSeg: null, barCustTp: null, barProd: null, barSubp: null, barAum: null };
    var _rk = _barKey(rowDim), _ck = _barKey(colDim);
    if (_rk) _barVals[_rk] = rowVal;
    if (_ck) _barVals[_ck] = colVal;
    var barSeg = _barVals.barSeg, barCustTp = _barVals.barCustTp,
        barProd = _barVals.barProd, barSubp = _barVals.barSubp, barAum = _barVals.barAum;

    try {
      var r = await fetch(url);
      var data = await r.json();
      var chartEl = document.getElementById(drillId + "-chart");
      if (!data.ok || !data.dates || !data.dates.length) {
        if (chartEl) chartEl.textContent = "No daily data found for this selection.";
      } else {
        chartEl.style.cssText = "height:260px;width:100%;";
        chartEl.innerHTML = "";

        var validRates = data.rate_pct.filter(function(v) { return v != null; });
        var yMin = Math.min.apply(null, validRates);
        var yMax = Math.max.apply(null, validRates);
        var ySpan = yMax - yMin;
        // Pad %15 + smoothing 0.65 — spline overshoot ekseni aşmasın (balance drill ile aynı).
        var yPad = ySpan > 0 ? ySpan * 0.15 : Math.max(Math.abs(yMax) * 0.02, 0.1);

        Plotly.react(chartEl, [{
          x: data.dates, y: data.rate_pct,
          type: "scatter", mode: "lines+markers",
          name: labelFull,
          line:   { color: "#D4A574", width: 2, shape: "spline", smoothing: 0.65 },
          marker: { size: 4, color: "#D4A574" },
          connectgaps: false,
          hovertemplate: "%{x}<br><b>%{y:.2f}%</b><extra>" + labelFull + "</extra>",
        }], {
          autosize: true,
          height: 260,
          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
          font: { family: "system-ui,-apple-system,sans-serif", size: 12, color: "#E4E8F0" },
          xaxis: { gridcolor: "rgba(255,255,255,0.06)", color: "#7A8399", automargin: true },
          yaxis: { gridcolor: "rgba(255,255,255,0.06)", color: "#7A8399",
                   title: "Rate (%)", tickformat: ".2f", ticksuffix: "%",
                   range: [yMin - yPad, yMax + yPad], autorange: false, zeroline: false,
                   automargin: true },
          margin: { l: 70, r: 20, t: 8, b: 50 },
          separators: ",.",
        }, _plotlyConfig);
        requestAnimationFrame(function() { try { Plotly.Plots.resize(chartEl); } catch(_) {} });
      }
      // Always render the product bar regardless of whether the line chart has data.
      // origD0/origD1 = pre-expansion dropdown dates for exact-date match in the bar endpoint.
      _renderHmProductBar(drillId, origD0, origD1, barSeg, barAum, "rate", prefix, barCustTp, breakDim, barProd, barSubp);
    } catch(e) {
      var c = document.getElementById(drillId + "-chart");
      if (c) c.textContent = "Data could not be loaded.";
      _renderHmProductBar(drillId, origD0, origD1, barSeg, barAum, "rate", prefix, barCustTp, breakDim, barProd, barSubp);
    }
  }

  // Renders the product-breakdown horizontal bar chart inside a drill card.
  // kind = "balance" | "rate"; uses the current heatmap toggle mode for that prefix.
  // seg / aum / custTp: null → param not sent (no filter); string (even "") → filter explicitly.
  async function _renderHmProductBar(drillId, d0, d1, seg, aum, kind, prefix, custTp, breakDim, prod, subp) {
    var barEl = document.getElementById(drillId + "-bar");
    if (!barEl) return;
    // Pick the right data source so the bar totals match the heatmap cell.
    // ca-mon (rate monthly) & ba-mon (balance monthly) → DepositDetailEngine (MONTH).
    // ddd (rate daily) & ba-dly (balance daily) → DailyDepositEngine (DAT).
    var source = (prefix === "ca-mon" || prefix === "ba-mon") ? "monthly" : "daily";
    // Carry the same dim_filters / merges the balance analysis tab sends so
    // sum of product bars == heatmap cell value.
    var bubQS = "";
    if (kind === "balance" || kind === "customer") {
      var isMon  = (prefix === "ba-mon");
      var bState = isMon ? baMonBubState  : baDlyBubState;
      var bMerge = isMon ? baMonBubMerges : baDlyBubMerges;
      bubQS = _balanceBubStateToQuery(bState, bMerge);
    } else if (kind === "rate") {
      // Cost Analysis rate drill — bubble filter panel state (ca-mon / ddd).
      bubQS = _caBubQS(prefix);
    }
    // null → don't send param (Total: no filter); string (even "") → send explicitly
    // so backend can filter to actual empty-string dimension values.
    var url = "/api/hm_product_bar"
      + "?date_0="  + encodeURIComponent(d0)
      + "&date_1="  + encodeURIComponent(d1)
      + "&source="  + encodeURIComponent(source)
      + (seg    !== null && seg    !== undefined ? "&segment=" + encodeURIComponent(seg)    : "")
      + (aum    !== null && aum    !== undefined ? "&aum="     + encodeURIComponent(aum)    : "")
      + (custTp !== null && custTp !== undefined ? "&cust_tp=" + encodeURIComponent(custTp) : "")
      + (prod   !== null && prod   !== undefined ? "&product=" + encodeURIComponent(prod)   : "")
      + (subp   !== null && subp   !== undefined ? "&subproduct=" + encodeURIComponent(subp) : "")
      + (breakDim ? "&break_dim=" + encodeURIComponent(breakDim) : "")
      + bubQS;
    try {
      var r    = await fetch(url);
      var data = await r.json();
      if (!data.ok || !data.products || !data.products.length) {
        barEl.textContent = "No product data found.";
        barEl.style.cssText = "color:var(--text-secondary);font-size:12px;padding:8px 0;";
        return;
      }
      var mode  = (kind === "balance") ? (baHmMode[prefix] || "delta")
                : (kind === "customer") ? (baCustHmMode[prefix] || "delta")
                : (caRateHmMode[prefix] || "delta");
      var isAbs = (mode === "abs");
      var vals, xTitle, tickfmt, ticksfx;
      if (kind === "balance") {
        vals    = isAbs ? data.balance_t1_m : data.delta_m;
        xTitle  = isAbs ? "Balance at t1 (₺M)" : "Balance Δ (₺M)";
        tickfmt = ",.0f"; ticksfx = " ₺M";
      } else if (kind === "customer") {
        vals    = isAbs ? data.count_t1 : data.count_delta;
        xTitle  = isAbs ? "Customers at t1" : "Customer Δ";
        tickfmt = ",.0f"; ticksfx = "";
      } else {
        vals    = isAbs ? data.rate_t1_pct : data.delta_bps;
        xTitle  = isAbs ? "Rate at t1 (%)" : "Rate Δ (bps)";
        tickfmt = isAbs ? ".2f" : ".0f";
        ticksfx = isAbs ? "%" : " bps";
      }
      var textArr = vals.map(function(v) {
        if (v == null) return "–";
        if (kind === "balance") return (isAbs ? "" : (v >= 0 ? "+" : "")) + Math.round(v).toLocaleString("tr-TR") + " ₺M";
        if (kind === "customer") return (isAbs ? "" : (v >= 0 ? "+" : "")) + Math.round(v).toLocaleString("tr-TR");
        return isAbs ? v.toFixed(2) + "%" : (v >= 0 ? "+" : "") + Math.round(v) + " bps";
      });
      var barColors = vals.map(function(v) {
        if (v == null) return "#5C6478";             // nötr gri
        if (!isAbs) return (v >= 0 ? "#7A9B7E" : "#B8826B");  // adaçayı / terracotta
        return kind === "balance" ? "#4A6B8A" : kind === "customer" ? "#9BAE8A" : "#7B6B95";
      });
      // Tüm PRISMA tonları koyu arka plan üzerinde okunur → hep açık metin.
      var textColors = barColors.map(function() { return "#E4E8F0"; });
      var chartH = Math.max(180, data.products.length * 36 + 70);
      barEl.style.cssText = "height:" + chartH + "px;width:100%;";
      barEl.innerHTML = "";
      Plotly.react(barEl, [{
        type: "bar", orientation: "h",
        x: vals, y: data.products,
        text: textArr, textposition: "inside",
        insidetextanchor: "middle",
        textfont: { size: 14, color: textColors },
        marker: { color: barColors },
        hovertemplate: "<b>%{y}</b><br>" + xTitle + ": %{text}<extra></extra>",
      }], {
        height: chartH,
        paper_bgcolor: "transparent", plot_bgcolor: "transparent",
        font: { family: "system-ui,-apple-system,sans-serif", size: 13, color: "#E4E8F0" },
        xaxis: { gridcolor: "rgba(255,255,255,0.06)", color: "#7A8399", title: xTitle,
                 tickformat: tickfmt, ticksuffix: ticksfx,
                 zeroline: !isAbs, zerolinecolor: "rgba(255,255,255,0.15)" },
        yaxis: { color: "#7A8399", automargin: true, type: "category", autorange: "reversed" },
        margin: { l: 8, r: 60, t: 4, b: 36 },
        separators: ",.",
      }, _plotlyConfig);
      requestAnimationFrame(function() { try { Plotly.Plots.resize(barEl); } catch(_) {} });
    } catch(e) {
      if (barEl) { barEl.textContent = "Bar chart could not be loaded."; barEl.style.color = "#7A8399"; }
    }
  }


  // Render waterfall slide (1=wf1 summary, 2=wf2 pricing, 3=wf4 mix drivers)
  function _renderTenorWfSlide(prefix, payload, slide) {
    var wfSlotMain = prefix + "-wf-main";
    var wfSlotBg   = prefix + "-wf2-bg";
    var wfSlotWf3  = prefix + "-wf3";
    var compEl  = document.getElementById(prefix + "-wf2-companion");
    var comp3El = document.getElementById(prefix + "-wf3-companion");
    var lblEl   = document.getElementById(prefix + "-wf-label");
    var prevEl  = document.getElementById(prefix + "-wf-prev");
    var nextEl  = document.getElementById(prefix + "-wf-next");
    if (lblEl)  lblEl.textContent = (slide + 1) + " / " + TA_WF_SLIDES.length;
    if (prevEl) prevEl.disabled = slide === 0;
    if (nextEl) nextEl.disabled = slide === TA_WF_SLIDES.length - 1;
    // purge old charts
    destroyChart(wfSlotMain);
    var bgEl = document.getElementById(wfSlotBg);
    if (bgEl) { var ap = bgEl._apexChart; if (ap) ap.destroy(); }
    var wf3El = document.getElementById(wfSlotWf3);
    if (wf3El) { var ap3 = wf3El._apexChart; if (ap3) ap3.destroy(); }
    var key  = TA_WF_SLIDES[slide];
    var fig  = payload && payload[key];
    if (fig) renderChart(wfSlotMain, fig, { height: 400 });
    // Companion: balance delta bar on Pricing Drivers slide (slide 1)
    if (slide === 1 && payload && payload.wf2_bg) {
      if (compEl) compEl.classList.remove("hidden");
      requestAnimationFrame(function() {
        renderChart(wfSlotBg, payload.wf2_bg, { height: 300 });
      });
    } else {
      if (compEl) compEl.classList.add("hidden");
    }
    // Companion: weight changes bar on Mix Drivers slide (slide 2)
    if (slide === 2 && payload && payload.wf3) {
      if (comp3El) comp3El.classList.remove("hidden");
      requestAnimationFrame(function() {
        renderChart(wfSlotWf3, payload.wf3, { height: 280 });
      });
    } else {
      if (comp3El) comp3El.classList.add("hidden");
    }
  }

  // Shared renderer for both monthly & daily tabs.
  // prefix = "ta-mon" or "ta-dly"; isDaily adds daily-only charts; d0/d1 for labels.
  function _renderTenorSnapshot(payload, prefix, isDaily, d0, d1) {
    var buckets = payload.buckets || [];
    var bal0 = payload.balance_t0_m  || [];
    var bal1 = payload.balance_t1_m  || [];
    var delt = payload.balance_delta_m || [];
    var r0   = payload.rate_t0_pct   || [];
    var r1   = payload.rate_t1_pct   || [];
    var ten0 = payload.tenor_t0      || [];   // bucket bazında ağırlıklı ort. gün
    var ten1 = payload.tenor_t1      || [];
    var w0   = payload.weight_t0_pct || [];
    var w1   = payload.weight_t1_pct || [];
    var wat  = payload.wat    || {t0:0, t1:0, delta:0};
    var tot  = payload.totals || {};
    // Display labels use the actual chosen dates instead of t₀/t₁
    var lbl0 = d0 || "t₀";
    var lbl1 = d1 || "t₁";
    // TENOR ↔ DTM modu: eksen/KPI etiketleri aktif moda göre.
    var isDtm = (payload.mode === "dtm");
    var bucketAxisTitle = isDtm ? "Remaining Maturity Bucket (days)" : "Maturity Bucket (days)";
    var tenLabel = isDtm ? "DTM" : "Tenor";   // hover'da ağırlıklı ort. gün etiketi
    _syncTaModeLabels();

    // ── KPI updates ────────────────────────────────────────────────────────
    var el = function(id) { return document.getElementById(id); };
    var fmt = function(v) { return (v == null) ? "–" : v.toLocaleString("tr-TR", {maximumFractionDigits:1}); };
    if (el(prefix + "-wat-t0")) el(prefix + "-wat-t0").textContent = fmt(wat.t0) + " days";
    if (el(prefix + "-wat-t1")) el(prefix + "-wat-t1").textContent = fmt(wat.t1) + " days";
    var dEl = el(prefix + "-wat-delta");
    if (dEl) {
      var sign = (wat.delta >= 0) ? "+" : "";
      dEl.textContent = sign + fmt(wat.delta) + " days";
      dEl.style.color = (wat.delta > 0) ? "#7A9B7E" : (wat.delta < 0) ? "#B8826B" : "var(--text-primary)";
    }

    // ── Drop warning ───────────────────────────────────────────────────────
    var warnEl = el(prefix + "-warning");
    if (warnEl) {
      var drop = Math.max(tot.dropped_t0_pct || 0, tot.dropped_t1_pct || 0);
      var warnParts = [];
      if (payload.mode_note) warnParts.push(payload.mode_note);
      if (buckets.length === 0) {
        warnParts.push("No records with maturity-bucket info for the selected filters/dates. (Only Vadeli / Kasa / O/N products carry a maturity bucket.)");
      } else if (drop > 5.0) {
        warnParts.push(drop.toFixed(1) + "% of total balance excluded from the analysis (no maturity-bucket info).");
      }
      if (warnParts.length) {
        warnEl.classList.remove("hidden");
        warnEl.textContent = warnParts.join(" — ");
      } else {
        warnEl.classList.add("hidden"); warnEl.textContent = "";
      }
    }

    if (buckets.length === 0) {
      ["-ladder", "-ladder-delta", "-curve", "-mix", "-wf-main"].forEach(function(s) {
        var n = el(prefix + s);
        if (n) Plotly.purge(n);
      });
      if (isDaily) { var n2 = el(prefix + "-rate"); if (n2) Plotly.purge(n2); }
      return;
    }

    var COLOR_T0 = "#7A8399", COLOR_T1 = "#D4A574";
    var palette  = _ordinalRamp((payload.buckets || []).length || 5);   // vade bucket'lari ordinal

    // ── Chart 1a: Maturity Ladder ──────────────────────────────────────────
    // İki tarih (t0/t1) YAN YANA; her tarihin mevduatı 0'ın üstünde pozitif,
    // o tarihin swap hedge'i AYNI kolonun tam altında negatif. Barlar açık
    // offset/width ile konumlanır (barmode="overlay") — böylece Plotly'nin
    // "relative" modunun offsetgroup'u yok sayıp tarihleri üst üste yığması
    // önlenir. Bucket başına 2 geniş kolon → barlar/yazılar büyük kalır.
    var _SLOT_W = 0.42, _OFF_T0 = -0.44, _OFF_T1 = 0.02;
    var ladderTextT0 = bal0.map(function(v) { return Math.round(v).toLocaleString("tr-TR"); });
    var ladderTextT1 = bal1.map(function(v) { return Math.round(v).toLocaleString("tr-TR"); });
    // Mevduat hover customdata = [wavg gün, wavg faiz %] (r0/r1 = rate_t*_pct).
    var _depCD = function(ten, rate) {
      return buckets.map(function(_, i) {
        return [(ten && ten[i] != null) ? ten[i] : null, (rate && rate[i] != null) ? rate[i] : null];
      });
    };
    var _depHov = function(lbl) {
      return "<b>%{x}</b><br>" + lbl + ": %{y:,.0f} ₺M<br>WAvg Faiz: %{customdata[1]:.2f}%"
           + "<br>WAvg " + tenLabel + ": %{customdata[0]:,.0f} days<extra></extra>";
    };
    var ladderData = [
      { x: buckets, y: bal0, name: lbl0, type: "bar", marker: { color: COLOR_T0 },
        offset: _OFF_T0, width: _SLOT_W, customdata: _depCD(ten0, r0),
        text: ladderTextT0, textposition: "outside", cliponaxis: false,
        textfont: { size: 11, color: "var(--text-primary)" },
        hovertemplate: _depHov(lbl0) },
      { x: buckets, y: bal1, name: lbl1, type: "bar", marker: { color: COLOR_T1 },
        offset: _OFF_T1, width: _SLOT_W, customdata: _depCD(ten1, r1),
        text: ladderTextT1, textposition: "outside", cliponaxis: false,
        textfont: { size: 11, color: "var(--text-primary)" },
        hovertemplate: _depHov(lbl1) },
    ];
    // Swap hedge overlay (yalnız Daily Evolution) — yaşayan TRY-hedge swap'ları
    // kendi tarihinin mevduat barıyla AYNI offset/width'te, 0'ın altında negatif.
    var hed0 = payload.hedge_t0_m || null;
    var hed1 = payload.hedge_t1_m || null;
    var hten0 = payload.hedge_t0_tenor || [];   // bucket bazında hedge wavg gün
    var hten1 = payload.hedge_t1_tenor || [];
    var hrat0 = payload.hedge_t0_rate || [];    // wavg yıllık effective faiz (%)
    var hrat1 = payload.hedge_t1_rate || [];
    var hasHedge = (hed0 || hed1) &&
      ((hed0 || []).some(function(v) { return v > 0; }) ||
       (hed1 || []).some(function(v) { return v > 0; }));
    if (hasHedge) {
      var negT = function(arr) { return (arr || []).map(function(v) { return -(v || 0); }); };
      var hedText = function(arr) {
        return (arr || []).map(function(v) { return v > 0 ? "-" + Math.round(v).toLocaleString("tr-TR") : ""; });
      };
      // customdata = [hedge ₺M, wavg gün, wavg yıllık compound faiz %] → hover 3'ünü gösterir.
      var hedCD = function(amt, ten, rat) {
        return (amt || []).map(function(v, i) {
          return [v, (ten && ten[i] != null) ? ten[i] : null, (rat && rat[i] != null) ? rat[i] : null];
        });
      };
      var _hedHov = function(lbl) {
        return "<b>%{x}</b><br>Hedge " + lbl + ": %{customdata[0]:,.0f} ₺M<br>WAvg "
             + tenLabel + ": %{customdata[1]:,.0f} days<br>WAvg Rate (annual): "
             + "%{customdata[2]:.2f}%<extra></extra>";
      };
      ladderData.push(
        { x: buckets, y: negT(hed0), name: "Hedge " + lbl0, type: "bar", marker: { color: "#B8826B" },
          offset: _OFF_T0, width: _SLOT_W,
          text: hedText(hed0), textposition: "outside", cliponaxis: false,
          textfont: { size: 11, color: "#B8826B" },
          hovertemplate: _hedHov(lbl0), customdata: hedCD(hed0, hten0, hrat0) },
        { x: buckets, y: negT(hed1), name: "Hedge " + lbl1, type: "bar", marker: { color: "#8A5A44" },
          offset: _OFF_T1, width: _SLOT_W,
          text: hedText(hed1), textposition: "outside", cliponaxis: false,
          textfont: { size: 11, color: "#8A5A44" },
          hovertemplate: _hedHov(lbl1), customdata: hedCD(hed1, hten1, hrat1) });
    }
    renderPlotlyFig(prefix + "-ladder", {
      data: ladderData,
      layout: {
        barmode: "overlay", height: 340, separators: ",.",
        xaxis: { title: bucketAxisTitle },
        yaxis: { title: "Balance (₺M)", zeroline: true, zerolinecolor: "rgba(255,255,255,0.25)" },
        margin: { l: 60, r: 20, t: 64, b: 50 },
        // Baslik SOLA yasli — sag-ustteki legend ile ayni satirda cakismasin.
        title: { text: (hasHedge ? "Balance vs Hedge (" : "Balance (") + lbl0 + " vs " + lbl1 + ")", font: { size: 14 }, x: 0.01, xanchor: "left" },
        // Legend plot ALANININ DISINDA (ustte, sagda) — bar deger etiketleriyle
        // cakismaz, grafigi daraltmaz (t margin genisletildi).
        legend: { orientation: "h", x: 1, xanchor: "right", y: 1.10, yanchor: "bottom",
                  bgcolor: "rgba(0,0,0,0)", font: { size: 11 } },
      },
    }, 340);

    // ── Chart 1b: Balance Change (nominal Δ per bucket) ────────────────────
    // Ladder ile aynı mantık: mevduat değişimi (renk sign'a göre), hedge değişimi
    // AYNI kolonda ladder konvansiyonunda negatif (aşağı) → hed0 - hed1 (hedge
    // arttıysa daha negatif = aşağı). barmode overlay + aynı offset.
    var deltaColors = delt.map(function(v) { return v >= 0 ? "#7A9B7E" : "#B8826B"; });
    var deltaText   = delt.map(function(v) {
      var rounded = Math.round(v);
      var sign = rounded > 0 ? "+" : "";
      return sign + rounded.toLocaleString("tr-TR");
    });
    var _dOff = -0.30, _dW = 0.6;
    var _deltaData = [{
      x: buckets, y: delt, type: "bar", name: "Δ Balance",
      offset: _dOff, width: _dW, marker: { color: deltaColors },
      text: deltaText, textposition: "outside", cliponaxis: false,
      textfont: { size: 11, color: "var(--text-primary)" },
      hovertemplate: "<b>%{x}</b><br>Δ Balance: %{y:+,.0f} ₺M<extra></extra>",
    }];
    if (hasHedge) {
      var hedDelta = buckets.map(function(_, i) {
        return ((hed0 && hed0[i]) || 0) - ((hed1 && hed1[i]) || 0);   // = -(t1-t0)
      });
      var hedDeltaTxt = hedDelta.map(function(v) {
        var r = Math.round(v); return r === 0 ? "" : (r > 0 ? "+" : "") + r.toLocaleString("tr-TR");
      });
      _deltaData.push({
        x: buckets, y: hedDelta, type: "bar", name: "Δ Hedge",
        offset: _dOff, width: _dW, marker: { color: "#8A5A44" },
        text: hedDeltaTxt, textposition: "outside", cliponaxis: false,
        textfont: { size: 11, color: "#8A5A44" },
        hovertemplate: "<b>%{x}</b><br>Δ Hedge: %{y:+,.0f} ₺M<extra></extra>",
      });
    }
    renderPlotlyFig(prefix + "-ladder-delta", {
      data: _deltaData,
      layout: {
        height: 260, barmode: "overlay",
        separators: ",.",
        xaxis: { title: bucketAxisTitle },
        yaxis: { title: "Δ Balance (₺M)" },
        margin: { l: 60, r: 20, t: 40, b: 50 },
        title: { text: (hasHedge ? "Balance & Hedge Change (" : "Balance Change (") + lbl0 + " → " + lbl1 + ")", font: { size: 14 } },
        showlegend: hasHedge,
        legend: { orientation: "h", x: 1, xanchor: "right", y: 1.12, font: { size: 11 } },
        shapes: [{ type: "line", x0: -0.5, x1: buckets.length - 0.5, y0: 0, y1: 0,
                   line: { color: "rgba(255,255,255,0.25)", width: 1 } }],
      },
    }, 260);

    // ── Chart 2: Term Structure (combo: two lines + rate diff bar) ──────────
    var rateDiff = r1.map(function(v, i) { return Math.round((v - r0[i]) * 100); }); // bps, rounded
    var diffColors = rateDiff.map(function(v) { return v >= 0 ? "rgba(122,155,126,0.65)" : "rgba(184,130,107,0.65)"; });
    var diffText   = rateDiff.map(function(v) { return (v >= 0 ? "+" : "") + v + " bps"; });
    renderPlotlyFig(prefix + "-curve", {
      data: [
        { x: buckets, y: rateDiff, name: "Rate Δ (bps)", type: "bar",
          yaxis: "y2",
          marker: { color: diffColors },
          text: diffText, textposition: "outside", cliponaxis: false,
          textfont: { size: 12, color: _plotInk() },
          hovertemplate: "<b>%{x}</b><br>Δ: %{y:+d} bps<extra></extra>",
          showlegend: true },
        { x: buckets, y: r0, name: lbl0, mode: "lines+markers", type: "scatter",
          yaxis: "y",
          line: { color: COLOR_T0, width: 2 }, marker: { size: 8 },
          hovertemplate: "<b>%{x}</b><br>" + lbl0 + ": %{y:.2f}%<extra></extra>" },
        { x: buckets, y: r1, name: lbl1, mode: "lines+markers", type: "scatter",
          yaxis: "y",
          line: { color: COLOR_T1, width: 2 }, marker: { size: 8 },
          hovertemplate: "<b>%{x}</b><br>" + lbl1 + ": %{y:.2f}%<extra></extra>" },
      ],
      layout: {
        height: 360,
        xaxis: { title: bucketAxisTitle },
        yaxis:  { title: "Rate (%)", ticksuffix: "%", side: "left" },
        yaxis2: { title: "Δ (bps)", overlaying: "y", side: "right", showgrid: false,
                  zeroline: true, zerolinecolor: "rgba(255,255,255,0.15)", zerolinewidth: 1 },
        margin: { l: 60, r: 60, t: 50, b: 50 },
        legend: { orientation: "h", x: 0, xanchor: "left", y: 1.12 },
        barmode: "overlay",
      },
    }, 360);

    // ── Chart 3: Bucket Composition (two horizontal stacked bars) ──────────
    var mixTraces = [];
    buckets.forEach(function(b, i) {
      mixTraces.push({
        y: [lbl0, lbl1],
        x: [w0[i], w1[i]],
        name: b, orientation: "h", type: "bar",
        marker: { color: palette[i % palette.length] },
        hovertemplate: "<b>" + b + "</b><br>Share: %{x:.2f}%<extra></extra>",
      });
    });
    renderPlotlyFig(prefix + "-mix", {
      data: mixTraces,
      layout: {
        height: 200, barmode: "stack",
        xaxis: { title: "Composition (%)", ticksuffix: "%", range: [0, 100] },
        yaxis: {
          title: "",
          type: "category",
          categoryorder: "array",
          categoryarray: [lbl1, lbl0],
          automargin: true,
        },
        margin: { l: 110, r: 20, t: 10, b: 50 },
        legend: { orientation: "h", x: 0, y: -0.5 },
      },
    }, 220);

    // ── Chart 3b: Composition Δ (w1 - w0, vertical bar per bucket) ──────────
    var mixDelta    = w1.map(function(v, i) { return parseFloat((v - w0[i]).toFixed(2)); });
    var mixDeltaCol = mixDelta.map(function(v) { return v >= 0 ? "#7A9B7E" : "#B8826B"; });
    var mixDeltaTxt = mixDelta.map(function(v) { return (v >= 0 ? "+" : "") + v.toFixed(2) + "%"; });
    renderPlotlyFig(prefix + "-mix-delta", {
      data: [{
        x: buckets, y: mixDelta, type: "bar",
        marker: { color: mixDeltaCol },
        text: mixDeltaTxt, textposition: "outside", cliponaxis: false,
        textfont: { size: 12, color: _plotInk() },
        hovertemplate: "<b>%{x}</b><br>Δ Share: %{y:+.2f}%<extra></extra>",
      }],
      layout: {
        height: 240,
        xaxis: { title: bucketAxisTitle },
        yaxis: { title: "Δ Composition (%)", ticksuffix: "%" },
        margin: { l: 60, r: 20, t: 40, b: 50 },
        title: { text: "Composition Change (" + lbl0 + " → " + lbl1 + ")", font: { size: 14 } },
        shapes: [{ type: "line", x0: -0.5, x1: buckets.length - 0.5, y0: 0, y1: 0,
                   line: { color: "rgba(255,255,255,0.25)", width: 1 } }],
      },
    }, 240);

    // ── Chart 4: Bucket Rate Waterfall (3-slide carousel) ──────────────────
    var wfSlide = prefix === "ta-mon" ? taMonWfSlide : taDlyWfSlide;
    _renderTenorWfSlide(prefix, payload, wfSlide);

    // Bucket ekseni (X) seçimi → MATURITY_BUCKET gruplama. Tenor bar
    // grafiklerinin X ekseni bucket'lı; Y sayısal (dim yok → tıklanmaz). Numerik
    // → Ctrl+tık aradaki bucket'ları da seçer; Enter grup kurar, tüm tenor
    // grafiklerini yeniden çeker (backend MATURITY_BUCKET merge'i honor eder).
    (function() {
      var _isMon = (prefix === "ta-mon");
      var _cfg = {
        allX: buckets, allY: [], colVals: buckets, rowVals: [],
        colDim: "MATURITY_BUCKET", rowDim: null,
        colNumeric: true, rowNumeric: false,
        meta:   function() { return taFilterMeta; },
        state:  function() { return _isMon ? taMonBubState : taDlyBubState; },
        merges: function() { return _isMon ? taMonBubMerges : taDlyBubMerges; },
        apply:  function() {
          var st = _isMon ? taMonBubState : taDlyBubState;
          var mg = _isMon ? taMonBubMerges : taDlyBubMerges;
          var pid = _isMon ? "ta-mon-filters" : "ta-dly-filters";
          var rf = _isMon ? fetchTenorMonthly : fetchTenorDaily;
          _renderBubFilters(pid, taFilterMeta, st, mg, function() { rf(); });
          rf();
        },
      };
      [prefix + "-ladder", prefix + "-ladder-delta", prefix + "-curve",
       prefix + "-mix-delta"].forEach(function(id) { _attachHmAxisSelect(id, _cfg); });
    })();

    // ── Daily-only: per-bucket rate evolution time series ─────────────────
    if (isDaily && payload.daily_evolution) {
      var ev = payload.daily_evolution;
      var dates = ev.dates || [];
      var evBuckets = ev.buckets || [];
      var rateTraces = evBuckets.map(function(b, i) {
        return {
          x: dates, y: (ev.rate_pct && ev.rate_pct[b]) || [],
          name: b, type: "scatter", mode: "lines",
          line: { color: palette[i % palette.length], width: 2 },
          connectgaps: true,
          hovertemplate: "<b>" + b + "</b><br>%{x}<br>Rate: %{y:.2f}%<extra></extra>",
        };
      });
      renderPlotlyFig(prefix + "-rate", {
        data: rateTraces,
        layout: {
          height: 340,
          xaxis: { title: "" },
          yaxis: { title: "Weighted-Avg Rate (%)", ticksuffix: "%" },
          margin: { l: 60, r: 20, t: 10, b: 50 },
          legend: { orientation: "h", x: 0, y: -0.2 },
        },
      }, 340);
      // WAT sparkline
      renderPlotlyFig("ta-dly-wat-spark", {
        data: [{
          x: dates, y: ev.wat_series || [], type: "scatter", mode: "lines",
          // tozeroy dolgusu duz (flat) WAT serisinde 60px'lik amber blok gibi
          // gorunuyordu -> sade cizgi; eksen veri araligina oturur.
          line: { color: "#D4A574", width: 1.5 },
          hovertemplate: "%{x}<br>WAT: %{y:.0f} days<extra></extra>",
        }],
        layout: {
          height: 60, showlegend: false,
          xaxis: { visible: false }, yaxis: { visible: false },
          margin: { l: 0, r: 0, t: 0, b: 0 },
        },
      }, 60);
    }
  }

  // ── Daily Deposit Detail functions ──────────────────────────────────────────
  function renderDddSlide(idx) {
    dddSlide = idx;
    var key = DDD_SLIDES[idx];
    // Unlock accordion height so newly-shown companions aren't clipped by a
    // previously-captured scrollHeight pixel value.
    var body = document.getElementById("acc-body-ddd-wf");
    if (body) { body.style.maxHeight = "none"; body.style.overflow = "visible"; }
    var stale = document.getElementById("ddd-drill-row");
    if (stale) stale.remove();
    destroyChart("ddd-wf-main");
    destroyChart("ddd-wf3");
    destroyChart("ddd-wf2-bg");
    function _dddDrill(product, anchorEl) {
      var inp0 = document.getElementById("ddd-date0");
      var inp1 = document.getElementById("ddd-date1");
      var d0 = inp0 ? inp0.value : "";
      var d1 = inp1 ? inp1.value : "";
      var dims = DDD_DIMS.filter(function(d) { return dddDims[d]; });
      var bd = (document.getElementById("ddd-break-dim") || {}).value || "PRODUCT";
      _showDepositDrillDown("ddd-drill-row", product, d0, d1, dims, anchorEl,
                            { breakDim: bd, source: "daily", prefix: "ddd" });
    }
    if (dddFigs && dddFigs[key]) {
      var wfOpts = { height: 420 };
      if (idx === 1 || idx === 2) {
        wfOpts.onRelativeBarDblClick = function(bar) {
          _dddDrill(bar.x, document.getElementById("ddd-wf-main"));
        };
      }
      renderChart("ddd-wf-main", dddFigs[key], wfOpts);
    }
    var comp2 = document.getElementById("ddd-wf2-companion");
    if (idx === 1 && dddFigs && dddFigs.wf2_bg) {
      if (comp2) comp2.classList.remove("hidden");
      // Defer to next frame so the layout settles after un-hiding the card.
      requestAnimationFrame(function() {
        renderChart("ddd-wf2-bg", dddFigs.wf2_bg, {
          height: 300,
          onBarDblClick: function(cat) { _dddDrill(cat, document.getElementById("ddd-wf2-bg")); },
        });
      });
    } else {
      if (comp2) comp2.classList.add("hidden");
    }
    var companion = document.getElementById("ddd-wf-companion");
    if (idx === 2 && dddFigs && dddFigs.wf3) {
      if (companion) companion.classList.remove("hidden");
      requestAnimationFrame(function() {
        renderChart("ddd-wf3", dddFigs.wf3, {
          height: 320,
          onBarDblClick: function(cat) { _dddDrill(cat, document.getElementById("ddd-wf3")); },
        });
      });
    } else {
      if (companion) companion.classList.add("hidden");
    }
    var btnPrev = document.getElementById("ddd-prev");
    var btnNext = document.getElementById("ddd-next");
    if (btnPrev) btnPrev.disabled = idx === 0;
    if (btnNext) btnNext.disabled = idx === DDD_SLIDES.length - 1;
    var lbl = document.getElementById("ddd-slide-label");
    if (lbl) lbl.textContent = (idx + 1) + " / " + DDD_SLIDES.length;
  }

  async function loadDailyDepositDates() {
    if (dddDatesLoaded) return;
    try {
      var res = await fetch("/api/daily_deposit_dates");
      var jd  = await res.json();
      if (!jd.ok) return;
      var dates = jd.dates || [];
      dddDateSet = new Set(dates);
      var inp0  = document.getElementById("ddd-date0");
      var inp1  = document.getElementById("ddd-date1");
      var hint  = document.getElementById("ddd-date-hint");
      if (dates.length > 0) {
        var minD = dates[0], maxD = dates[dates.length - 1];
        inp0.min = minD; inp0.max = maxD;
        inp1.min = minD; inp1.max = maxD;
        // Default start = most recent Thursday strictly before maxD
        inp0.value = _prevThursday(dates, maxD);
        inp1.value = maxD;
        if (hint) hint.textContent = "(" + minD + " — " + maxD + ")";
      }
      dddDatesLoaded = true;
    } catch(e) { /* silent */ }
  }

  function _showDddWarning(msg) {
    var w = document.getElementById("ddd-warning");
    if (!w) return;
    w.textContent = msg;
    w.classList.toggle("hidden", !msg);
    if (msg) {
      // Clear any stale chart on warning so the user isn't misled
      destroyChart("ddd-wf-main");
      destroyChart("ddd-wf3");
      destroyChart("ddd-wf2-bg");
      var c2 = document.getElementById("ddd-wf2-companion");
      var c3 = document.getElementById("ddd-wf-companion");
      if (c2) c2.classList.add("hidden");
      if (c3) c3.classList.add("hidden");
    }
  }

  async function fetchDailyDepositWaterfalls() {
    var inp0 = document.getElementById("ddd-date0");
    var inp1 = document.getElementById("ddd-date1");
    var d0 = inp0 ? inp0.value : "", d1 = inp1 ? inp1.value : "";
    if (!d0 || !d1) { _showDddWarning("Please select two dates."); return; }
    if (d0 === d1)  { _showDddWarning("Select two different dates."); return; }
    if (dddDateSet) {
      var missing = [];
      if (!dddDateSet.has(d0)) missing.push(d0);
      if (!dddDateSet.has(d1)) missing.push(d1);
      if (missing.length) {
        _showDddWarning("No data for these date(s): " + missing.join(", "));
        return;
      }
    }
    _showDddWarning("");
    var dims = DDD_DIMS.filter(function(d) { return dddDims[d]; });
    try {
      var r = await fetch("/api/daily_deposit_waterfalls?date_0=" + encodeURIComponent(d0) +
                          "&date_1=" + encodeURIComponent(d1) +
                          "&dims=" + encodeURIComponent(dims.join(",")) + _rateConvQS("ddd") + _bscDemandQS());
      var data = await r.json();
      if (!data.ok) {
        _showDddWarning(data.error || "Data could not be loaded.");
        return;
      }
      dddFigs  = data.figs || {};
      dddSlide = 0;
      if (!dddWfOpen) {
        dddWfOpen = true;
        var btn  = document.getElementById("acc-btn-ddd-wf");
        var body = document.getElementById("acc-body-ddd-wf");
        if (btn)  btn.classList.add("open");
        if (body) { body.style.maxHeight = "none"; body.style.overflow = "visible"; }
      }
      renderDddSlide(0);
      dddBubMeta   = (dddFigs && dddFigs.bubble_filter_meta)  || {};
      dddBubPdims  = (dddFigs && dddFigs.bubble_product_dims) || {};
      dddBubFilter = {};
      // dddBubMerges SIFIRLANMAZ — gruplama ortak hafızada (sharedDimMerges).
      dddBubAggMembers = {};
      _bubSplit["ddd"] = {};      // yeni veri → per-bubble kırılım + seçim sıfırlanır
      _bubSel["ddd"] = null;
      // Tam-ekran SUBPRODUCT chip'i açıksa taze state objelerine yeniden bağla.
      _renderFsSubprodChip("ddd");
      // Tam-ekran tarih slider'ı açıksa seriyi tazele (bkz. ca-mon muadili).
      _tlInvalidate("ddd");
      _renderBubFilters("ddd-bub-filters", dddBubMeta, dddBubFilter, dddBubMerges, function() {
        _renderDddBubbles();
        _fetchCaRateHeatmap("ddd");   // filtre değişimi rate heatmap'e de uygulanır
      });
      requestAnimationFrame(function() { _renderDddBubbles(); });
      // Rate heatmap
      _renderCaRateFromState("ddd", dddFigs, d0, d1);
    } catch(e) {
      _showDddWarning("Request error: " + (e && e.message ? e.message : e));
    }
  }

  function _renderDddBubbles() {
    if (!dddFigs || !dddFigs.bubble_balance) return;
    // activeDims = aktif ekran boyutları (MATURITY_BUCKET HARİÇ) — bkz. _renderCaMonBubbles.
    _renderBubbles("ddd-bub-bal", "ddd-bub-rate",
                   dddFigs.bubble_balance, dddFigs.bubble_rate,
                   dddBubFilter, dddBubMerges, dddBubPdims, dddBubAggMembers, "ddd",
                   DDD_DIMS.filter(function(d) { return dddDims[d]; }));
    requestAnimationFrame(function() {
      _toggleBubLabels("ddd-bub-bal", "ddd-bub-rate", false);
      var _dddBubCtx = function() {
        var inp0 = document.getElementById("ddd-date0");
        var inp1 = document.getElementById("ddd-date1");
        return { d0: inp0 ? inp0.value : "", d1: inp1 ? inp1.value : "",
                 dims: DDD_DIMS.filter(function(d) { return dddDims[d]; }),
                 aggMembers: dddBubAggMembers,
                 breakDim: (document.getElementById("ddd-break-dim") || {}).value || "PRODUCT",
                 source: "daily", prefix: "ddd" };
      };
      _attachDepositBubbleDrill("ddd-bub-bal",  "ddd-bubble-drill", _dddBubCtx);
      _attachDepositBubbleDrill("ddd-bub-rate", "ddd-bubble-drill", _dddBubCtx);
    });
  }

  function toggleSection(name) {
    var s = sections[name];
    s.open = !s.open;
    var btn  = document.getElementById("acc-btn-" + name);
    var body = document.getElementById("acc-body-" + name);
    btn.classList.toggle("open", s.open);
    if (s.open) {
      // Expand: animate from 0 → scrollHeight, then release to 'none' so
      // content (e.g. AG Grid autoHeight) can grow freely after opening.
      body.style.maxHeight = body.scrollHeight + "px";
      body.addEventListener("transitionend", function onEnd() {
        body.removeEventListener("transitionend", onEnd);
        if (s.open) body.style.maxHeight = "none";
      });
      if (s.dirty) fetchSection(name);
    } else {
      // Collapse: lock current height first (needed if maxHeight was 'none'),
      // force a reflow, then animate to 0.
      body.style.maxHeight = body.scrollHeight + "px";
      body.offsetHeight; // force reflow
      body.style.maxHeight = "0";
    }
  }

  async function fetchSection(name) {
    try {
      if (currentPage === "cross-scenario") {
        if (name === "historic")       await fetchCrossHistoric();
        else if (name === "waterfall") await fetchCrossWaterfall();
        else if (name === "table")     await fetchCrossRawData();
      } else {
        if (name === "historic")       await fetchHistoric();
        else if (name === "waterfall") await fetchWaterfall();
        else if (name === "table")     await fetchRawData();
      }
    } catch(e) {
      showError(e.message || "Hata");
    }
  }

  // Mark all sections dirty; immediately re-fetch any that are currently open
  function onParamsChange() {
    highlightedProduct = null;
    pendingNavigation  = null;
    Object.keys(sections).forEach(function(k) { sections[k].dirty = true; });
    Object.keys(sections).forEach(function(k) {
      if (sections[k].open) fetchSection(k);
    });
    bseDataA = null;
    Object.keys(bseSections).forEach(function(k) { bseSections[k].dirty = true; });
    Object.keys(bseSections).forEach(function(k) {
      if (bseSections[k].open) fetchBseSection(k);
    });
    if (currentTab === "deposit-detail") {
      fetchDepositDetailWaterfalls();
    }
  }

  function getSourceForApi() {
    return currentDataSource;
  }

  function setActiveNav() {
    document.querySelectorAll("#report-nav a").forEach(a => {
      var active = currentPage === "standard" && !simScenarioMode && a.dataset.source === currentDataSource;
      a.classList.toggle("active", active);
    });
    document.querySelectorAll("#analysis-nav a, #manual-nav a").forEach(a => {
      var active;
      if (a.dataset.page === "sim-scenario") {
        active = currentPage === "standard" && simScenarioMode;
      } else {
        active = currentPage === a.dataset.page;
      }
      a.classList.toggle("active", active);
    });
    document.querySelectorAll("#deposit-nav a, #np-nav a, #sector-nav a").forEach(a => {
      a.classList.toggle("active", currentPage === a.dataset.page);
    });
  }

  function setSimScenarioMode() {
    simScenarioMode   = true;
    crossScenarioMode = false;
    currentPage = "standard";
    currentDataSource = document.getElementById("scenarioName").value;
    wfSlide = 0; wfFigs = null;
    currentTab = "nim-evolution";
    document.querySelectorAll(".nim-tab-btn").forEach(function(b) {
      b.classList.toggle("active", b.dataset.tab === "nim-evolution");
    });
    Object.keys(sections).forEach(function(k) { sections[k].dirty = true; });
    bseDataA = null;
    Object.keys(bseSections).forEach(function(k) { bseSections[k].dirty = true; });
    document.getElementById("scenario-label").style.display = "";
    updatePageVisibility();
    setActiveNav();
    updateTitle();
    refreshDates();
  }

  // PRISMA 12-ton kategorik palet — tüm multi-series chart'larda bu kullanılır.
  var _PRISMA_CAT = [
    "#4A6B8A","#6B8FA8","#7A9B7E","#9BAE8A",
    "#B8946A","#D4A574","#B8826B","#A06B6B",
    "#8B7BA8","#7B6B95","#6B7589","#8B95A7",
  ];

  // Moda göre grafik mürekkep rengi — trace-level textfont'lar render anında
  // sabitlendiğinden CSS var kullanamaz; bu helper build anında doğru rengi verir
  // (canlı toggle'da sweepPlotly textfont restyle'ı devralır).
  function _plotInk() { return document.body.classList.contains("light-mode") ? "#2C2A26" : "#E4E8F0"; }

  // ORDINAL boyutlar (AUM bantları, vade bucket'ları — küçükten büyüğe sıralı)
  // kategorik palet DEĞİL sıralı gradient alır: koyu navy → denim → amber.
  // n renk, sabit duraklar arasında lineer interpolasyonla üretilir; böylece
  // stacked chart'ta sıra görsel olarak da okunur (PRISMA renk disiplini).
  var _ORDINAL_STOPS = ["#1F3A55","#2D4B6E","#4A6B8A","#6B8FA8","#8B8F86","#B8946A","#D4A574","#E8B988"];
  function _ordinalRamp(n) {
    if (n <= 0) return [];
    if (n === 1) return [_ORDINAL_STOPS[4]];
    var hex = function(c) { return [parseInt(c.slice(1,3),16), parseInt(c.slice(3,5),16), parseInt(c.slice(5,7),16)]; };
    var out = [];
    for (var i = 0; i < n; i++) {
      var t = i / (n - 1) * (_ORDINAL_STOPS.length - 1);
      var lo = Math.floor(t), hi = Math.min(lo + 1, _ORDINAL_STOPS.length - 1), f = t - lo;
      var a = hex(_ORDINAL_STOPS[lo]), b = hex(_ORDINAL_STOPS[hi]);
      out.push("#" + a.map(function(av, ci) {
        var v = Math.round(av + (b[ci] - av) * f);
        return (v < 16 ? "0" : "") + v.toString(16);
      }).join(""));
    }
    return out;
  }

  const LINE_COLORS = {
    "Total NIM":               "#D4A574",  // amber
    "TRY NIM":                 "#4A6B8A",  // denim
    "Realized":                "#7A8399",  // gri (korunuyor)
    "Scenario 1":              "#D4A574",  // amber
    "Scenario 2":              "#7A9B7E",  // adaçayı
    "SC1":                     "#7A9B7E",  // adaçayı
    "SC2":                     "#4A6B8A",  // denim
    "Loans":                   "#7A9B7E",  // adaçayı
    "Time Deposits":           "#B8946A",  // soluk amber
    "Time + Demand Deposits":  "#8B7BA8",  // lavanta
  };

  function destroyChart(id) {
    if (chartInstances[id]) {
      chartInstances[id].destroy();
      delete chartInstances[id];
    }
  }

  function renderChart(id, fig, opts) {
    if (!fig) return;
    if (fig.type === "waterfall")  renderWaterfall(id, fig, opts);
    else if (fig.type === "bar")   renderBarChart(id, fig, opts);
    else if (fig.type === "bar-growth") renderBarGrowthChart(id, fig, opts);
    else if (fig.type === "line")  renderLineChart(id, fig, opts);
  }

  // Render a raw Plotly figure (used by Cost Analysis bubble charts).
  function renderPlotlyFig(id, fig, height) {
    var el = document.getElementById(id);
    if (!el || !fig || !fig.data) return;
    // Tam ekrandaysa (bubble min-size slider'ı yeniden render tetiklediğinde)
    // sabit yüksekliği DAYATMA — overlay gövdesinin yüksekliğini koru; aksi halde
    // grafik fullscreen'de 380px'e küçülürdü.
    if (height) {
      var _fsBody = el.closest && el.closest(".chart-fs-body");
      // BSC Presentation içinde sabit 380px küçük kalır → görünür alanın ~%55'i.
      var _bscC = !_fsBody && el.closest && el.closest("#bsc-content");
      el.style.height = (_fsBody ? Math.max(200, _fsBody.clientHeight - 16)
                        : _bscC ? Math.max(height, Math.round(window.innerHeight * 0.55))
                        : height) + "px";
    }
    var _axisBase = { gridcolor: "rgba(255,255,255,0.06)", zerolinecolor: "rgba(255,255,255,0.12)", linecolor: "rgba(255,255,255,0.06)", color: "#7A8399", tickfont: { size: 12, color: "#7A8399" } };
    var darkOverride = {
      paper_bgcolor: "transparent",
      plot_bgcolor:  "transparent",
      font:       { family: "system-ui, -apple-system, sans-serif", size: 13, color: "#E4E8F0" },
      legend:     { bgcolor: "rgba(0,0,0,0)", font: { size: 11, color: "#7A8399" } },
      hoverlabel: { bgcolor: "#131826", bordercolor: "rgba(255,255,255,0.15)", font: { family: "system-ui, -apple-system, sans-serif", size: 12, color: "#E4E8F0" } },
      xaxis:  _axisBase,
      yaxis:  _axisBase,
      yaxis2: _axisBase,
    };
    var layout = Object.assign({}, fig.layout || {}, darkOverride);
    if ((fig.layout || {}).xaxis)  layout.xaxis  = Object.assign({}, fig.layout.xaxis,  _axisBase);
    if ((fig.layout || {}).yaxis)  layout.yaxis  = Object.assign({}, fig.layout.yaxis,  _axisBase);
    if ((fig.layout || {}).yaxis2) layout.yaxis2 = Object.assign({}, fig.layout.yaxis2, _axisBase);
    // Legend: tema (renk/font) + çağıranın POZİSYONU birlikte yaşasın. Eskiden
    // darkOverride.legend çağıranın legend'ını komple eziyordu → orientation/x/y
    // sessizce kayboluyor, legend hep Plotly default'una (sağ-dikey) düşüyordu.
    if ((fig.layout || {}).legend) {
      layout.legend = Object.assign({}, darkOverride.legend, fig.layout.legend);
      layout.legend.font = Object.assign({}, darkOverride.legend.font,
                                          (fig.layout.legend.font || {}));
    }
    // Patch title font: TEMA HER ZAMAN KAZANIR. Eski merge sırası backend'den
    // gelen koyu title rengini (#1a202c) korunuyordu → dark modda başlık
    // görünmezdi ("Balance Evolution" vb). Boyut 14px + PRISMA metin rengi
    // zorlanır; light modda _themeLayout başlığı krem-uyumluya çevirir.
    if (layout.title) {
      var _t = typeof layout.title === "string" ? { text: layout.title } : Object.assign({}, layout.title);
      _t.font = Object.assign({}, _t.font, { size: 14, color: "#E4E8F0" });
      layout.title = _t;
    }
    Plotly.react(el, fig.data, layout, _plotlyConfig);
  }

  // Attach a plotly_click listener on a deposit bubble chart element.
  // contextGetter() → { d0, d1, dims, align? } — called at click time so values
  // are fresh. drillRowId is shared across both bubble charts in the same group
  // so clicking either one replaces the same drill panel.
  function _attachDepositBubbleDrill(bubbleId, drillRowId, contextGetter) {
    var el = document.getElementById(bubbleId);
    if (!el) return;
    el.removeAllListeners && el.removeAllListeners("plotly_click");
    function doDrill(product, ctx) {
      var passOpts = {};
      if (ctx.align) passOpts.align = ctx.align;
      // Kırılım (breakdown) bar'ı — waterfall drill'iyle AYNI: contextGetter
      // breakDim/source/prefix verirse _showDepositDrillDown combo grafiğin
      // altına Balance Δ / Rate Δ toggle'lı yatay bar kırılımını ekler.
      if (ctx.breakDim) { passOpts.breakDim = ctx.breakDim; passOpts.source = ctx.source; passOpts.prefix = ctx.prefix; }
      // When the clicked bubble is a merged/aggregated point, pass its underlying
      // product list so the drill-down sums them server-side.
      var members = ctx.aggMembers && ctx.aggMembers[product];
      if (members && members.length && !(members.length === 1 && members[0] === product)) {
        passOpts.members = members;
      }
      _showDepositDrillDown(drillRowId, product, ctx.d0, ctx.d1, ctx.dims, el,
                            Object.keys(passOpts).length ? passOpts : null);
    }
    el.on("plotly_click", function(ev) {
      if (!ev || !ev.points || !ev.points.length) return;
      var pt = ev.points[0];
      // Nokta kimliği: customdata'daki NAME (smart-label text'i inceltebilir).
      var nameIdx = (bubbleId.indexOf("-bub-rate") >= 0) ? 2 : 3;
      var product = (pt.customdata && pt.customdata[nameIdx]) || pt.text
                    || (pt.customdata && pt.customdata[0]) || "";
      if (!product) return;
      var ctx = contextGetter();
      // Outstanding Cost (ca-mon/ddd): TEK TIK = seç, ÇİFT TIK = drill.
      // Plotly dblclick point bilgisi vermediğinden 300ms'lik zamanlayıcıyla
      // ayrıştırılır: pencere içinde aynı noktaya 2. tık → drill; süre dolarsa seç.
      if (ctx.prefix && _BUB_SELECT_PREFIXES[ctx.prefix]) {
        var pfx = ctx.prefix;
        if (_bubClickTimer[pfx] && _bubClickLast[pfx] === product) {
          clearTimeout(_bubClickTimer[pfx]);
          _bubClickTimer[pfx] = null;
          doDrill(product, ctx);
          return;
        }
        if (_bubClickTimer[pfx]) clearTimeout(_bubClickTimer[pfx]);
        _bubClickLast[pfx] = product;
        _bubClickTimer[pfx] = setTimeout(function() {
          _bubClickTimer[pfx] = null;
          _toggleBubSelect(pfx, product);
        }, 300);
        return;
      }
      doDrill(product, ctx);
    });
  }

  // Fetch daily time series for a product and render a dual-axis combo chart
  // (line = interest rate %, bar = balance ₺M) below the trigger element's card.
  // drillRowId: unique ID so same panel is replaced on repeated clicks.
  // opts.align="monthly"   → backend expands d0 to month start and d1 to last
  //                           available daily-data DAT inside d1's month.
  // opts.members=[..ids..] → backend aggregates these underlying products
  //                           together (for merged bubbles).
  async function _showDepositDrillDown(drillRowId, product, d0, d1, dims, triggerEl, opts) {
    opts = opts || {};
    var drillChartId = drillRowId + "-chart";
    var existing = document.getElementById(drillRowId);
    if (existing) existing.remove();

    var anchorEl = triggerEl ? (triggerEl.closest(".card") || triggerEl) : null;
    if (!anchorEl) return;

    // Kırılım (breakdown) bölümü — yalnız çağıran opts.breakDim verirse
    // (Cost Analysis waterfall/bar drill'i). Balance Δ (default) ↔ Rate Δ (bps)
    // slider'ı ile metrik değişir; veri /api/hm_product_bar'dan bileşik anahtarla.
    var showBreak = !!opts.breakDim;
    var breakDim  = opts.breakDim || "";
    var breakLbl  = showBreak ? _baDimLabel(breakDim) : "";

    var url = "/api/deposit_product_daily"
      + "?product=" + encodeURIComponent(product)
      + "&date_0="  + encodeURIComponent(d0)
      + "&date_1="  + encodeURIComponent(d1)
      + "&dims="    + encodeURIComponent((dims || []).join(","));
    if (opts.align) url += "&align=" + encodeURIComponent(opts.align);
    // Rate Type: Cost sayfalarından açılan drill combo çizgisi de seçime uyar.
    if (opts.prefix && (opts.prefix === "ca-mon" || opts.prefix === "ddd")) {
      url += _rateConvQS(opts.prefix);
    }
    if (opts.members && opts.members.length) {
      url += "&members=" + encodeURIComponent(opts.members.join(","));
    }

    var drillRow = document.createElement("div");
    drillRow.id        = drillRowId;
    drillRow.className = "card";
    drillRow.style.cssText = "position:relative;margin-top:16px;padding:16px;";
    drillRow.innerHTML =
      '<button onclick="document.getElementById(\'' + drillRowId + '\').remove()"' +
        ' style="position:absolute;top:8px;right:10px;background:none;border:none;' +
        'cursor:pointer;font-size:16px;color:var(--text-secondary);z-index:2;" title="Kapat">✕</button>' +
      '<div style="font-size:12px;font-weight:600;color:var(--text-primary);margin-bottom:8px;">' +
        'Daily Detail — ' + product + '</div>' +
      '<div id="' + drillChartId + '" style="height:340px;"></div>' +
      (showBreak
        ? '<div style="height:1px;background:rgba(255,255,255,0.07);margin:12px 0;"></div>' +
          '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap;">' +
            '<span style="font-size:11px;color:var(--text-secondary);">' + breakLbl + ' Breakdown</span>' +
            '<div class="hm-switch" id="' + drillRowId + '-metric-switch" style="margin-left:auto;">' +
              '<span class="hm-lbl active" data-metric="balance">Balance &#916;</span>' +
              '<div class="hm-toggle"><div class="hm-knob"></div></div>' +
              '<span class="hm-lbl" data-metric="rate">Rate &#916; (bps)</span>' +
            '</div>' +
          '</div>' +
          '<div id="' + drillRowId + '-bar" style="min-height:80px;width:100%;display:flex;' +
            'align-items:center;justify-content:center;color:var(--text-secondary);font-size:12px;">Loading…</div>'
        : "");
    anchorEl.insertAdjacentElement("afterend", drillRow);

    // Kırılım slider'ı (Balance Δ ↔ Rate Δ) + ilk render.
    if (showBreak) {
      var _barEl = document.getElementById(drillRowId + "-bar");
      if (_barEl) _barEl.__wfMetric = "balance";
      var _sw = document.getElementById(drillRowId + "-metric-switch");
      if (_sw) _sw.addEventListener("click", function(ev) {
        var lbl = ev.target.closest(".hm-lbl");
        var m = (lbl && lbl.dataset.metric) ? lbl.dataset.metric
                : ((_barEl && _barEl.__wfMetric === "balance") ? "rate" : "balance");
        if (_barEl) { _barEl.__wfMetric = m; if (_barEl.__wfData) _drawWfBreakdownBar(_barEl, m); }
        _sw.classList.toggle("is-right", m === "rate");
        _sw.querySelectorAll(".hm-lbl").forEach(function(l) {
          l.classList.toggle("active", l.dataset.metric === m);
        });
      });
      _renderWfBreakdownBar(drillRowId, product, dims,
        opts.origD0 || d0, opts.origD1 || d1, breakDim,
        opts.source || "daily", opts.prefix || "");
    }

    try {
      var r    = await fetch(url);
      var data = await r.json();
      if (!data.ok || !data.dates || !data.dates.length) {
        document.getElementById(drillChartId).textContent = "No daily data found.";
        return;
      }
      var drillEl = document.getElementById(drillChartId);
      var _balVals = (data.balance_m || []).filter(function(v) { return v != null && isFinite(v); });
      var _balMin  = _balVals.length ? Math.min.apply(null, _balVals) : 0;
      var _balMax  = _balVals.length ? Math.max.apply(null, _balVals) : 1;
      var _balSpan = _balMax - _balMin;
      var _balPad  = _balSpan > 0 ? _balSpan * 0.10 : Math.max(Math.abs(_balMax), 1) * 0.10;
      var _balLo   = _balMin - _balPad;
      var _balHi   = _balMax + _balPad;
      Plotly.react(drillEl, [
        {
          x: data.dates,
          y: data.rate_pct,
          name: "Rate (%)",
          type: "scatter",
          mode: "lines+markers",
          line:   { color: "#4A6B8A", width: 2 },
          marker: { size: 4 },
          yaxis:  "y1",
          hovertemplate: "%{x}: %{y:.2f}%<extra>Rate</extra>",
        },
        {
          x: data.dates,
          y: data.balance_m,
          name: "Balance (₺M)",
          type: "bar",
          marker: { color: "rgba(74,107,138,0.45)", line: { color: "#4A6B8A", width: 1 } },
          yaxis:  "y2",
          hovertemplate: "%{x}: %{y:,.1f} ₺M<extra>Balance</extra>",
        }
      ], Object.assign({}, _plotlyDefaults, {
        title:      { text: "", font: { size: 12 } },
        height:     340,
        showlegend: true,
        legend:     { orientation: "h", x: 0, y: 1.12 },
        yaxis:  _axisOpts({ title: { text: "Rate (%)" }, ticksuffix: "%", side: "left" }),
        yaxis2: _axisOpts({ title: { text: "Balance (₺M)" }, side: "right", overlaying: "y", showgrid: false, tickformat: ",.0f", range: [_balLo, _balHi], autorange: false }),
        xaxis:  _axisOpts({ type: "category", tickangle: -30, nticks: 20 }),
        margin: { l: 64, r: 64, t: 40, b: 80 },
        barmode: "overlay",
      }), _plotlyConfig);
    } catch(e) {
      var el2 = document.getElementById(drillChartId);
      if (el2) el2.textContent = "Load error: " + (e && e.message ? e.message : e);
    }

    drillRow.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // Cost Analysis waterfall/bar drill'inin altına kırılım (breakdown) bar'ı çizer.
  // Bileşik waterfall kalemi (product) + aktif dims → /api/hm_product_bar composite
  // yolu; break_dim'e göre kırılır. Balance Δ ve Rate Δ (bps) aynı yanıttan gelir,
  // metrik slider'ı re-fetch YAPMADAN yeniden çizer.
  async function _renderWfBreakdownBar(drillRowId, product, dims, d0, d1, breakDim, source, prefix) {
    var barEl = document.getElementById(drillRowId + "-bar");
    if (!barEl) return;
    var url = "/api/hm_product_bar"
      + "?date_0="    + encodeURIComponent(d0)
      + "&date_1="    + encodeURIComponent(d1)
      + "&source="    + encodeURIComponent(source || "daily")
      + "&break_dim=" + encodeURIComponent(breakDim)
      + "&wf_product="+ encodeURIComponent(product)
      + "&wf_dims="   + encodeURIComponent((dims || []).join(","))
      + (prefix ? _caBubQS(prefix) : "");
    try {
      var r = await fetch(url);
      var data = await r.json();
      if (!data.ok || !data.products || !data.products.length) {
        barEl.style.cssText = "color:var(--text-secondary);font-size:12px;padding:8px 0;";
        barEl.textContent = "No breakdown data found.";
        return;
      }
      barEl.__wfData = data;
      _drawWfBreakdownBar(barEl, barEl.__wfMetric || "balance");
    } catch (e) {
      barEl.style.cssText = "color:var(--text-secondary);font-size:12px;padding:8px 0;";
      barEl.textContent = "Breakdown could not be loaded.";
    }
  }

  function _drawWfBreakdownBar(barEl, metric) {
    var data = barEl.__wfData;
    if (!data || typeof Plotly === "undefined") return;
    var isRate = (metric === "rate");
    var vals   = isRate ? data.delta_bps : data.delta_m;
    var xTitle = isRate ? "Rate Δ (bps)" : "Balance Δ (₺M)";
    // Toggle YENİDEN çizer: önce purge şart — innerHTML tek başına silinirse
    // element üstünde Plotly'nin gd state'i (data/layout) kalır, react "plot
    // zaten var" sanıp SESSİZCE boş render eder (bug: rate'e geçince boş kalıp
    // balance'a dönünce de dolmuyordu).
    try { Plotly.purge(barEl); } catch (e) {}
    var hasVal = (vals || []).some(function(v) { return v != null; });
    if (!hasVal) {
      barEl.style.cssText = "color:var(--text-secondary);font-size:12px;padding:8px 0;";
      barEl.textContent = isRate ? "No rate breakdown data (no overlapping t0/t1 balance)."
                                 : "No balance breakdown data.";
      return;
    }
    var textArr = (vals || []).map(function(v) {
      if (v == null) return "–";
      return isRate ? (v >= 0 ? "+" : "") + Math.round(v) + " bps"
                    : (v >= 0 ? "+" : "") + Math.round(v).toLocaleString("tr-TR") + " ₺M";
    });
    var colors = (vals || []).map(function(v) {
      return v == null ? "#5C6478" : (v >= 0 ? "#7A9B7E" : "#B8826B");
    });
    var prods  = data.products || [];
    var chartH = Math.max(160, prods.length * 34 + 60);
    barEl.style.cssText = "height:" + chartH + "px;width:100%;";
    barEl.innerHTML = "";
    Plotly.react(barEl, [{
      type: "bar", orientation: "h",
      x: vals, y: prods,
      text: textArr, textposition: "inside", insidetextanchor: "middle",
      textfont: { size: 13, color: "#E4E8F0" },
      marker: { color: colors },
      hovertemplate: "<b>%{y}</b><br>" + xTitle + ": %{text}<extra></extra>",
    }], {
      height: chartH, separators: ",.",
      paper_bgcolor: "transparent", plot_bgcolor: "transparent",
      font: { family: "system-ui,-apple-system,sans-serif", size: 12,
              color: (typeof _plotInk === "function") ? _plotInk() : "#E4E8F0" },
      xaxis: { title: { text: xTitle, font: { size: 11 } },
               gridcolor: "rgba(255,255,255,0.06)", zeroline: true,
               zerolinecolor: "rgba(255,255,255,0.15)",
               tickformat: isRate ? ".0f" : ",.0f" },
      yaxis: { automargin: true, type: "category", autorange: "reversed" },
      margin: { l: 10, r: 20, t: 8, b: 46 }, bargap: 0.35,
    }, _plotlyConfig);
  }

  // ── Grafik başlığı → tam-ekran (STANDART, tüm grafikler) ────────────────────
  // Herhangi bir Plotly (.gtitle) veya ApexCharts (.apexcharts-title-text)
  // başlığına tıklanınca o grafiğin GERÇEK DOM elemanı tam-ekran overlay'e
  // TAŞINIR (yeniden render değil → tüm etkileşim/drill korunur), Esc/X ile
  // eski yerine döner. Yeni eklenen her grafik başlık (title.text) taşıdığı
  // sürece bu özelliği otomatik kazanır. Başlıkta görsel değişiklik YAPILMAZ.
  (function initChartFullscreen() {
    var _fs = null;   // { overlay, el, ph, prevCss }
    function _resize(el) {
      try { if (window.Plotly && el.classList.contains("js-plotly-plot")) Plotly.Plots.resize(el); } catch (e) {}
      try { window.dispatchEvent(new Event("resize")); } catch (e) {}
    }
    // Bubble grafiği için (id: <prefix>-bub-bal / -bub-rate) üstündeki min-size
    // slider barını döndürür. Tam ekranda bu bar da overlay'e taşınır ki kullanıcı
    // fullscreen'de de görsel filtreyi değiştirebilsin (bar İKİ grafiği de etkiler).
    function _bubSliderBarFor(el) {
      var m = (el && el.id || "").match(/^(.*)-bub-(bal|rate)$/);
      if (!m) return null;
      return document.getElementById(m[1] + "-bub-minsize-bar");
    }
    // Elemanın ait olduğu Cost sekmesi ("ca-mon" | "ddd" | null) — tam ekranda
    // Detailed Dim / Rate Type seçicilerinin taşınacağı sekmeyi belirler.
    function _costPrefixOf(el) {
      if (!el || !el.closest) return null;
      if (el.closest("#ca-mon-section")) return "ca-mon";
      if (el.closest("#ddd-section")) return "ddd";
      // BSC Presentation Slide 2: bubble sunuma taşınmış — prefix sunum
      // modundan gelir → tam-ekran Detailed Dim / Rate Type / SUBPRODUCT
      // chip'i + tarih slider'ı sunumdan açılan fullscreen'de de kurulur.
      if (el.closest("#bsc-pres") && _bsc && _bsc.slide === 1) return _bscPrefix(1, _bsc.mode);
      return null;
    }
    function _close() {
      if (!_fs) return;
      var f = _fs; _fs = null;
      document.removeEventListener("keydown", _onKey);
      // Tarih slider'ı: timer'ı durdur, hareket kilidini sil; play/drag ortasında
      // kapatıldıysa bekleyen tarihi sayfa Date(End)'ine commit et (refetch).
      try { _tlTeardown(true); } catch (e) {}
      // Taşınan kontrol elemanlarını (Detailed Dim / Rate Type) yerlerine koy.
      (f.ctrls || []).forEach(function (c) {
        if (c.node && c.ph && c.ph.parentNode) {
          c.ph.parentNode.insertBefore(c.node, c.ph);
          c.ph.remove();
        }
      });
      // Slider barını orijinal yerine geri koy (grafikten ÖNCE — placeholder hâlâ DOM'da).
      if (f.sliderBar && f.sliderPh && f.sliderPh.parentNode) {
        f.sliderPh.parentNode.insertBefore(f.sliderBar, f.sliderPh);
        f.sliderPh.remove();
      }
      if (f.ph && f.ph.parentNode) {
        f.el.style.cssText = f.prevCss;               // orijinal boyut/stili geri yükle
        f.ph.parentNode.insertBefore(f.el, f.ph);
        f.ph.remove();
      }
      if (f.overlay && f.overlay.parentNode) f.overlay.remove();
      // Sayfa filtre panelini tazele — overlay'de değişen SUBPRODUCT state'i
      // sayfadaki chip etiketine/checkbox'larına yansısın (aynı obje).
      if (f.pfx) { try { _rebuildCostBubFilterPanel(f.pfx); } catch (e) {} }
      // BSC Presentation Slide 2 açıkken fullscreen tarih slider'ı prezentasyon
      // slider'ını sökmüştü (_tlInit tekil) — kapanınca yeniden kur.
      try { if (typeof _bscOnFsClose === "function") _bscOnFsClose(); } catch (e) {}
      requestAnimationFrame(function () { _resize(f.el); });
    }
    function _onKey(e) { if (e.key === "Escape") _close(); }
    // Overlay İÇİNDE gösterilen grafiği değiştir (sunum slide 2'nin Delta
    // Interest Rate ↔ Delta Balance toggle'ı): eski eleman placeholder'ına
    // döner, yenisi için placeholder açılıp overlay gövdesine taşınır.
    function _swapEl(newEl) {
      if (!_fs || !newEl || newEl === _fs.el) return;
      _fs.el.style.cssText = _fs.prevCss;
      _fs.ph.parentNode.insertBefore(_fs.el, _fs.ph);
      _fs.ph.remove();
      var ph = document.createElement("div");
      ph.style.display = "none";
      newEl.parentNode.insertBefore(ph, newEl);
      var prevCss = newEl.style.cssText;
      var body = _fs.overlay.querySelector(".chart-fs-body");
      body.appendChild(newEl);
      newEl.style.width = "100%";
      newEl.style.height = Math.max(200, body.clientHeight - 16) + "px";
      _fs.el = newEl; _fs.ph = ph; _fs.prevCss = prevCss;
      requestAnimationFrame(function () { _resize(newEl); });
    }
    window._chartFsSwap = _swapEl;
    // isContainer: waterfall carousel gibi ÇOKLU içerik blokları — yükseklik
    // dayatılmaz (kartlar kendi boylarında, gövde kayar), slider bar aranmaz.
    function _open(el, isContainer) {
      if (_fs) return;
      var ph = document.createElement("div"); ph.style.display = "none";
      el.parentNode.insertBefore(ph, el);
      var prevCss = el.style.cssText;
      var overlay = document.createElement("div");
      overlay.className = "chart-fs-overlay";
      var topbar = document.createElement("div"); topbar.className = "chart-fs-topbar";
      var btn = document.createElement("button");
      btn.className = "chart-fs-close"; btn.type = "button";
      btn.innerHTML = "&#10005;"; btn.title = "Kapat (Esc)";
      btn.addEventListener("click", _close);
      topbar.appendChild(btn);
      overlay.appendChild(topbar);
      // Cost sekmelerinde sayfa-üstü Detailed Dim / Rate Type seçicilerini
      // overlay'e TAŞI (klon değil — aynı elemanlar; değişim sayfa state'ini de
      // günceller, refetch mevcut listener'larla tetiklenir ve id'ler moved
      // elemanları hedeflediğinden açık grafik/heatmap yerinde yenilenir).
      var ctrls = [];
      var pfx = _costPrefixOf(el);
      var wantSubprod = false;
      if (pfx) {
        var strip = document.createElement("div"); strip.className = "chart-fs-ctrls";
        [pfx + "-break-dim", pfx + "-rate-conv"].forEach(function (sid) {
          var sel = document.getElementById(sid);
          if (!sel) return;
          var lab = sel.previousElementSibling;
          var nodes = (lab && lab.tagName === "LABEL") ? [lab, sel] : [sel];
          nodes.forEach(function (n) {
            var cph = document.createElement("span"); cph.style.display = "none";
            n.parentNode.insertBefore(cph, n);
            strip.appendChild(n);
            ctrls.push({ node: n, ph: cph });
          });
        });
        // Sunum slide 2'den açıldıysa Delta Interest Rate ↔ Delta Balance
        // toggle'ı da şeride taşınır (tam-ekranda grafik türü değiştirilebilir).
        var mSw = document.getElementById("bsc-bub-metric-switch");
        if (mSw && el.closest && el.closest("#bsc-pres")) {
          var mph = document.createElement("span");
          mph.style.display = "none";
          mSw.parentNode.insertBefore(mph, mSw);
          strip.appendChild(mSw);
          ctrls.push({ node: mSw, ph: mph });
        }
        // Bubble tam-ekranında Rate Type'ın sağına SUBPRODUCT filtresi (sayfa
        // paneliyle aynı state'e bağlı ikinci chip — overlay eklendikten sonra
        // render edilir; refetch sonrası fetch'ler yeniden bağlar).
        if (/-bub-(bal|rate)$/.test(el.id || "")) {
          wantSubprod = true;
          var sHost = document.createElement("span");
          sHost.id = "chart-fs-subprod";
          sHost.dataset.prefix = pfx;
          sHost.style.cssText = "display:inline-flex;align-items:center;";
          strip.appendChild(sHost);
        }
        if (ctrls.length || wantSubprod) overlay.appendChild(strip);
      }
      // Bubble grafiğiyse min-size slider barını da overlay'e taşı (topbar altına).
      var sliderBar = isContainer ? null : _bubSliderBarFor(el), sliderPh = null;
      if (sliderBar) {
        sliderPh = document.createElement("div"); sliderPh.style.display = "none";
        sliderBar.parentNode.insertBefore(sliderPh, sliderBar);
        overlay.appendChild(sliderBar);
      }
      // Cost bubble tam-ekranı: min-size barının ALTINA tarih slider'ı + ▶ play
      // (yalnız seçim-destekli Cost sekmelerinde; bar overlay ile yaşar).
      if (sliderBar && pfx && _BUB_SELECT_PREFIXES[pfx]) _tlInit(pfx, overlay);
      var body = document.createElement("div"); body.className = "chart-fs-body";
      overlay.appendChild(body);
      document.body.appendChild(overlay);
      body.appendChild(el);
      el.style.width = "100%";
      if (isContainer) {
        // Carousel/çoklu kart: iç grafikler kendi boylarında; gövde kayar.
        el.style.maxHeight = "none";
        el.style.overflow = "visible";
      } else {
        // body içerik yüksekliği = clientHeight − alt padding (16). Grafik tek
        // başınayken tam oturur (scrollbar çıkmaz); drill eklenince gövde kayar.
        el.style.height = Math.max(200, body.clientHeight - 16) + "px";
      }
      _fs = { overlay: overlay, el: el, ph: ph, prevCss: prevCss,
              sliderBar: sliderBar, sliderPh: sliderPh, ctrls: ctrls, pfx: pfx };
      // SUBPRODUCT chip'i overlay DOM'a girdikten sonra render edilir.
      if (wantSubprod) _renderFsSubprodChip(pfx);
      document.addEventListener("keydown", _onKey);
      requestAnimationFrame(function () { _resize(el); setTimeout(function () { _resize(el); }, 140); });
    }
    // Bir konteyner içindeki GÖRÜNÜR grafik köklerini bul (Plotly .js-plotly-plot
    // + Apex .apexcharts-canvas'ın kabı). "Görünür" = merkez-x konteynerin
    // (kartın) rect'i içinde → carousel'de ekran dışına kaymış paneli eler.
    function _visibleCharts(container) {
      var out = [], cr = container.getBoundingClientRect();
      container.querySelectorAll(".js-plotly-plot, .apexcharts-canvas").forEach(function (n) {
        var el = n.classList.contains("apexcharts-canvas") ? n.parentNode : n;
        if (!el) return;
        var r = el.getBoundingClientRect();
        if (r.width < 20 || r.height < 20) return;
        var cx = r.left + r.width / 2;
        if (cx >= cr.left - 2 && cx <= cr.right + 2 && out.indexOf(el) < 0) out.push(el);
      });
      return out;
    }
    // Bir başlık (SVG text) elemanının kök grafik elemanını döndürür.
    function _chartOfTitle(ti) {
      return ti.classList.contains("gtitle")
        ? ti.closest(".js-plotly-plot")
        : (function () { var c = ti.closest(".apexcharts-canvas"); return c ? c.parentNode : null; })();
    }
    document.addEventListener("click", function (ev) {
      if (_fs) return;
      var t = ev.target;
      if (!t) return;
      // (1) İç grafik başlığı — Plotly (.gtitle) / Apex (.apexcharts-title-text).
      // SVG <text> yalnız harf glyph'lerinde tıklama alır; kullanıcı harfler
      // arası/çevresine basınca kaçıyordu → başlığın TÜM bounding-box'ı ile
      // hit-test et (satırın her yeri tıklanabilir).
      var x = ev.clientX, y = ev.clientY;
      var titles = document.querySelectorAll(".gtitle, .apexcharts-title-text");
      for (var i = 0; i < titles.length; i++) {
        var r = titles[i].getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        if (x >= r.left - 3 && x <= r.right + 3 && y >= r.top - 3 && y <= r.bottom + 3) {
          var el = _chartOfTitle(titles[i]);
          if (el) {
            ev.preventDefault(); ev.stopPropagation();
            // Cost waterfall carousel'i: tek grafik yerine TÜM slider bloğu
            // (nav butonları + aktif slide + companion bar) tam ekrana taşınır —
            // slide geçişleri ve Pricing/Mix companion'ları overlay içinde çalışır.
            var wfBody = el.closest && el.closest("#acc-body-ca-mon-wf, #acc-body-ddd-wf");
            if (wfBody) { _open(wfBody, true); return; }
            // Tenor "Balance vs Hedge" (ladder): altındaki bitişik Δ Balance
            // bar'ıyla BİRLİKTE tam ekrana taşınır — dashboard'da accordion
            // gövdesi, sunum Slide 5'te iki kartı saran bsc-ladder-wrap.
            var lm = (el.id || "").match(/^(ta-(?:mon|dly))-ladder$/);
            if (lm) {
              var ladCont = (el.closest && el.closest("#bsc-ladder-wrap"))
                          || document.getElementById("acc-body-" + lm[1] + "-ladder");
              if (ladCont) { _open(ladCont, true); return; }
            }
            _open(el);
            return;
          }
        }
      }
      if (!t.closest) return;
      // (2) Grafik-kartı ACCORDION BAŞLIĞI (ör. "Interest Rate Heatmap",
      // "Balance / Customer Heatmap"). İç başlığı prominent olmayan grafikler
      // kullanıcı için kart başlığından açılır. Toggling accordion'lar atlanır:
      // waterfall (-wf, kendi toggle'ı var; iç Apex başlığı zaten çalışır) ve
      // NIM std-section (collapse eden accordion'lar). Kartta TEK görünür grafik
      // varsa o açılır; birden fazla (bubble/composition) → iç başlıklar hallediyor.
      var hdr = t.closest(".accordion-header");
      if (!hdr) return;
      if (/-wf$/.test(hdr.id || "")) return;
      if (hdr.closest("#std-section")) return;
      var acc = hdr.closest(".accordion");
      if (!acc) return;
      var vis = _visibleCharts(acc);
      if (vis.length === 1) _open(vis[0]);
    });
  })();

  function renderWaterfall(id, data, opts) {
    opts = opts || {};
    var chartHeight = opts.height || 320;
    destroyChart(id);

    // Strip stale DOM event listeners from previous renders by replacing the
    // element with a fresh clone. Each render of the same slot (e.g. wf-main)
    // would otherwise accumulate mousedown/dblclick handlers, causing the
    // dblclick callback to fire multiple times when slides are swapped.
    (function() {
      var old = document.getElementById(id);
      if (old && old.parentNode) {
        var fresh = old.cloneNode(false);   // same id/class/style, no children
        old.parentNode.replaceChild(fresh, old);
      }
    })();

    var n      = data.bars.length;
    var yMin   = Math.floor(data.y_range[0]);
    var yMax   = Math.ceil(data.y_range[1]);
    var vis    = [0, n];          // [startIdx, endIdx) — currently visible slice
    var curBars = data.bars.slice(); // mirrors the visible slice for tooltip

    function buildSeries(start, end) {
      var bars = data.bars.slice(start, end);
      curBars  = bars;
      var m    = bars.length;
      // visLow = max(b.low, yMin): for absolute/total bars that start from 0,
      // we only show the portion above yMin so the bar stays within the axis range.
      var spacerD = bars.map(function(b) { return Math.max(b.low, yMin) - yMin; });
      var absD = new Array(m).fill(null), posD = new Array(m).fill(null);
      var negD = new Array(m).fill(null), totD = new Array(m).fill(null);
      bars.forEach(function(b, i) {
        var h = b.high - Math.max(b.low, yMin);
        if      (b.measure === "absolute")                     absD[i] = h;
        else if (b.measure === "relative" && b.value >= 0)    posD[i] = h;
        else if (b.measure === "relative" && b.value <  0)    negD[i] = h;
        else                                                   totD[i] = h;
      });
      return {
        series: [
          { name: "_spacer",  data: spacerD },
          { name: "Absolute", data: absD },
          { name: "Increase", data: posD },
          { name: "Decrease", data: negD },
          { name: "Total",    data: totD },
        ],
        categories: bars.map(function(b) { return b.x; }),
      };
    }

    function applyVis(ch) {
      var s = buildSeries(vis[0], vis[1]);
      ch.updateOptions({ xaxis: { categories: s.categories } }, false, false);
      ch.updateSeries(s.series, false);
    }

    var ICO_RESET = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>';

    // chart declared before options so the mounted event can reference it
    var chart;
    var initial = buildSeries(0, n);
    // Tema-duyarlı renkler: render anındaki moda göre (canlı toggle'da sweepApex
    // foreColor/başlığı günceller; data label'lar seri-bazlı sabit kontrast alır).
    var _wfLight = document.body.classList.contains("light-mode");
    var _wfInk   = _wfLight ? "#2C2A26" : "#E4E8F0";
    // Data label kontrastı SERİ bazında: Absolute=denim bar (açık metin),
    // Increase/Decrease=adaçayı/terracotta ince barlar (etiket çoğu zaman bar
    // DIŞINA taşar → zemin rengine göre tema metni), Total=amber bar (WCAG için
    // HER ZAMAN koyu metin — beyaz-üstü-amber ~1.9:1 ile başarısızdı).
    var _wfLabelColors = ["rgba(0,0,0,0)", "#F0E6D5", _wfInk, _wfInk, "#1A1408"];
    chart = new ApexCharts(document.getElementById(id), {
      series: initial.series,
      chart: {
        type: "bar", stacked: true, height: chartHeight,
        animations: { enabled: true, speed: 300, animateGradually: { enabled: false } },
        toolbar: {
          show: true,
          tools: {
            download: true,
            zoom: false, zoomin: false, zoomout: false,
            pan: false, selection: false, reset: false,
            customIcons: [{
              icon: ICO_RESET, index: 0, title: "Reset Zoom", class: "apx-cicon",
              click: function(ch) { vis[0] = 0; vis[1] = n; applyVis(ch); }
            }],
          },
        },
        fontFamily: "system-ui, -apple-system, sans-serif",
        foreColor: _wfLight ? "#4A4844" : "#7A8399",
        background: "transparent",
      },
      theme: { mode: _wfLight ? "light" : "dark" },
      colors: ["rgba(0,0,0,0)", "#4A6B8A", "#7A9B7E", "#B8826B", "#D4A574"],
      fill:   { opacity: [0, 1, 1, 1, 1] },
      stroke: { show: false },
      plotOptions: { bar: { horizontal: false, columnWidth: "60%" } },
      states: {
        hover:  { filter: { type: "lighten", value: 0.08 } },
        active: { filter: { type: "darken",  value: 0.12 } },
      },
      dataLabels: {
        enabled: true,
        formatter: function(value, opts) {
          // Series 0 is the transparent spacer — no label
          if (opts.seriesIndex === 0 || value === null || value === undefined) return "";
          var bar = curBars[opts.dataPointIndex];
          if (!bar) return "";
          // Balance Bridge (unit === "M"): show ₺M with TR thousands separator
          if (data.unit === "M") {
            var v = Math.round(bar.value);
            if (bar.measure === "relative") return (v >= 0 ? "+" : "") + v.toLocaleString("tr-TR");
            else return Math.round(bar.value).toLocaleString("tr-TR");
          }
          // Default NIM charts: bps for relative, % for absolute/total
          if (bar.measure === "relative") {
            var v = Math.round(bar.value);
            return (v >= 0 ? "+" : "") + v + " bps";
          } else {
            return (bar.value / 100).toFixed(2) + "%";
          }
        },
        style: { fontSize: "12px", fontWeight: "600", colors: _wfLabelColors },
        offsetY: -4,
        background: { enabled: false },
      },
      title: { text: data.title, style: { fontSize: "14px", fontWeight: "600", color: _wfInk } },
      xaxis: { categories: initial.categories },
      yaxis: {
        title: { text: data.yaxis_title },
        min: 0, max: yMax - yMin,
        labels: { formatter: function(v) { return Math.round(v + yMin).toString(); } },
      },
      tooltip: {
        custom: function(opts) {
          var bar = curBars[opts.dataPointIndex];
          if (!bar) return "";
          var v = bar.value, sign = v >= 0 ? "+" : "";
          var tdL = 'style="padding:2px 8px 2px 0;color:var(--text-secondary);white-space:nowrap"';
          var tdR = 'style="padding:2px 0 2px 8px;font-weight:600;text-align:right;white-space:nowrap"';
          var rows = "";
          if (bar.measure === "relative") {
            // Direction: always show from-level → to-level (positive: low→high, negative: high→low)
            var fromBps = v >= 0 ? bar.low : bar.high;
            var toBps   = v >= 0 ? bar.high : bar.low;
            rows += "<tr><td " + tdL + ">NIM Contribution</td><td " + tdR + ">" +
                    sign + Math.round(v) + " bps (" + Math.round(fromBps) + " \u2192 " + Math.round(toBps) + ")</td></tr>";
          } else {
            rows += "<tr><td " + tdL + ">Level</td><td " + tdR + ">" + (bar.high / 100).toFixed(2) + "%</td></tr>";
          }
          if (bar.rate_t0 != null && bar.rate_t1 != null) {
            var dr = (bar.rate_t1 - bar.rate_t0) * 10000;
            var rSign = dr >= 0 ? "+" : "";
            rows += "<tr><td " + tdL + ">Rate Change</td><td " + tdR + ">" +
                    rSign + Math.round(dr) + " bps (" + (bar.rate_t0 * 100).toFixed(2) + "% \u2192 " + (bar.rate_t1 * 100).toFixed(2) + "%)</td></tr>";
          }
          return '<div style="padding:10px 12px;font-family:system-ui,sans-serif;font-size:12px;background:var(--bg-panel);color:var(--text-primary);border-radius:4px"><b>' +
                 bar.x + '</b><table style="margin-top:6px;border-collapse:collapse">' + rows + '</table></div>';
        },
      },
      legend: {
        show: true, position: "top", fontSize: "12px",
        customLegendItems: ["Absolute", "Increase", "Decrease", "Total"],
        markers: { fillColors: ["#4A6B8A", "#7A9B7E", "#B8826B", "#D4A574"] },
      },
      grid: { borderColor: _wfLight ? "#E5DFD1" : "rgba(255,255,255,0.07)" },
    });
    chart.render();
    chartInstances[id] = chart;

    // ── Drag-to-select-zoom overlay ───────────────────────────────────────────
    // ApexCharts does not support selection zoom on category-axis bar charts,
    // so we implement it by attaching mouse events directly to the chart div.
    var el = document.getElementById(id);
    el.style.position   = "relative";
    el.style.userSelect = "none";
    el.style.cursor     = "crosshair";

    // Persistent selection rectangle (reused every drag)
    var selBox = document.createElement("div");
    selBox.style.cssText = [
      "position:absolute", "pointer-events:none", "z-index:10",
      "background:rgba(212,165,116,0.10)", "border:1.5px solid var(--accent)",
      "display:none",
    ].join(";");
    el.appendChild(selBox);

    var dragging = false, dragX0 = 0;

    function chartArea() {
      // chart.w.globals has the real pixel geometry after render
      var g = chart.w && chart.w.globals;
      return {
        left:   (g && g.translateX)  || 55,
        width:  (g && g.gridWidth)   || (el.offsetWidth - 80),
        top:    (g && g.translateY)  || 20,
        height: (g && g.gridHeight)  || 250,
      };
    }

    el.addEventListener("mousedown", function(e) {
      if (e.button !== 0) return;
      var rect = el.getBoundingClientRect();
      var area = chartArea();
      var x    = e.clientX - rect.left;
      var y    = e.clientY - rect.top;
      // Restrict drag to the actual grid area — ignore toolbar / axis clicks
      if (x < area.left || x > area.left + area.width)   return;
      if (y < area.top  || y > area.top  + area.height)  return;
      // ApexCharts re-renders (updateSeries/updateOptions) clear el's children,
      // so selBox may have been detached. Re-append it before use.
      if (!selBox.parentNode) el.appendChild(selBox);
      dragging = true;
      dragX0   = x;
      selBox.style.left    = x + "px";
      selBox.style.top     = area.top + "px";
      selBox.style.height  = area.height + "px";
      selBox.style.width   = "0";
      selBox.style.display = "block";
      e.preventDefault();
    });

    el.addEventListener("mousemove", function(e) {
      if (!dragging) return;
      var rect = el.getBoundingClientRect();
      var x    = e.clientX - rect.left;
      selBox.style.left  = Math.min(dragX0, x) + "px";
      selBox.style.width = Math.abs(x - dragX0) + "px";
    });

    function onRelease(e) {
      if (!dragging) return;
      dragging             = false;
      selBox.style.display = "none";
      var rect = el.getBoundingClientRect();
      var x1   = Math.min(dragX0, e.clientX - rect.left);
      var x2   = Math.max(dragX0, e.clientX - rect.left);
      if (x2 - x1 < 5) return;          // ignore accidental tiny clicks
      var area = chartArea();
      var len  = vis[1] - vis[0];
      var barW = area.width / len;
      var i0   = vis[0] + Math.max(0,   Math.floor((x1 - area.left) / barW));
      var i1   = vis[0] + Math.min(len, Math.ceil ((x2 - area.left) / barW));
      if (i1 > i0) { vis[0] = i0; vis[1] = i1; applyVis(chart); }
    }

    el.addEventListener("mouseup",    onRelease);
    el.addEventListener("mouseleave", function() {
      if (dragging) { dragging = false; selBox.style.display = "none"; }
    });
    el.addEventListener("dblclick", function(e) {
      var rect = el.getBoundingClientRect();
      var area = chartArea();
      var x    = e.clientX - rect.left;
      if (x >= area.left && x <= area.left + area.width) {
        var len  = vis[1] - vis[0];
        var barW = area.width / Math.max(len, 1);
        var bi   = vis[0] + Math.floor((x - area.left) / barW);
        if (bi >= 0 && bi < curBars.length) {
          var bar = curBars[bi];
          if (opts && opts.onBarDblClick && bar && bar.x && bar.x.indexOf(" | ") !== -1) {
            opts.onBarDblClick(bar);
            return;   // do NOT reset zoom
          }
          // Deposit drill-down: double-click on any relative bar (product-level)
          if (opts && opts.onRelativeBarDblClick && bar && bar.measure === "relative"
              && bar.x !== "Other Items") {
            opts.onRelativeBarDblClick(bar);
            return;   // do NOT reset zoom
          }
        }
      }
      vis[0] = 0; vis[1] = n; applyVis(chart);   // default: reset zoom
    });
  }

  // 400ms double-click detector for ApexCharts bar / bar-growth charts.
  // Apex emits dataPointSelection on each click — we count two within 400ms on
  // the same index as a double-click and invoke opts.onBarDblClick(category).
  function _makeBarDblClickHandler(categories, onBarDblClick) {
    var lastIdx = -1, lastTs = 0;
    return function(_e, _ctx, cfg) {
      if (!onBarDblClick) return;
      var idx = cfg.dataPointIndex;
      var now = Date.now();
      if (idx === lastIdx && now - lastTs < 400) {
        var cat = categories[idx];
        if (cat && cat !== "Other Items" && cat !== "Start Rate" && cat !== "After Mix" && cat !== "End Rate") {
          onBarDblClick(cat);
        }
        lastIdx = -1; lastTs = 0;
      } else {
        lastIdx = idx; lastTs = now;
      }
    };
  }

  function renderBarChart(id, data, opts) {
    opts = opts || {};
    var chartHeight = opts.height || 320;
    destroyChart(id);
    var tooltips = data.tooltips || [];
    var colors = data.values.map(function(v) { return v >= 0 ? "#7A9B7E" : "#B8826B"; });
    var chart = new ApexCharts(document.getElementById(id), {
      series: [{ name: data.yaxis_title, data: data.values }],
      chart: { type: "bar", height: chartHeight, toolbar: { show: false },
               fontFamily: "system-ui, -apple-system, sans-serif",
               foreColor: "#7A8399",
               background: "transparent",
               animations: { enabled: true, speed: 300, animateGradually: { enabled: false } },
               events: { dataPointSelection: _makeBarDblClickHandler(data.categories || [], opts.onBarDblClick) } },
      plotOptions: { bar: { horizontal: false, columnWidth: "60%", distributed: true,
                            dataLabels: { position: "top" } } },
      states: {
        hover:    { filter: { type: "lighten", value: 0.08 } },
        active:   { filter: { type: "darken",  value: 0.12 } },
        inactive: { opacity: 0.65 },
      },
      colors: colors,
      dataLabels: {
        enabled: true,
        formatter: function(v) {
          if (v === 0) return "";   // spacer bars (Start NIM / After Mix alignment)
          return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
        },
        offsetY: -20,
        style: { fontSize: "13px", fontWeight: "500", colors: ["#E4E8F0"] },
      },
      title: { text: data.title, style: { fontSize: "14px", fontWeight: "600", color: _plotInk() } },
      xaxis: {
        categories: data.categories,
        labels: { rotate: -30, trim: false, style: { fontSize: "12px" } },
      },
      yaxis: {
        title: { text: data.yaxis_title },
        min: data.y_range[0],
        max: data.y_range[1],
        labels: { formatter: function(v) { return v.toFixed(2) + "%"; } },
      },
      legend: { show: false },
      grid: { borderColor: "rgba(255,255,255,0.07)" },
      tooltip: {
        theme: "dark",
        custom: function(opts) {
          var i = opts.dataPointIndex;
          var v = data.values[i];
          var cat = data.categories[i] || "";
          var sign = v >= 0 ? "+" : "";
          var tt = tooltips[i] || {};
          var rows = '<tr><td style="color:var(--text-secondary);padding-right:8px">Δ Weight</td>' +
                     '<td style="font-weight:600">' + sign + v.toFixed(3) + '%</td></tr>';
          if (tt.rate_avg_bps != null) {
            rows += '<tr><td style="color:var(--text-secondary);padding-right:8px">Rate (avg of t0,t1)</td>' +
                    '<td style="font-weight:600">' + (tt.rate_avg_bps / 100).toFixed(1) + '%</td></tr>';
          }
          if (tt.wavg_avg_bps != null) {
            rows += '<tr><td style="color:var(--text-secondary);padding-right:8px">Avg Deposit Rate (avg)</td>' +
                    '<td style="font-weight:600">' + (tt.wavg_avg_bps / 100).toFixed(1) + '%</td></tr>';
          }
          if (tt.rate_1_bps != null) {
            rows += '<tr><td style="color:var(--text-secondary);padding-right:8px">Rate (date 2)</td>' +
                    '<td style="font-weight:600">' + (tt.rate_1_bps / 100).toFixed(1) + '%</td></tr>';
          }
          if (tt.bench_rate_1_bps != null) {
            var sideLabel = tt.bs_type === "Assets" ? "Assets" : "Liabilities";
            rows += '<tr><td style="color:var(--text-secondary);padding-right:8px">' + sideLabel + ' w.avg (date 2)</td>' +
                    '<td style="font-weight:600">' + (tt.bench_rate_1_bps / 100).toFixed(1) + '%</td></tr>';
          }
          return '<div style="padding:10px;font-family:system-ui,sans-serif;font-size:12px;background:var(--bg-panel);color:var(--text-primary);border-radius:4px">' +
                 '<b style="display:block;margin-bottom:6px">' + cat + '</b>' +
                 '<table style="border-collapse:collapse">' + rows + '</table></div>';
        },
      },
    });
    chart.render();
    chartInstances[id] = chart;
  }

  function renderBarGrowthChart(id, data, opts) {
    opts = opts || {};
    var chartHeight = opts.height || 300;
    destroyChart(id);
    var tooltips = data.tooltips || [];
    var colors = data.values.map(function(v) {
      if (v === null || v === undefined) return "transparent";
      return v >= 0 ? "#7A9B7E" : "#B8826B";
    });
    var chart = new ApexCharts(document.getElementById(id), {
      series: [{ name: data.yaxis_title, data: data.values.map(function(v) { return v === null ? 0 : v; }) }],
      chart: { type: "bar", height: chartHeight, toolbar: { show: false },
               fontFamily: "system-ui, -apple-system, sans-serif",
               foreColor: "#7A8399",
               background: "transparent",
               animations: { enabled: true, speed: 300, animateGradually: { enabled: false } },
               events: { dataPointSelection: _makeBarDblClickHandler(data.categories || [], opts.onBarDblClick) } },
      plotOptions: { bar: { horizontal: false, columnWidth: "60%", distributed: true,
                            dataLabels: { position: "top" } } },
      states: {
        hover:    { filter: { type: "lighten", value: 0.08 } },
        active:   { filter: { type: "darken",  value: 0.12 } },
        inactive: { opacity: 0.65 },
      },
      colors: colors,
      dataLabels: {
        enabled: true,
        formatter: function(v, ctx) {
          var raw = data.values[ctx.dataPointIndex];
          if (raw === null || raw === undefined || raw === 0) return "";
          return (raw >= 0 ? "+" : "") + raw.toFixed(0) + "M";
        },
        offsetY: -20,
        style: { fontSize: "13px", fontWeight: "500", colors: ["#E4E8F0"] },
      },
      title: { text: data.title, style: { fontSize: "14px", fontWeight: "600", color: _plotInk() } },
      xaxis: {
        categories: data.categories,
        labels: { rotate: -30, trim: false, style: { fontSize: "12px" } },
      },
      yaxis: {
        title: { text: data.yaxis_title },
        min: data.y_range[0],
        max: data.y_range[1],
        labels: { formatter: function(v) { return v.toFixed(0) + "M"; } },
      },
      legend: { show: false },
      grid: { borderColor: "rgba(255,255,255,0.07)" },
      tooltip: {
        theme: "dark",
        custom: function(o) {
          var i   = o.dataPointIndex;
          var raw = data.values[i];
          if (raw === null || raw === undefined) return "";
          var cat = data.categories[i] || "";
          var tt  = tooltips[i] || {};
          var sign = raw >= 0 ? "+" : "";
          var rows = '<tr><td style="color:var(--text-secondary);padding-right:8px">Δ Balance</td>' +
                     '<td style="font-weight:600">' + sign + raw.toFixed(2) + ' ₺M</td></tr>';
          if (tt.pct != null) {
            rows += '<tr><td style="color:var(--text-secondary);padding-right:8px">Δ %</td>' +
                    '<td style="font-weight:600">' + (tt.pct >= 0 ? "+" : "") + tt.pct.toFixed(2) + '%</td></tr>';
          }
          if (tt.b0_m != null) {
            rows += '<tr><td style="color:var(--text-secondary);padding-right:8px">Balance (t₀)</td>' +
                    '<td style="font-weight:600">' + tt.b0_m.toLocaleString() + ' ₺M</td></tr>' +
                    '<tr><td style="color:var(--text-secondary);padding-right:8px">Balance (t₁)</td>' +
                    '<td style="font-weight:600">' + tt.b1_m.toLocaleString() + ' ₺M</td></tr>';
          }
          return '<div style="padding:10px;font-family:system-ui,sans-serif;font-size:12px;background:var(--bg-panel);color:var(--text-primary);border-radius:4px">' +
                 '<b style="display:block;margin-bottom:6px">' + cat + '</b>' +
                 '<table style="border-collapse:collapse">' + rows + '</table></div>';
        },
      },
    });
    chart.render();
    chartInstances[id] = chart;
  }

  function renderLineChart(id, data, opts) {
    opts = opts || {};
    var chartHeight = opts.height || 320;
    destroyChart(id);
    var _lcLight = document.body.classList.contains("light-mode");
    var chart = new ApexCharts(document.getElementById(id), {
      series: data.series.map(function(s) {
        var pts = s.data.map(function(p) { return { x: p.x, y: p.y }; });
        // Append a null sentinel 30 days after the last real point so that
        // ApexCharts' internal x-range extends beyond the final data point.
        // Without this, the last point sits on the axis boundary and the
        // hover-detection zone never activates (known ApexCharts v3 bug).
        if (pts.length > 0) {
          var lastMs = new Date(pts[pts.length - 1].x).getTime();
          pts.push({ x: new Date(lastMs + 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10), y: null });
        }
        return { name: s.name, data: pts };
      }),
      chart: { type: "line", height: chartHeight, toolbar: { show: false },
               fontFamily: "system-ui, -apple-system, sans-serif",
               foreColor: _lcLight ? "#4A4844" : "#7A8399",
               background: "transparent",
               animations: { enabled: true, speed: 300, animateGradually: { enabled: false } },
               events: {
                 mounted: function(chartCtx) {
                   data.series.forEach(function(s, i) {
                     if (!s.hide_legend) return;
                     var el = document.querySelector(
                       "#" + id + " .apexcharts-legend-series[rel='" + (i + 1) + "']"
                     );
                     if (el) el.style.display = "none";
                   });
                 },
                 legendClick: function(chartCtx, seriesIndex, config) {
                   // When a sim series is toggled, also sync its realized (_r_) counterpart.
                   // We use setTimeout so the default toggle finishes first.
                   var simName = config.globals.seriesNames[seriesIndex];
                   if (!simName || simName.startsWith("_r_")) return;
                   var pairedName = "_r_" + simName;
                   setTimeout(function() {
                     var collapsed = (chartCtx.w && chartCtx.w.globals &&
                                      chartCtx.w.globals.collapsedSeriesIndices) || [];
                     var simHidden    = collapsed.indexOf(seriesIndex) >= 0;
                     var pairedIdx    = config.globals.seriesNames.indexOf(pairedName);
                     if (pairedIdx < 0) return;
                     var pairedHidden = collapsed.indexOf(pairedIdx) >= 0;
                     if (simHidden !== pairedHidden) chartCtx.toggleSeries(pairedName);
                   }, 0);
                 }
               } },
      colors: data.series.map(function(s) {
        var n = s.name || "";
        return s.color || LINE_COLORS[n] || LINE_COLORS[n.replace(/^_r_/, "")] || "#D4A574";
      }),
      stroke: {
        curve: "straight",
        width: data.series.map(function(s) { return s.dash ? 2 : 2.5; }),
        dashArray: data.series.map(function(s) { return s.dash ? 6 : 0; }),
      },
      states: {
        hover:    { filter: { type: "none" } },
        inactive: { opacity: 0.50 },
      },
      markers: { size: data.series.map(function(s) { return s.hide_legend ? 0 : 4; }),
                 strokeColors: "var(--bg-panel)", strokeWidth: 2,
                 hover: { sizeOffset: 3 } },
      dataLabels: {
        enabled: !!data.show_data_labels,
        formatter: data.as_percent
          ? function(v) { return v != null ? v.toFixed(2) + "%" : ""; }
          : function(v) { return v != null ? v.toFixed(1) : ""; },
        offsetY: -8,
        // Light modda krem zeminde #E4E8F0 kaybolur → koyu grafit (_plotInk).
        style: { fontSize: "13px", fontWeight: "700", colors: [_plotInk()] },
        background: { enabled: false },
      },
      title: { text: data.title, style: { fontSize: "13px", fontWeight: "600", color: _plotInk() } },
      xaxis: { type: "datetime", labels: { datetimeUTC: false, format: "MMM yy" } },
      yaxis: {
        title: { text: data.yaxis_title },
        min: data.y_min != null ? data.y_min : undefined,
        max: data.y_max != null ? data.y_max : undefined,
        labels: {
          formatter: data.as_percent
            ? function(v) { return v.toFixed(2) + "%"; }
            : function(v) { return v.toFixed(1) + " bps"; },
        },
      },
      tooltip: {
        shared: false,
        intersect: false,
        x: { format: "yyyy-MM-dd" },
        y: {
          title: { formatter: function(n) { return (n || "").replace(/^_r_/, ""); } },
          formatter: data.as_percent
            ? function(v) { return v != null ? v.toFixed(2) + "%" : ""; }
            : function(v) { return v != null ? v.toFixed(1) + " bps" : ""; },
        },
      },
      legend: { show: true, position: "top", fontSize: "12px" },
      grid: { borderColor: _lcLight ? "#E5DFD1" : "rgba(255,255,255,0.07)" },
      theme: { mode: _lcLight ? "light" : "dark" },
    });
    var rp = chart.render();
    if (rp && rp.catch) rp.catch(function(e) {
      showError("Chart render error (" + id + "): " + (e && e.message ? e.message : String(e)));
    });
    // Backend'in initial_hidden işaretlediği seriler pasif başlar (legend'dan
    // tıklanarak geri açılır) — ör. en güncel iki senaryo dışındakiler.
    var _hideAtStart = data.series.filter(function(s) { return s.initial_hidden; })
                                  .map(function(s) { return s.name; });
    if (_hideAtStart.length) {
      var _doHide = function() {
        _hideAtStart.forEach(function(n) { try { chart.hideSeries(n); } catch (e) {} });
      };
      if (rp && rp.then) rp.then(_doHide); else setTimeout(_doHide, 0);
    }
    // Sweep round-trip güvencesi: grafik LIGHT modda render edilmiş olsa bile
    // sweepApex'in "pristine dark" diye light değerleri yakalamaması için bu
    // fonksiyonun dark sabitleri burada explicit kaydedilir.
    chart.__prismaOrig = {
      chart: { foreColor: "#7A8399" },
      grid:  { borderColor: "rgba(255,255,255,0.07)" },
      title: { style: { color: "#E4E8F0" } },
      dataLabels: { style: { colors: ["#E4E8F0"] } }
    };
    chartInstances[id] = chart;
  }

  // ── BSE Plotly Renderers (all BSE charts bypass ApexCharts) ─────────────────

  var _plotlyDefaults = {
    paper_bgcolor: "transparent",
    plot_bgcolor:  "transparent",
    font: { family: "system-ui, -apple-system, sans-serif", size: 13, color: "#E4E8F0" },
    legend: { orientation: "h", y: -0.28, x: 0, font: { size: 11, color: "#A9B3C4" },
              bgcolor: "rgba(0,0,0,0)" },
    hoverlabel: { bgcolor: "#131826", bordercolor: "rgba(255,255,255,0.15)",
                  font: { family: "system-ui, -apple-system, sans-serif", size: 12, color: "#E4E8F0" } },
    margin: { l: 58, r: 16, t: 40, b: 60 },
  };
  var _plotlyConfig = { responsive: true, displayModeBar: false };
  function _axisOpts(extra) {
    // automargin: eksen başlığı/tick etiketleri sabit margin'e sığmadığında
    // Plotly margin'i kendisi genişletir (başlık-rakam üst üste binmesi biter).
    return Object.assign({ gridcolor: "rgba(255,255,255,0.06)", zerolinecolor: "rgba(255,255,255,0.12)", zerolinewidth: 1, linecolor: "rgba(255,255,255,0.06)", nticks: 6, tickfont: { size: 12 }, automargin: true }, extra);
  }
  // BSE grafik başlığı — bold + mod-duyarlı mürekkep (dark'ta silik kalmasın).
  function _bseTitle(text, size) {
    return { text: text ? "<b>" + text + "</b>" : "",
             font: { size: size || 15, color: _plotInk() } };
  }

  // Waterfall — uses Plotly native waterfall (no ApexCharts)
  function renderBseWaterfall(id, fig) {
    var el = document.getElementById(id);
    if (!el || !fig || !fig.bars) return;
    var colors = fig.bars.map(function(b) {
      if (b.measure === "total")    return "#D4A574";  // amber
      if (b.measure === "absolute") return "#4A6B8A";  // denim
      return b.value >= 0 ? "#7A9B7E" : "#B8826B";    // adaçayı / terracotta
    });
    var trace = {
      type:      "waterfall",
      x:         fig.bars.map(function(b) { return b.label; }),
      y:         fig.bars.map(function(b) { return b.value; }),
      measure:   fig.bars.map(function(b) { return b.measure; }),
      connector: { line: { color: "rgba(255,255,255,0.08)", width: 1 } },
      increasing:  { marker: { color: "#7A9B7E" } },
      decreasing:  { marker: { color: "#B8826B" } },
      totals:      { marker: { color: "#D4A574" } },
      textposition: "outside",
      text: fig.bars.map(function(b) {
        var v = b.value;
        if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + "M";
        if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1) + "K";
        return Math.round(v).toLocaleString();
      }),
    };
    var layout = Object.assign({}, _plotlyDefaults, {
      title:  _bseTitle(fig.title),
      height: 340,
      yaxis:  _axisOpts({ title: fig.yaxis_title || "Balance (mn)", tickformat: ",.0f", range: fig.y_range || undefined }),
      // tickmode linear + dtick 1: kategori ekseninde HER barın altında etiket
      // (Plotly kalabalıkta tick atlar — waterfall'da atlanamaz).
      xaxis:  _axisOpts({ tickangle: -35, tickmode: "linear", dtick: 1, tickfont: { size: 11 } }),
      margin: { l: 58, r: 16, t: 48, b: 90 },
    });
    Plotly.react(el, [trace], layout, _plotlyConfig);
  }

  // Line — handles {series: [{name, dates, values}]} format
  function renderBseLine(id, fig) {
    var el = document.getElementById(id);
    if (!el || !fig || !fig.series) return;
    var COLORS = _PRISMA_CAT;
    var traces = fig.series.map(function(s, i) {
      return {
        x: s.dates || fig.dates || [],
        y: s.values || [],
        name: s.name,
        type: "scatter",
        mode: "lines+markers",
        marker: { size: 5 },
        line: { color: COLORS[i % COLORS.length], width: 2 },
      };
    });
    var layout = Object.assign({}, _plotlyDefaults, {
      title:  _bseTitle(fig.title),
      height: 300,
      yaxis:  _axisOpts({ title: fig.yaxis_title || "", tickformat: ",.2f" }),
      xaxis:  _axisOpts({ type: "category" }),
    });
    Plotly.react(el, traces, layout, _plotlyConfig);
  }

  // Bubble Chart — {bubbles:[{name,x,y,size}], time_series:{name:{dates,values}}}
  // X = period growth %, Y = nominal change, click → drill-down line chart below
  function renderBseBubble(id, fig) {
    var el = document.getElementById(id);
    if (!el || !fig || !fig.bubbles || !fig.bubbles.length) {
      if (el) { el.style.display = "flex"; el.style.alignItems = "center";
                el.style.justifyContent = "center"; el.textContent = "Veri yok"; }
      return;
    }
    // Remove stale drill-down from a previous render
    var prevDrill = document.getElementById(id + "-drillrow");
    if (prevDrill) prevDrill.remove();

    var COLORS = _PRISMA_CAT;
    var bubbles = fig.bubbles;
    var sizes   = bubbles.map(function(b) { return b.size; });
    var maxSize = Math.max.apply(null, sizes.filter(function(s){ return s > 0; }));
    var sizeref = maxSize > 0 ? (2 * maxSize) / (70 * 70) : 1;

    function shortLabel(n) { return n.length > 22 ? n.substring(0, 21) + "…" : n; }

    var trace = {
      type: "scatter",
      mode: "markers+text",
      x:    bubbles.map(function(b) { return b.x; }),
      y:    bubbles.map(function(b) { return b.y; }),
      text: bubbles.map(function(b) { return shortLabel(b.name); }),
      textposition: "top center",
      textfont: { size: 12, color: _plotInk() },
      customdata: bubbles.map(function(b) {
        var bal = b.size >= 1e6 ? (b.size/1e6).toFixed(1)+"M"
                : b.size >= 1e3 ? (b.size/1e3).toFixed(1)+"K"
                : Math.round(b.size).toLocaleString();
        return [b.name, bal];
      }),
      hovertemplate: "<b>%{customdata[0]}</b><br>" +
                     "Growth %: %{x:.2f}%<br>" +
                     "Nominal Δ: %{y:,.0f}<br>" +
                     "Balance (End): %{customdata[1]}" +
                     "<br><i>Click for details</i><extra></extra>",
      marker: {
        size:     sizes,
        sizemode: "area",
        sizeref:  sizeref,
        sizemin:  6,
        color:    bubbles.map(function(_, i) { return COLORS[i % COLORS.length]; }),
        opacity:  0.72,
        line:     { color: "rgba(255,255,255,0.25)", width: 1 },
      },
    };

    var layout = Object.assign({}, _plotlyDefaults, {
      title:  _bseTitle(fig.title),
      height: 400,
      showlegend: false,
      xaxis: _axisOpts({ title: { text: fig.xaxis_title || "Growth (%)" }, ticksuffix: "%", tickformat: ".1f", zeroline: true }),
      yaxis: _axisOpts({ title: { text: fig.yaxis_title || "Nominal Change (mn)" }, tickformat: ",.0f", zeroline: true }),
      margin: { l: 70, r: 24, t: 48, b: 64 },
    });
    Plotly.react(el, [trace], layout, _plotlyConfig);

    // Drill-down: clicking a bubble reveals its balance time series below
    if (fig.time_series) {
      el.removeAllListeners && el.removeAllListeners("plotly_click");
      el.on("plotly_click", function(ev) {
        if (!ev || !ev.points || !ev.points.length) return;
        var pt          = ev.points[0];
        var productName = pt.customdata && pt.customdata[0];
        var tsData      = fig.time_series[productName];
        if (!tsData) return;
        _showBubbleDrillDown(id, productName, tsData,
                             COLORS[pt.pointIndex % COLORS.length]);
      });
    }
  }

  // Insert (or replace) a line chart below the bubble's parent row
  function _showBubbleDrillDown(bubbleId, productName, tsData, color) {
    var drillRowId  = bubbleId + "-drillrow";
    var drillChartId = bubbleId + "-drill";
    var bubbleEl    = document.getElementById(bubbleId);
    var parentRow   = bubbleEl.closest(".bse-chart-row");

    // Remove existing drill-down for this bubble (re-click = toggle / switch product)
    var existing = document.getElementById(drillRowId);
    if (existing) existing.remove();

    var drillRow = document.createElement("div");
    drillRow.id        = drillRowId;
    drillRow.className = "bse-chart-row single";
    drillRow.innerHTML =
      '<div class="card" style="position:relative;">' +
        '<button onclick="document.getElementById(\'' + drillRowId + '\').remove()"' +
          ' style="position:absolute;top:8px;right:10px;background:none;border:none;' +
          'cursor:pointer;font-size:15px;color:var(--text-secondary);z-index:1;" title="Kapat">✕</button>' +
        '<div class="plot-container" id="' + drillChartId + '"></div>' +
      '</div>';
    parentRow.insertAdjacentElement("afterend", drillRow);

    var drillEl = document.getElementById(drillChartId);
    Plotly.react(drillEl, [{
      x:    tsData.dates,
      y:    tsData.values,
      name: productName,
      type: "scatter",
      mode: "lines+markers",
      line: { color: color || "#D4A574", width: 2 },
      marker: { size: 5 },
      hovertemplate: "%{x}: %{y:,.0f}<extra></extra>",
    }], Object.assign({}, _plotlyDefaults, {
      title:      _bseTitle(productName + " — Balance Evolution", 12.5),
      height:     240,
      showlegend: false,
      yaxis:      _axisOpts({ title: "Balance (mn)", tickformat: ",.0f" }),
      xaxis:      _axisOpts({ type: "category" }),
      margin:     { l: 70, r: 24, t: 38, b: 50 },
    }), _plotlyConfig);

    drillRow.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // Stacked Area — handles {series: [{name, dates, values}]} format
  function renderStackedArea(id, data, opts) {
    var el = document.getElementById(id);
    if (!el || !data || !data.series) return;
    var traces = data.series.map(function(s, i) {
      return {
        x: s.dates || data.dates || [],
        y: s.values || [],
        name: s.name,
        type: "scatter",
        mode: "none",
        fill: "tonexty",
        stackgroup: "one",
        line: { color: _PRISMA_CAT[i % _PRISMA_CAT.length] },
        fillcolor: _PRISMA_CAT[i % _PRISMA_CAT.length]
                     .replace(/^#/, "rgba(")
                     .replace(/([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i, function(_, r, g, b) {
                       // 0.55 alfa: 0.72 dark zeminde fazla parlak/ışıltılı duruyordu
                       return parseInt(r,16)+","+parseInt(g,16)+","+parseInt(b,16)+",0.55)";
                     }),
      };
    });
    var layout = Object.assign({}, _plotlyDefaults, {
      title:  _bseTitle(data.title),
      height: (opts && opts.height) || 320,
      yaxis:  _axisOpts({ title: data.yaxis_title || "", tickformat: ",.0f" }),
      xaxis:  _axisOpts({ type: "category" }),
    });
    Plotly.react(el, traces, layout, _plotlyConfig);
  }

  // Dual-Axis Line — handles {dates, series_left, series_right}
  function renderDualAxisLine(id, data, opts) {
    var el = document.getElementById(id);
    if (!el || !data) return;
    var dates  = data.dates || [];
    var COLORS = _PRISMA_CAT;
    var traces = [];
    (data.series_left || []).forEach(function(s, i) {
      traces.push({ x: dates, y: s.values || [], name: s.name,
                    type: "scatter", mode: "lines+markers",
                    line: { color: COLORS[i % COLORS.length], width: 2 }, yaxis: "y" });
    });
    (data.series_right || []).forEach(function(s, i) {
      traces.push({ x: dates, y: s.values || [], name: s.name,
                    type: "scatter", mode: "lines+markers",
                    line: { color: COLORS[(i + 2) % COLORS.length], width: 2, dash: "dot" }, yaxis: "y2" });
    });
    var layout = Object.assign({}, _plotlyDefaults, {
      title:  _bseTitle(data.title),
      height: (opts && opts.height) || 320,
      // ",.2~f": küsurat varsa gösterir (Synthetic Share %15.2 gibi), tam
      // sayıda/binlik bakiyede sıfırları kırpar — ",.0f"un "15 15 15" tekrar
      // eden tick problemi biter.
      yaxis:  _axisOpts({ title: data.yaxis_title || "", tickformat: ",.2~f" }),
      yaxis2: _axisOpts({ overlaying: "y", side: "right", title: data.yaxis_title_right || "", tickformat: ",.2~f" }),
      xaxis:  _axisOpts({ type: "category" }),
      margin: { l: 58, r: 58, t: 44, b: 50 },
    });
    Plotly.react(el, traces, layout, _plotlyConfig);
  }

  // Grouped Bar — handles {categories, series: [{name, values}]}
  function renderBseBar(id, fig) {
    var el = document.getElementById(id);
    if (!el || !fig || !fig.series) return;
    var COLORS = _PRISMA_CAT;
    var traces = fig.series.map(function(s, i) {
      return {
        x: fig.categories || [],
        y: s.values || [],
        name: s.name,
        type: "bar",
        marker: { color: COLORS[i % COLORS.length] },
      };
    });
    var layout = Object.assign({}, _plotlyDefaults, {
      title:    _bseTitle(fig.title),
      height:   320,
      barmode:  "group",
      yaxis:    _axisOpts({ title: fig.yaxis_title || "", tickformat: ".2f", ticksuffix: "%" }),
      xaxis:    _axisOpts({ tickangle: -20 }),
      margin:   { l: 58, r: 16, t: 44, b: 80 },
    });
    Plotly.react(el, traces, layout, _plotlyConfig);
  }

  // FX Mismatch Stacked Bar
  // Two bars on X: "FX Assets" and "FX Liabilities"; each product is a stack layer.
  // Hover shows balance + interest rate. Click on dominant side → drill-down rate chart.
  function renderBseMismatchBar(id, fig) {
    var el = document.getElementById(id);
    if (!el || !fig || !fig.series || !fig.series.length) {
      if (el) { el.style.display = "flex"; el.style.alignItems = "center";
                el.style.justifyContent = "center"; el.textContent = "Veri yok"; }
      return;
    }
    // Remove stale drill-down
    var prevDrill = document.getElementById(id + "-drillrow");
    if (prevDrill) prevDrill.remove();

    // Build deterministic name→color map so same product keeps same color across renders
    var _colorByName = {};
    fig.series.forEach(function(s, i) { _colorByName[s.name] = _PRISMA_CAT[i % _PRISMA_CAT.length]; });
    var xCats = ["FX Assets", "FX Liabilities"];

    // One trace per product; customdata carries [rate_str] per bar point
    var traces = fig.series.map(function(s, i) {
      var rA = s.asset_rate_pct != null ? s.asset_rate_pct.toFixed(2) + "%" : "N/A";
      var rL = s.liab_rate_pct  != null ? s.liab_rate_pct.toFixed(2)  + "%" : "N/A";
      return {
        name:      s.name,
        x:         xCats,
        y:         [s.asset_val || 0, s.liab_val || 0],
        type:      "bar",
        customdata: [[rA], [rL]],
        hovertemplate: "<b>" + s.name + "</b><br>%{x}: %{y:,.0f}<br>Rate: %{customdata[0]}<extra></extra>",
        // 0.78: 0.88 dark zeminde fazla doygun/parlak duruyordu (prisma tonu korunur)
        marker: { color: _colorByName[s.name], opacity: 0.78, line: { color: "#0A0E1A", width: 1 } },
      };
    });

    var annotations = [];
    if (fig.on_bs_gap !== undefined) {
      var gapLabel = (fig.on_bs_gap >= 0 ? "+" : "") +
                     Math.round(fig.on_bs_gap).toLocaleString();
      annotations.push({
        xref: "paper", yref: "paper",
        // y:1.02 + geniş üst margin: rozet plot ile başlık ARASINDA durur
        // (eskiden başlığın üstüne biniyordu).
        x: 0.5, y: 1.02, xanchor: "center", yanchor: "bottom",
        text: "On-BS FX Gap: <b>" + gapLabel + "</b>",
        showarrow: false,
        font: { size: 12, color: fig.on_bs_gap >= 0 ? "#7A9B7E" : "#B8826B" },
        bgcolor: fig.on_bs_gap >= 0 ? "rgba(122,155,126,0.15)" : "rgba(184,130,107,0.15)",
        borderpad: 4,
      });
    }

    var layout = Object.assign({}, _plotlyDefaults, {
      title:    Object.assign(_bseTitle(fig.title), { y: 0.985, yanchor: "top" }),
      height:   420,
      barmode:  "stack",
      yaxis:    _axisOpts({ title: fig.yaxis_title || "Balance (mn)", tickformat: ",.0f" }),
      xaxis:    _axisOpts({ tickfont: { size: 13, color: _plotInk() } }),
      margin:   { l: 64, r: 180, t: 92, b: 50 },
      annotations: annotations,
      legend:   { orientation: "v", x: 1.02, y: 1, xanchor: "left", font: { size: 11, color: "#A9B3C4" } },
    });
    Plotly.react(el, traces, layout, _plotlyConfig);

    // Click handler: only open drill-down for the dominant (larger) side
    if (fig.fx_product_drill && fig.fx_dominant_side) {
      el.removeAllListeners && el.removeAllListeners("plotly_click");
      el.on("plotly_click", function(ev) {
        if (!ev || !ev.points || !ev.points.length) return;
        var pt           = ev.points[0];
        var clickedCol   = pt.x;                 // "FX Assets" or "FX Liabilities"
        var productName  = pt.data && pt.data.name;
        var dominant     = fig.fx_dominant_side; // "Assets" or "Liabilities"
        if (!productName) return;
        // Smaller side click → do nothing
        if (dominant === "Assets"      && clickedCol !== "FX Assets")      return;
        if (dominant === "Liabilities" && clickedCol !== "FX Liabilities") return;
        // Zero-balance segment click → do nothing
        if (!pt.y || pt.y < 0.001) return;
        _showMismatchDrillDown(id, productName, clickedCol, fig);
      });
    }
  }

  // Drill-down line chart for FX mismatch bar click:
  //   dominant=Liabilities → TRY funding alternatives + FX Liab product synthetic rate
  //   dominant=Assets      → TRY asset alternatives  + FX Asset product synthetic rate
  function _showMismatchDrillDown(mismatchId, productName, clickedCol, fig) {
    var drillRowId   = mismatchId + "-drillrow";
    var drillChartId = mismatchId + "-drill";
    var el           = document.getElementById(mismatchId);
    var parentRow    = el.closest(".bse-chart-row");

    var existing = document.getElementById(drillRowId);
    if (existing) existing.remove();

    var drillRow = document.createElement("div");
    drillRow.id        = drillRowId;
    drillRow.className = "bse-chart-row single";
    drillRow.innerHTML =
      '<div class="card" style="position:relative;">' +
        '<button onclick="document.getElementById(\'' + drillRowId + '\').remove()"' +
          ' style="position:absolute;top:8px;right:10px;background:none;border:none;' +
          'cursor:pointer;font-size:15px;color:var(--text-secondary);z-index:1;" title="Kapat">✕</button>' +
        '<div class="plot-container" id="' + drillChartId + '"></div>' +
      '</div>';
    parentRow.insertAdjacentElement("afterend", drillRow);

    var dates   = fig.drill_dates || [];
    var dominant = fig.fx_dominant_side;
    var benchmarks = dominant === "Liabilities"
      ? fig.tl_liab_benchmarks
      : fig.tl_asset_benchmarks;
    var drillKey = productName + (clickedCol === "FX Assets" ? "__asset" : "__liab");
    var drill    = fig.fx_product_drill && fig.fx_product_drill[drillKey];

    var COLORS = ["#485166","#7A8399","#8891A4",   // benchmarks: soluk (PRISMA'da korunuyor)
                  "#B8826B"];                       // synthetic: terracotta
    var traces = [];
    var ci = 0;

    // Benchmark lines (dashed, muted)
    if (benchmarks) {
      Object.keys(benchmarks).forEach(function(bname) {
        var vals = benchmarks[bname];
        traces.push({
          x: dates, y: vals, name: bname,
          type: "scatter", mode: "lines+markers",
          line: { color: COLORS[ci % 3], width: 1.8, dash: "dot" },
          marker: { size: 4 },
          connectgaps: true,
          hovertemplate: bname + ": %{y:.2f}%<extra></extra>",
        });
        ci++;
      });
    }

    // Synthetic TRY rate of clicked product (FX rate + swap TL leg rate)
    if (drill) {
      var synLabel = productName + " Synthetic TRY";
      traces.push({
        x: dates, y: drill.synthetic, name: synLabel,
        type: "scatter", mode: "lines+markers",
        line: { color: "#B8826B", width: 2.5 },
        marker: { size: 6 },
        connectgaps: true,
        hovertemplate: synLabel + ": %{y:.2f}%<extra></extra>",
      });
    }

    var titleSide  = dominant === "Liabilities" ? "FX Liability" : "FX Asset";
    var benchLabel = dominant === "Liabilities"
      ? "TRY Funding Alternatives"
      : "TRY Asset Alternatives";

    Plotly.react(document.getElementById(drillChartId), traces,
      Object.assign({}, _plotlyDefaults, {
        title:  { text: titleSide + " Drill-Down: " + productName +
                        " — " + benchLabel + " vs Synthetic TRY Rate",
                  font: { size: 12 } },
        height: 300,
        yaxis:  _axisOpts({ title: "Rate (%)", tickformat: ".2f", ticksuffix: "%" }),
        xaxis:  _axisOpts({ type: "category" }),
        margin: { l: 64, r: 24, t: 50, b: 50 },
      }), _plotlyConfig);

    drillRow.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // Render Section A charts for the active currency (TRY or FX)
  function renderBseSectionA(data) {
    if (!data) return;
    var sfx = bseCurrencyA.toLowerCase();
    renderBseWaterfall("bse-a-vol-assets", data["volume_bridge_assets_" + sfx]);
    renderBseWaterfall("bse-a-vol-liab",   data["volume_bridge_liab_"   + sfx]);
    renderBseBubble("bse-a-comp-assets",   data["composition_assets_"   + sfx]);
    renderBseBubble("bse-a-comp-liab",     data["composition_liab_"     + sfx]);
  }

  // ── BS Evolution Section State Machine ────────────────────────────────────
  function toggleBseSection(key) {
    var s = bseSections[key];
    s.open = !s.open;
    var btn  = document.getElementById("bse-btn-" + key);
    var body = document.getElementById("bse-body-" + key);
    btn.classList.toggle("open", s.open);
    if (s.open) {
      body.style.maxHeight = body.scrollHeight + "px";
      body.addEventListener("transitionend", function onEnd() {
        body.removeEventListener("transitionend", onEnd);
        if (s.open) body.style.maxHeight = "none";
      });
      if (s.dirty) fetchBseSection(key);
    } else {
      body.style.maxHeight = body.scrollHeight + "px";
      body.offsetHeight;
      body.style.maxHeight = "0";
    }
  }

  function _bseIdentityBadge(badgeId, info) {
    var el = document.getElementById(badgeId);
    if (!el || !info) return;
    var gapPct = (info.nop_gap_pct || info.funding_gap_pct || 0) * 100;
    if (info.ok) {
      el.className = "bse-identity-badge ok";
      el.textContent = "Identity check OK (gap " + gapPct.toFixed(3) + "%)";
    } else {
      el.className = "bse-identity-badge err";
      el.textContent = "⚠ Identity check FAILED (gap " + gapPct.toFixed(3) + "% > 0.5%) — possible data inconsistency.";
    }
  }

  async function fetchBseSection(key) {
    var src = encodeURIComponent(getSourceForApi());
    var d0  = encodeURIComponent(document.getElementById("date0").value || "");
    var d1  = encodeURIComponent(document.getElementById("date1").value || "");
    try {
      if (key === "a") {
        var r = await fetch("/api/bs_evolution_growth?source=" + src + "&date_0=" + d0 + "&date_1=" + d1);
        var data = await r.json();
        if (!data.ok) { showError(data.error || "BSE Section A error"); return; }
        bseSections.a.dirty = false;
        bseDataA = data;
        renderBseSectionA(bseDataA);
        _bseIdentityBadge("bse-badge-a", data.bs_identity_info);
      } else if (key === "b") {
        var r = await fetch("/api/bs_evolution_fx_position?source=" + src + "&date_0=" + d0 + "&date_1=" + d1);
        var data = await r.json();
        if (!data.ok) { showError(data.error || "BSE Section B error"); return; }
        bseSections.b.dirty = false;
        renderBseWaterfall("bse-b-bridge",    data.fx_position_bridge);
        renderDualAxisLine("bse-b-nop",        data.nop_series);
        renderStackedArea("bse-b-hedge-comp",  data.hedge_composition);
        renderBseMismatchBar("bse-b-mismatch", data.fx_mismatch_bar);
        _bseIdentityBadge("bse-badge-b", data.bs_identity_info);
      } else if (key === "c") {
        var r = await fetch("/api/bs_evolution_funding?source=" + src + "&date_0=" + d0 + "&date_1=" + d1);
        var data = await r.json();
        if (!data.ok) { showError(data.error || "BSE Section C error"); return; }
        bseSections.c.dirty = false;
        renderBseBubble("bse-c-stack",   data.funding_stack);
        renderDualAxisLine("bse-c-share", data.synthetic_share);
        renderBseBar("bse-c-cost",        data.funding_cost_bar);
        _bseIdentityBadge("bse-badge-c", data.bs_identity_info);
      }
    } catch(e) {
      showError("BSE Section " + key.toUpperCase() + " error: " + (e.message || String(e)));
    }
  }

  function showError(msg) {
    elErr.textContent = msg || "";
    elErr.classList.toggle("hidden", !msg);
  }

  function updatePageVisibility() {
    const isSim    = currentPage === "simulation-results";
    const isCross  = currentPage === "cross-scenario";
    const isManual = currentPage === "users-manual";
    const isCost   = currentPage === "cost-analysis";
    const isTenor  = currentPage === "tenor-analysis";
    const isBal    = currentPage === "balance-analysis";
    const isWeekly = currentPage === "weekly-report";
    const isNp     = currentPage.startsWith("np-");
    const isSector = currentPage === "sector-comparison";
    const isStd    = !isSim && !isCross && !isManual && !isCost && !isTenor && !isBal && !isWeekly && !isNp && !isSector;
    var sectorSec = document.getElementById("sector-comparison-section");
    if (sectorSec) sectorSec.classList.toggle("hidden", !isSector);
    var npFiltersEl = document.getElementById("np-filters");
    var npSectionEl = document.getElementById("np-section");
    // Eski global np-filters barı emekli — 3 NB sayfası da ORTAK paneli kullanır.
    if (npFiltersEl) npFiltersEl.classList.add("hidden");
    if (npSectionEl) npSectionEl.classList.toggle("hidden", !isNp);
    if (isNp) {
      [["np-vp-section", "np-volume-pricing"]].forEach(function(pair) {
        var el = document.getElementById(pair[0]);
        if (el) el.classList.toggle("hidden", currentPage !== pair[1]);
      });
      _syncNpSharedFilterUI();
    }
    var costSec = document.getElementById("cost-analysis-section");
    if (costSec) costSec.classList.toggle("hidden", !isCost);
    var tenorSec = document.getElementById("tenor-analysis-section");
    if (tenorSec) tenorSec.classList.toggle("hidden", !isTenor);
    var taMonSec = document.getElementById("ta-mon-section");
    var taDlySec = document.getElementById("ta-dly-section");
    if (taMonSec) taMonSec.classList.toggle("hidden", !(isTenor && taTab === "monthly-averages"));
    if (taDlySec) taDlySec.classList.toggle("hidden", !(isTenor && taTab === "daily-evolution"));
    var balSec = document.getElementById("balance-analysis-section");
    if (balSec) balSec.classList.toggle("hidden", !isBal);
    var baMonSec = document.getElementById("ba-mon-section");
    var baDlySec = document.getElementById("ba-dly-section");
    if (baMonSec) baMonSec.classList.toggle("hidden", !(isBal && baTab === "monthly-averages"));
    if (baDlySec) baDlySec.classList.toggle("hidden", !(isBal && baTab === "daily-evolution"));
    var wrSec = document.getElementById("weekly-report-section");
    if (wrSec) wrSec.classList.toggle("hidden", !isWeekly);
    if (isWeekly && typeof initWeeklyReport === "function") initWeeklyReport();

    // Cost Analysis sub-tabs: Monthly Averages / Daily Evolution.
    // ddd-section now lives under cost-analysis-section, so its visibility is
    // governed by caTab instead of currentTab.
    var caMonSec = document.getElementById("ca-mon-section");
    var dddSec   = document.getElementById("ddd-section");
    if (caMonSec) caMonSec.classList.toggle("hidden", !(isCost && caTab === "monthly-averages"));
    if (dddSec)   dddSec.classList.toggle("hidden",   !(isCost && caTab === "daily-evolution"));
  }

  // ── Weekly Report (modüler slider) ──────────────────────────────────────────
  // Yeni slide eklemek: WEEKLY_SLIDES dizisine `{ id, title, render }` ekle ve
  // index.html'e `<template id="<id>-tpl">…</template>` yerleştir. Slide render
  // fonksiyonu mevcut weeklyReportState.payload'ı görür — tarih state'i tüm
  // slide'lar arasında otomatik paylaşılır.
  var weeklyReportState = {
    initialized: false,
    slideIdx: 0,
    dateStart: null,
    dateEnd:   null,
    payload:           null,   // Slide 1 (tablo 3'lüsü + vade bucket histogramı)
    segmentsPayload:   null,   // Slide 2 (eski Slide 3)
    payloadKey: null,          // dateStart||dateEnd — same key => skip fetch
    grids: {},                 // { hostId: gridApi } — destroy on re-render
  };

  function _renderWeeklySlide1(payload) {
    if (!payload) return;
    _renderWeeklyGrid("wr-grid-1", payload.table_1);
    _renderWeeklyGrid("wr-grid-2", payload.table_2);
    _renderWeeklyGrid("wr-grid-3", payload.table_3);
    _renderWeeklyDtmHistogram("wr-s1-dtm", payload.dtm_histogram);
  }

  // Slider yapısı — her slide:
  //   id        : HTML <template> ID prefix (örn. "wr-slide-3")
  //   title     : Sidebar/nav label
  //   endpoint  : Veri çekilecek API (Slide 1 mevcut payload'ı kullanır → "")
  //   stateKey  : weeklyReportState'te payload key (örn. "segmentsPayload")
  //   render    : (payload) => DOM doldurucu
  var WEEKLY_SLIDES = [
    { id: "wr-slide-1", title: "Deposit Rollover Report",
      endpoint: "",                 stateKey: "payload",         render: _renderWeeklySlide1 },
    { id: "wr-slide-3", title: "Customer Segment & Top Customers",
      endpoint: "weekly_segments",  stateKey: "segmentsPayload", render: _renderWeeklySlide3 },
  ];

  // DD/MM/YYYY format helpers — backend ile pipeline uçtan uca DD/MM/YYYY.
  function _wrFmt(d) {
    var dd = String(d.getDate()).padStart(2,"0"),
        mm = String(d.getMonth()+1).padStart(2,"0"),
        yy = d.getFullYear();
    return dd + "/" + mm + "/" + yy;
  }
  function _wrValid(s) { return /^\d{2}\/\d{2}\/\d{4}$/.test(s || ""); }
  function _wrToDate(s) {
    var p = s.split("/");
    return new Date(+p[2], +p[1]-1, +p[0]);
  }
  function _wrWeekBounds() {
    // Bulunduğun haftanın Pazartesi ve Cuma tarihleri (Pzt=1..Paz=0).
    var t = new Date();
    var day = t.getDay();
    var diffMon = (day === 0) ? -6 : 1 - day;
    var mon = new Date(t); mon.setDate(t.getDate() + diffMon);
    var fri = new Date(mon); fri.setDate(mon.getDate() + 4);
    return [mon, fri];
  }

  function initWeeklyReport() {
    if (weeklyReportState.initialized) return;
    weeklyReportState.initialized = true;

    if (!weeklyReportState.dateStart) {
      var wb = _wrWeekBounds();
      weeklyReportState.dateStart = _wrFmt(wb[0]);
      weeklyReportState.dateEnd   = _wrFmt(wb[1]);
    }

    var fpShared = {
      dateFormat: "d/m/Y",
      allowInput: false,
      locale: { firstDayOfWeek: 1 },
      onChange: function() {
        weeklyReportState.dateStart = document.getElementById("wr-date-start").value;
        weeklyReportState.dateEnd   = document.getElementById("wr-date-end").value;
        _fetchWeeklyData();
      }
    };
    flatpickr(document.getElementById("wr-date-start"),
      Object.assign({}, fpShared, { defaultDate: weeklyReportState.dateStart }));
    flatpickr(document.getElementById("wr-date-end"),
      Object.assign({}, fpShared, { defaultDate: weeklyReportState.dateEnd }));

    document.getElementById("wr-prev").addEventListener("click", function(){ _navigateWeekly(-1); });
    document.getElementById("wr-next").addEventListener("click", function(){ _navigateWeekly(+1); });

    _renderWeeklySlideHost();
    _fetchWeeklyData();
  }

  function _setWeeklyStatus(msg) {
    var el = document.getElementById("wr-status");
    if (el) el.textContent = msg || "";
  }

  function _fetchWeeklyData() {
    var ds = weeklyReportState.dateStart, de = weeklyReportState.dateEnd;
    if (!ds || !de || !_wrValid(ds) || !_wrValid(de)) { _setWeeklyStatus("Select dates."); return; }
    if (_wrToDate(ds) > _wrToDate(de)) { _setWeeklyStatus("Date Start cannot be after Date End."); return; }
    var key = ds + "||" + de;
    // Cache invalidation: tarih değiştiğinde tüm slide payload'ları sıfırlanır
    if (key !== weeklyReportState.payloadKey) {
      WEEKLY_SLIDES.forEach(function(s) {
        if (s.stateKey) weeklyReportState[s.stateKey] = null;
      });
      weeklyReportState.payloadKey = key;
    }
    // Mevcut slide'ı render et — gerekirse endpoint'i lazy fetch ile çek
    _ensureAndRenderSlide();
  }

  function _ensureAndRenderSlide() {
    var idx = weeklyReportState.slideIdx;
    if (idx >= WEEKLY_SLIDES.length) idx = 0;
    var slide = WEEKLY_SLIDES[idx];
    var existing = weeklyReportState[slide.stateKey];
    if (existing) {
      _renderWeeklySlideHost();
      return;
    }
    var ep = slide.endpoint || "weekly_rollings";
    var ds = weeklyReportState.dateStart, de = weeklyReportState.dateEnd;
    _setWeeklyStatus("Loading…");
    fetch("/api/" + ep + "?date_start=" + encodeURIComponent(ds) +
          "&date_end=" + encodeURIComponent(de))
      .then(function(r){ return r.json(); })
      .then(function(j){
        if (!j.ok) { _setWeeklyStatus("Hata: " + (j.error || "?")); return; }
        weeklyReportState[slide.stateKey] = j;
        _setWeeklyStatus(j.row_count > 0
          ? ("Veri: " + j.row_count + " rows.")
          : "No data found in the selected date range.");
        _renderWeeklySlideHost();
      })
      .catch(function(e){ _setWeeklyStatus("Hata: " + (e.message || e)); });
  }

  function _navigateWeekly(delta) {
    var nextIdx = weeklyReportState.slideIdx + delta;
    if (nextIdx < 0 || nextIdx >= WEEKLY_SLIDES.length) return;
    weeklyReportState.slideIdx = nextIdx;
    _ensureAndRenderSlide();
  }

  function _renderWeeklySlideHost() {
    var idx = weeklyReportState.slideIdx;
    if (idx >= WEEKLY_SLIDES.length) idx = 0;
    var slide = WEEKLY_SLIDES[idx];

    var lbl = document.getElementById("wr-slide-label");
    if (lbl) lbl.textContent = "Slide " + (idx+1) + " / " + WEEKLY_SLIDES.length;
    document.getElementById("wr-prev").disabled = (idx === 0);
    document.getElementById("wr-next").disabled = (idx >= WEEKLY_SLIDES.length - 1);

    // Mevcut AG Grid instance'larını yok et, DOM'u temizle.
    Object.keys(weeklyReportState.grids).forEach(function(id) {
      try { weeklyReportState.grids[id].destroy(); } catch(e) {}
    });
    weeklyReportState.grids = {};

    var host = document.getElementById("wr-slide-host");
    host.innerHTML = "";
    var tpl = document.getElementById(slide.id + "-tpl");
    if (tpl && tpl.content) host.appendChild(tpl.content.cloneNode(true));
    var data = weeklyReportState[slide.stateKey];
    try { slide.render(data); }
    catch(e) { _setWeeklyStatus("Render error: " + (e.message || e)); }
  }

  function _renderWeeklyGrid(hostId, tbl) {
    var host = document.getElementById(hostId);
    if (!host) return;
    if (!tbl || !tbl.rows || tbl.rows.length === 0) {
      host.innerHTML = '<div style="padding:16px;color:var(--text-muted);font-size:13px;">'
                     + 'No data found in the selected date range.</div>';
      return;
    }

    var isCurrencyKind = (tbl.kind === "currency");

    // Column-wise max from data rows (footer excluded — ezmemek için).
    var colMaxes = tbl.columns.map(function(_, j) {
      var mx = 0;
      tbl.rows.forEach(function(r) { var v = Math.abs(r.values[j] || 0); if (v > mx) mx = v; });
      return mx || 1;
    });

    // 7-stop PRISMA sequential palette. Dark = koyu navy → amber; light = krem
    // → koyu amber (kremde dark hücreler kalmasın). Yazı da mode-aware.
    var _hmLt = _hmLight();
    var _HM_STOPS = _hmLt
      ? ["#FAF7F0","#F0E9D8","#E5D4A8","#D4A574","#B8860B","#96700A","#6E5008"]
      : ["#131826","#1F2433","#1B2236","#4A6B8A","#8B95A7","#B8946A","#D4A574"];
    function _hmBg(v, mx) {
      if (!v || mx <= 0) return null;
      var k = Math.min(1.0, Math.abs(v) / mx);
      return _HM_STOPS[Math.min(6, Math.floor(k * 7))];
    }
    function _hmColor(v, mx) {
      if (!v || mx <= 0) return _hmLt ? "#8A8680" : "#E4E8F0";
      var k = Math.min(1.0, Math.abs(v) / mx);
      if (_hmLt) return k >= 4 / 7 ? "#FFFEFA" : "#2C2A26";   // yüksek=koyu amber→beyaz; düşük=krem→grafit
      return k >= 5 / 7 ? "#0A0E1A" : "#E4E8F0";
    }
    function _hmWeight(v, mx) {
      if (!v || mx <= 0) return undefined;
      return Math.abs(v) / mx >= 5 / 7 ? 500 : undefined;
    }
    function _fmtNum(p) {
      if (p.value == null) return "0";
      return Number(p.value).toLocaleString("en-US", { maximumFractionDigits: 0 });
    }

    // ── Column definitions ─────────────────────────────────────────────────
    var colDefs = [];
    if (isCurrencyKind) {
      colDefs.push({
        field: "_label", headerName: "Ccy", width: 64, suppressMovable: true,
        cellStyle: function(p) {
          var base = { textAlign: "left" };
          if (p.node.rowPinned) return Object.assign(base, { color: "var(--text-primary)", fontWeight: 600 });
          return Object.assign(base, { color: "var(--accent)", fontWeight: 600, letterSpacing: "0.04em" });
        }
      });
    }
    colDefs.push({
      field: "_date", headerName: "Date", width: 122, suppressMovable: true,
      cellStyle: function(p) {
        var base = { textAlign: "left" };
        if (p.node.rowPinned) return Object.assign(base, { color: "var(--text-primary)", fontWeight: 600 });
        return Object.assign(base, { color: "var(--text-primary)", fontSize: "12px" });
      }
    });
    tbl.columns.forEach(function(band, j) {
      (function(jj) {
        colDefs.push({
          field: "b" + jj, headerName: band,
          width: 106, suppressMovable: true, type: "numericColumn",
          valueFormatter: _fmtNum,
          cellStyle: function(p) {
            if (p.node.rowPinned) return { fontWeight: 600, color: "var(--text-primary)" };
            var bg = _hmBg(p.value, colMaxes[jj]);
            var tc = _hmColor(p.value, colMaxes[jj]);
            var fw = _hmWeight(p.value, colMaxes[jj]);
            return Object.assign(
              { fontVariantNumeric: "tabular-nums" },
              bg ? { background: bg } : {},
              tc ? { color: tc } : {},
              fw ? { fontWeight: fw } : {}
            );
          }
        });
      })(j);
    });
    colDefs.push({
      field: "_total", headerName: "Total", width: 116, suppressMovable: true, type: "numericColumn",
      valueFormatter: _fmtNum,
      cellStyle: function(p) {
        return { fontWeight: 700, color: "var(--text-primary)",
                 borderLeft: "1px solid var(--border-mid)", fontVariantNumeric: "tabular-nums" };
      }
    });
    colDefs.push({
      field: "_pct", headerName: "% Total", width: 76, suppressMovable: true,
      valueFormatter: function(p) { return p.value != null ? p.value + "%" : ""; },
      cellStyle: function(p) {
        return { textAlign: "right", color: p.value != null ? "var(--accent)" : "var(--text-muted)",
                 fontWeight: 500, fontVariantNumeric: "tabular-nums" };
      }
    });

    // ── Row data ───────────────────────────────────────────────────────────
    var rowData = tbl.rows.map(function(row) {
      var obj = { _date: row.date || "", _label: row.label || "",
                  _total: row.total,     _pct: row.pct_of_total };
      tbl.columns.forEach(function(_, j) { obj["b" + j] = row.values[j]; });
      return obj;
    });

    // Footer → pinnedBottomRowData
    var pinnedData = (tbl.footer || []).map(function(fr) {
      var obj = { _date: isCurrencyKind ? "" : fr.label,
                  _label: isCurrencyKind ? fr.label : "",
                  _total: fr.total, _pct: null };
      tbl.columns.forEach(function(_, j) { obj["b" + j] = fr.values[j]; });
      return obj;
    });

    // ── Create grid ────────────────────────────────────────────────────────
    host.innerHTML = "";
    var api = agGrid.createGrid(host, {
      columnDefs: colDefs,
      rowData: rowData,
      pinnedBottomRowData: pinnedData,
      headerHeight: 34,
      rowHeight: 30,
      domLayout: "autoHeight",
      suppressHorizontalScroll: false,
      suppressCellFocus: false,
      defaultColDef: { resizable: true, sortable: false, filter: false },
      onCellClicked: function(e) {
        if (!e.data) return;
        // Drill-down açan hücreler: b0..bN (belirli AUM band) veya _total
        // (tüm bandlar). _date/_label/_pct hücreleri drill-down açmaz.
        var f = e.colDef.field;
        if (f !== "_total" && !/^b\d+$/.test(f)) return;

        // band: "" = tüm bandlar (Total kolonu), aksi halde belirli band
        var band = "";
        if (/^b\d+$/.test(f)) {
          band = tbl.columns[parseInt(f.substring(1), 10)] || "";
        }

        var isPinned = !!e.node.rowPinned;
        // Pinned satır = footer (FX/TRY/Total) → tüm tarihler için drill-down
        var rollDate = isPinned ? "" : (e.data._date || "");
        var rowLabel = e.data._label || "";

        // Currency context:
        //  - currency tablosu (T1): _label = "TRY" | "FX" | "Total" (pinned)
        //      → "Total" satırı tüm CCY'leri içerir (currency boş kalır)
        //  - try_gercek / try_tuzel tablo (T2/T3): hep TRY
        var currency = "";
        if (isCurrencyKind) {
          if (rowLabel === "TRY" || rowLabel === "FX") currency = rowLabel;
        } else {
          currency = "TRY";
        }
        var cust_tp = (hostId === "wr-grid-2") ? "G"
                    : (hostId === "wr-grid-3") ? "T" : "";

        _openWeeklyDrill({
          roll_date: rollDate,
          aum_band:  band,
          currency:  currency,
          cust_tp:   cust_tp,
          // Başlık/subtitle için meta
          dateStart: weeklyReportState.dateStart,
          dateEnd:   weeklyReportState.dateEnd,
        });
      },
    });
    weeklyReportState.grids[hostId] = api;
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Weekly Report — Plotly common config
  // ────────────────────────────────────────────────────────────────────────────
  function _wrPlotlyLayout(extra) {
    var axBase = { gridcolor: "rgba(255,255,255,0.06)", zerolinecolor: "rgba(255,255,255,0.12)", linecolor: "rgba(255,255,255,0.08)", color: "#7A8399" };
    var base = {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor:  "rgba(0,0,0,0)",
      font:    { family: "var(--font-body)", size: 11, color: "#E4E8F0" },
      margin:  { t: 30, l: 50, r: 20, b: 50 },
      xaxis:   axBase,
      yaxis:   axBase,
      legend:  { orientation: "h", y: -0.18, font: { size: 11 } },
    };
    var result = Object.assign({}, base, extra || {});
    if (extra && extra.xaxis)  result.xaxis  = Object.assign({}, axBase, extra.xaxis);
    if (extra && extra.yaxis)  result.yaxis  = Object.assign({}, axBase, extra.yaxis);
    if (extra && extra.yaxis2) result.yaxis2 = Object.assign({}, axBase, extra.yaxis2);
    return result;
  }
  function _wrPlotlyConfig() {
    return { displayModeBar: false, responsive: true };
  }
  function _wrStatCard(label, value, sub) {
    return '<div style="flex:1;min-width:140px;padding:10px 12px;background:var(--bg-surface);'
         + 'border:1px solid var(--border-mid);border-radius:6px;">'
         + '<div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:var(--text-muted);">' + label + '</div>'
         + '<div style="font-size:18px;font-weight:600;color:var(--text-primary);margin-top:4px;">' + value + '</div>'
         + (sub ? '<div style="font-size:11px;color:var(--text-muted);margin-top:2px;">' + sub + '</div>' : '')
         + '</div>';
  }
  function _wrFmtM(v) { return Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 }) + " M"; }
  function _wrFmt2(v) { return Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  // Segment renkleri — donut & stacked bar arası tutarlı
  var WR_SEGMENT_COLORS = {
    "Private":  "#D4A574",
    "Affluent": "#6B8FA8",
    "Salaried": "#7A9B7E",
    "NPO":      "#8B7BA8",
    "Corporate": "#B8946A",
    "Other":    "#6B7589",
  };
  function _wrSegColor(s) { return WR_SEGMENT_COLORS[s] || "#8B95A7"; }

  // ────────────────────────────────────────────────────────────────────────────
  // SLIDE 1 — Vade Bucket Histogramı (3 tablonun altında)
  // ────────────────────────────────────────────────────────────────────────────
  function _renderWeeklyDtmHistogram(hostId, rows) {
    var el = document.getElementById(hostId);
    if (!el) return;
    if (!rows || rows.length === 0) { _wrEmptyHost(hostId); return; }
    Plotly.newPlot(hostId, [{
      type: "bar",
      x: rows.map(function(r){ return r.bucket; }),
      y: rows.map(function(r){ return r.volume_m; }),
      text: rows.map(function(r){ return _wrFmtM(r.volume_m); }),
      textposition: "outside", cliponaxis: false,
      textfont: { size: 12 },
      hovertemplate: "%{x}<br>%{y:.0f} mio TRY<br>%{customdata} adet<extra></extra>",
      customdata: rows.map(function(r){ return r.ticket_count; }),
      marker: { color: "#D4A574" },
    }], _wrPlotlyLayout({
      title: { text: "Maturity Bucket Distribution (mio TRY)", font: { size: 14 } },
      margin: { t: 52, l: 50, r: 20, b: 50 },   // outside etiket + başlık çakışmasın
      xaxis: { type: "category", title: "Maturity (days)" },
      yaxis: { title: "Balance (mio TRY)" },
    }), _wrPlotlyConfig());
  }

  // ────────────────────────────────────────────────────────────────────────────
  // SLIDE 2 — Segment & Müşteri Listesi (template ID: wr-slide-3-tpl)
  // ────────────────────────────────────────────────────────────────────────────
  function _renderWeeklySlide3(p) {
    if (!p || !p.segments || p.segments.length === 0) {
      _wrEmptyHost("wr-s3-donut"); _wrEmptyHost("wr-s3-stacked");
      var hh = document.getElementById("wr-s3-hhi"); if (hh) hh.textContent = "";
      var dl = document.getElementById("wr-s3-date-label"); if (dl) dl.textContent = "—";
      var g  = document.getElementById("wr-s3-grid"); if (g) g.innerHTML = "";
      return;
    }

    // ── Donut — dönem geneli segment dağılımı ─────────────────────────────
    Plotly.newPlot("wr-s3-donut", [{
      type: "pie", hole: 0.55,
      labels: p.segments.map(function(s){ return s.segment; }),
      values: p.segments.map(function(s){ return s.volume_m; }),
      text:   p.segments.map(function(s){ return s.customer_count + " customers"; }),
      marker: { colors: p.segments.map(function(s){ return _wrSegColor(s.segment); }) },
      hovertemplate: "%{label}<br>%{value:.0f} mio TRY (%{percent})<br>%{text}<extra></extra>",
      textinfo: "label+percent",
    }], _wrPlotlyLayout({
      title: { text: "Segment Distribution — Full Period (mio TRY)", font:{size:14}},
      showlegend: false, margin: { t: 40, l: 10, r: 10, b: 10 },
    }), _wrPlotlyConfig());

    // ── Stacked bar — tarih × segment ─────────────────────────────────────
    var traces = (p.all_segments || []).map(function(seg) {
      return {
        type: "bar", name: seg,
        x: p.by_date.map(function(r){ return r.date; }),
        y: p.by_date.map(function(r){ return r[seg] || 0; }),
        marker: { color: _wrSegColor(seg) },
        hovertemplate: seg + "<br>%{x}<br>%{y:.0f} mio TRY<extra></extra>",
      };
    });
    Plotly.newPlot("wr-s3-stacked", traces, _wrPlotlyLayout({
      title: { text: "Date × Segment (mio TRY) — click a date to refresh the customer list",
               font:{size:12}},
      barmode: "stack", xaxis: { type: "category" },
    }), _wrPlotlyConfig());

    // Plotly bar click → müşteri listesini seçilen tarihe göre yenile
    document.getElementById("wr-s3-stacked").on("plotly_click", function(data) {
      if (!data.points || !data.points.length) return;
      var dateStr = data.points[0].x;
      _wrRenderSegmentCustomerGrid(dateStr, p);
    });

    // ── HHI (dönem geneli) ─────────────────────────────────────────────────
    var hhi = p.hhi || 0;
    var hhiLabel = hhi > 2500 ? "High concentration" : hhi > 1500 ? "Orta" : "Balanced distribution";
    document.getElementById("wr-s3-hhi").innerHTML =
        "HHI (period): <b style='color:var(--text-primary)'>" + hhi.toFixed(0) + "</b> — " + hhiLabel;

    // İlk tarihle başla
    var firstDate = (p.dates && p.dates.length > 0) ? p.dates[0] : null;
    if (firstDate) _wrRenderSegmentCustomerGrid(firstDate, p);
  }

  // Seçili tarihin müşteri listesini wr-s3-grid'e render eder.
  // CCY rozet renderer'ı — Prisma palette ile renkli kapsül
  var WR_CCY_CLASS = { TRY: "wr-ccy-TRY", USD: "wr-ccy-USD", EUR: "wr-ccy-EUR",
                       GBP: "wr-ccy-GBP", CHF: "wr-ccy-CHF" };
  function _wrCcyBadge(p) {
    if (!p.value) return "";
    var cls = WR_CCY_CLASS[p.value] || "wr-ccy-OTH";
    return '<span class="wr-ccy-badge ' + cls + '">' + p.value + "</span>";
  }
  // Segment chip renderer — _wrSegColor üzerinden renkli arka plan
  function _wrSegmentChip(p) {
    if (!p.value) return "";
    var c = _wrSegColor(p.value);
    return '<span class="wr-seg-chip" style="background:' + c
         + '22;color:' + c + ';">' + p.value + "</span>";
  }
  // % Pay — in-cell mini bar arka plan (max row üzerinden ölçeklenir)
  function _wrShareCellRenderer(maxShare) {
    return function(p) {
      var v = Number(p.value || 0);
      var pct = maxShare > 0 ? Math.min(100, (v / maxShare) * 100) : 0;
      return '<div class="wr-share-cell">'
           +   '<div class="wr-share-bar" style="width:' + pct.toFixed(1) + '%;"></div>'
           +   '<span class="wr-share-text">' + v.toFixed(2) + '%</span>'
           + '</div>';
    };
  }

  function _wrRenderSegmentCustomerGrid(dateStr, p) {
    var lbl = document.getElementById("wr-s3-date-label");
    if (lbl) lbl.textContent = dateStr;

    var rows = (p.customers_by_date && p.customers_by_date[dateStr]) || [];
    // Grid daha önce oluşturulmuşsa destroy et (weeklyReportState.grids üzerinden)
    if (weeklyReportState.grids["wr-s3-grid"]) {
      try { weeklyReportState.grids["wr-s3-grid"].destroy(); } catch(e) {}
      delete weeklyReportState.grids["wr-s3-grid"];
    }
    var host = document.getElementById("wr-s3-grid");
    if (!host) return;
    if (!rows || rows.length === 0) { _wrEmptyHost("wr-s3-grid"); return; }

    // In-cell bar ölçeği için max paylaşımı bul
    var maxShare = rows.reduce(function(m, r){ return Math.max(m, r.share_pct || 0); }, 0);

    var api = agGrid.createGrid(host, {
      columnDefs: [
        { field: "full_nm",  headerName: "Customer", width: 200,
          suppressMovable: true, sortable: true,
          cellRenderer: function(p){
            return '<span class="wr-cust-name">' + (p.value || "") + "</span>";
          }},
        { field: "ccy_code", headerName: "CCY", width: 90,
          suppressMovable: true, sortable: true,
          cellStyle: { display: "flex", alignItems: "center", justifyContent: "center" },
          cellRenderer: _wrCcyBadge },
        { field: "segment",  headerName: "Segment", width: 110,
          suppressMovable: true, sortable: true,
          cellRenderer: _wrSegmentChip },
        { field: "volume_m", headerName: "Hacim (mio)", width: 130,
          suppressMovable: true, sortable: true, type: "numericColumn",
          cellClass: "wr-num-accent",
          valueFormatter: function(p){ return _wrFmt2(p.value); } },
        { field: "share_pct", headerName: "% Share", width: 130,
          suppressMovable: true, sortable: true, type: "numericColumn",
          cellStyle: { padding: 0 },
          cellRenderer: _wrShareCellRenderer(maxShare) },
        { field: "ticket_count", headerName: "Count", width: 80,
          suppressMovable: true, sortable: true, type: "numericColumn",
          cellClass: "wr-num-soft" },
        { field: "avg_rate", headerName: "Avg. Rate", width: 110,
          suppressMovable: true, sortable: true, type: "numericColumn",
          cellClass: "wr-num-strong",
          valueFormatter: function(p){ return (p.value||0).toFixed(2) + "%"; } },
        { field: "avg_dtm", headerName: "Ort. DTM", width: 100,
          suppressMovable: true, sortable: true, type: "numericColumn",
          cellClass: "wr-num-soft",
          valueFormatter: function(p){ return p.value != null ? p.value + " g" : ""; } },
      ],
      rowData: rows,
      headerHeight: 38,
      rowHeight: 36,
      domLayout: "autoHeight",
      suppressCellFocus: true,
      defaultColDef: { resizable: true, filter: false },
    });
    weeklyReportState.grids["wr-s3-grid"] = api;
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Helpers
  // ────────────────────────────────────────────────────────────────────────────
  function _wrEmptyHost(id) {
    var el = document.getElementById(id); if (!el) return;
    el.innerHTML = '<div style="padding:16px;color:var(--text-muted);font-size:12px;">Veri yok.</div>';
  }
  function _wrSegmentCellStyle(p) {
    if (!p.value) return null;
    return { color: _wrSegColor(p.value), fontWeight: 500 };
  }
  function _wrSimpleGrid(hostId, rows, columnDefs) {
    var host = document.getElementById(hostId); if (!host) return;
    host.innerHTML = "";
    if (!rows || rows.length === 0) { _wrEmptyHost(hostId); return; }
    var api = agGrid.createGrid(host, {
      columnDefs: columnDefs.map(function(c){ return Object.assign({suppressMovable:true, sortable:true}, c); }),
      rowData: rows,
      headerHeight: 34,
      rowHeight: 28,
      domLayout: "autoHeight",
      suppressCellFocus: true,
      defaultColDef: { resizable: true, filter: false },
    });
    weeklyReportState.grids[hostId] = api;
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Drill-down modal — Slide 1 hücre tıklamasıyla açılır
  // ────────────────────────────────────────────────────────────────────────────
  function _openWeeklyDrill(ctx) {
    var modal = document.getElementById("wr-drill-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    document.getElementById("wr-drill-close").onclick = function(){
      modal.classList.add("hidden");
      // Modal kapanırken AG Grid instance'ını destroy et
      if (weeklyReportState.grids["wr-drill-cust-grid"]) {
        try { weeklyReportState.grids["wr-drill-cust-grid"].destroy(); } catch(e) {}
        delete weeklyReportState.grids["wr-drill-cust-grid"];
      }
      ["wr-drill-cust-grid","wr-drill-rate-hist","wr-drill-dtm-hist","wr-drill-segments"]
        .forEach(function(id){ var el = document.getElementById(id); if (el) el.innerHTML = ""; });
    };

    // Başlık: Tarih (boşsa "Dönem Geneli")
    var dateLabel;
    if (ctx.roll_date) {
      dateLabel = ctx.roll_date;
    } else if (ctx.dateStart && ctx.dateEnd) {
      dateLabel = "Full Period (" + ctx.dateStart + " – " + ctx.dateEnd + ")";
    } else {
      dateLabel = "Full Period";
    }
    // Subtitle: AUM band / CCY / Müşteri tipi — boşları "Tüm ..." olarak göster
    var subParts = [];
    subParts.push(ctx.aum_band ? "AUM " + ctx.aum_band : "All AUM Bands");
    subParts.push(ctx.currency ? "CCY " + ctx.currency : "All Currencies");
    if (ctx.cust_tp) subParts.push(ctx.cust_tp === "G" ? "Individual Customers" : "Corporate Customers");
    document.getElementById("wr-drill-title").textContent = "Cell Detail — " + dateLabel;
    document.getElementById("wr-drill-subtitle").textContent = subParts.join(" · ");
    document.getElementById("wr-drill-count").textContent = "(loading…)";

    var ds = weeklyReportState.dateStart, de = weeklyReportState.dateEnd;
    var url = "/api/weekly_drilldown?date_start=" + encodeURIComponent(ds)
            + "&date_end=" + encodeURIComponent(de)
            + (ctx.roll_date ? "&roll_date=" + encodeURIComponent(ctx.roll_date) : "")
            + (ctx.aum_band  ? "&aum_band="  + encodeURIComponent(ctx.aum_band)  : "")
            + (ctx.currency  ? "&currency="  + encodeURIComponent(ctx.currency)  : "")
            + (ctx.cust_tp   ? "&cust_tp="   + encodeURIComponent(ctx.cust_tp)   : "");
    fetch(url).then(function(r){return r.json();}).then(function(j){
      if (!j.ok) {
        document.getElementById("wr-drill-count").textContent = "Hata: " + (j.error || "?");
        return;
      }
      document.getElementById("wr-drill-count").textContent =
        "(" + j.row_count + " tickets · " + _wrFmtM(j.total_volume_m||0) + " TRY)";

      // Faiz histogramı
      Plotly.newPlot("wr-drill-rate-hist", [{
        type:"bar",
        x:(j.rate_histogram||[]).map(function(r){return r.bucket;}),
        y:(j.rate_histogram||[]).map(function(r){return r.volume_m;}),
        marker:{color:"#D4A574"},
        hovertemplate:"%{x}<br>%{y:.1f} mio TRY<extra></extra>",
      }], _wrPlotlyLayout({
        title:{text:"Rate Histogram", font:{size:12}}, margin:{t:30,l:40,r:10,b:60},
        xaxis:{type:"category"},
      }), _wrPlotlyConfig());

      // DTM histogramı
      Plotly.newPlot("wr-drill-dtm-hist", [{
        type:"bar",
        x:(j.dtm_histogram||[]).map(function(r){return r.bucket;}),
        y:(j.dtm_histogram||[]).map(function(r){return r.volume_m;}),
        marker:{color:"#4A6B8A"},
        hovertemplate:"%{x}<br>%{y:.1f} mio TRY<extra></extra>",
      }], _wrPlotlyLayout({
        title:{text:"Maturity Histogram", font:{size:12}}, margin:{t:30,l:40,r:10,b:60},
        xaxis:{type:"category"},
      }), _wrPlotlyConfig());

      // Segment donut
      Plotly.newPlot("wr-drill-segments", [{
        type:"pie", hole:0.55,
        labels:(j.segments||[]).map(function(s){return s.segment;}),
        values:(j.segments||[]).map(function(s){return s.volume_m;}),
        marker:{colors:(j.segments||[]).map(function(s){return _wrSegColor(s.segment);})},
        hovertemplate:"%{label}<br>%{value:.1f} mio (%{percent})<extra></extra>",
        textinfo:"label+percent",
      }], _wrPlotlyLayout({
        title:{text:"Segment Distribution", font:{size:12}},
        showlegend:false, margin:{t:30,l:10,r:10,b:10},
      }), _wrPlotlyConfig());

      // ── Müşteri grid'i (Prisma stilinde, Slide 2 ile aynı görsel dil) ──
      _wrRenderDrillCustomerGrid(j.customers || []);
    }).catch(function(e){
      document.getElementById("wr-drill-count").textContent = "Hata: " + (e.message || e);
    });
  }

  // Drill-down modal müşteri grid'i — Slide 2 ile aynı Prisma stili
  function _wrRenderDrillCustomerGrid(rows) {
    var host = document.getElementById("wr-drill-cust-grid");
    if (!host) return;
    if (weeklyReportState.grids["wr-drill-cust-grid"]) {
      try { weeklyReportState.grids["wr-drill-cust-grid"].destroy(); } catch(e) {}
      delete weeklyReportState.grids["wr-drill-cust-grid"];
    }
    if (!rows || rows.length === 0) { _wrEmptyHost("wr-drill-cust-grid"); return; }

    var maxShare = rows.reduce(function(m, r){ return Math.max(m, r.share_pct || 0); }, 0);

    var api = agGrid.createGrid(host, {
      columnDefs: [
        { field: "full_nm",  headerName: "Customer", width: 200,
          suppressMovable: true, sortable: true,
          cellRenderer: function(p){
            return '<span class="wr-cust-name">' + (p.value || "") + "</span>";
          }},
        { field: "ccy_code", headerName: "CCY", width: 90,
          suppressMovable: true, sortable: true,
          cellStyle: { display: "flex", alignItems: "center", justifyContent: "center" },
          cellRenderer: _wrCcyBadge },
        { field: "segment",  headerName: "Segment", width: 110,
          suppressMovable: true, sortable: true,
          cellRenderer: _wrSegmentChip },
        { field: "volume_m", headerName: "Hacim (mio)", width: 130,
          suppressMovable: true, sortable: true, type: "numericColumn",
          cellClass: "wr-num-accent",
          valueFormatter: function(p){ return _wrFmt2(p.value); } },
        { field: "share_pct", headerName: "% Cell", width: 130,
          suppressMovable: true, sortable: true, type: "numericColumn",
          cellStyle: { padding: 0 },
          cellRenderer: _wrShareCellRenderer(maxShare) },
        { field: "avg_rate", headerName: "Avg. Rate", width: 110,
          suppressMovable: true, sortable: true, type: "numericColumn",
          cellClass: "wr-num-strong",
          valueFormatter: function(p){ return (p.value||0).toFixed(2) + "%"; } },
        { field: "avg_dtm", headerName: "Ort. DTM", width: 100,
          suppressMovable: true, sortable: true, type: "numericColumn",
          cellClass: "wr-num-soft",
          valueFormatter: function(p){ return p.value != null ? p.value + " g" : ""; } },
        { field: "kampanya", headerName: "Kamp.", width: 80,
          suppressMovable: true, sortable: true,
          cellStyle: { display: "flex", alignItems: "center", justifyContent: "center" },
          cellRenderer: function(p){
            return p.value
              ? '<span style="color:var(--accent);font-size:14px;">●</span>'
              : '<span style="color:var(--text-muted);font-size:12px;">—</span>';
          }},
      ],
      rowData: rows,
      headerHeight: 38,
      rowHeight: 36,
      domLayout: "autoHeight",
      suppressCellFocus: true,
      defaultColDef: { resizable: true, filter: false },
    });
    weeklyReportState.grids["wr-drill-cust-grid"] = api;
  }

  // ── Raw Data (AG Grid) ──────────────────────────────────────────────────────

  // 0 = Rate view (default), 1 = NII view
  var rawTableView = 0;
  var rawTableCache = null;  // { records, summary, date0, date1 }

  function buildRawColDefs(date0, date1, view) {
    function fmtNum(p) {
      if (p.value == null || isNaN(p.value)) return "";
      return Math.round(p.value).toLocaleString("en-US");
    }
    function fmtRate(p) {
      if (p.value == null || isNaN(p.value)) return "";
      return (p.value * 100).toFixed(2) + "%";
    }
    function fmtDeltaBal(p) {
      if (p.value == null || isNaN(p.value)) return "";
      const v = Math.round(p.value);
      return (v >= 0 ? "+" : "") + v.toLocaleString("en-US");
    }
    function fmtBps(p) {
      if (p.value == null || isNaN(p.value)) return "";
      return (p.value >= 0 ? "+" : "") + p.value.toFixed(1) + " bps";
    }
    function deltaStyle(p) {
      if (p.value > 0) return { color: "#7A9B7E", fontWeight: "500" };
      if (p.value < 0) return { color: "#B8826B", fontWeight: "500" };
      return {};
    }
    var productCol = {
      field: "PRODUCT_NAME",
      headerName: "Product",
      width: 280,
      pinned: "left",
      sortable: false,
      cellRenderer: function(params) {
        const d = params.data;
        if (!d) return "";
        const v = params.value || "";
        if (d._type === "toplevel") {
          return '<span style="font-weight:700">' + v + "</span>";
        }
        if (d._type === "sub-leaf") {
          return '<span style="padding-left:56px;display:inline-block">' + v + "</span>";
        }
        if (d._type === "leaf") {
          if (d._hasChildren) {
            const ico = collapsedGroups.has(d._groupId) ? "▶" : "▼";
            return '<span style="padding-left:40px;cursor:pointer;display:inline-block">'
                   + '<span style="font-size:9px;margin-right:3px;color:#485166">' + ico + "</span>"
                   + v + "</span>";
          }
          return '<span style="padding-left:40px;display:inline-block">' + v + "</span>";
        }
        // group
        const collapsed = collapsedGroups.has(d._groupId);
        const icon = collapsed ? "▶" : "▼";
        const indent = (d._level || 0) * 20;
        const fw = d._level === 0 ? "700" : "600";
        return (
          '<span style="cursor:pointer;padding-left:' + indent + "px;font-weight:" + fw + '">' +
          icon + " " + v +
          "</span>"
        );
      },
    };

    if (view === 1) {
      // NII view: Balance + Interest Income/Cost, Delta NII
      return [
        productCol,
        {
          headerName: date0,
          children: [
            { field: "balance_0", headerName: "Balance",               width: 145, type: "numericColumn", valueFormatter: fmtNum },
            { field: "nii_0",     headerName: "Interest Income/Cost",  width: 155, type: "numericColumn", valueFormatter: fmtNum },
          ],
        },
        {
          headerName: date1,
          children: [
            { field: "balance_1", headerName: "Balance",               width: 145, type: "numericColumn", valueFormatter: fmtNum },
            { field: "nii_1",     headerName: "Interest Income/Cost",  width: 155, type: "numericColumn", valueFormatter: fmtNum },
          ],
        },
        {
          headerName: "Δ Change",
          children: [
            { field: "delta_balance", headerName: "Δ Balance", width: 145, type: "numericColumn", valueFormatter: fmtDeltaBal, cellStyle: deltaStyle },
            { field: "delta_nii",     headerName: "Δ NII",     width: 120, type: "numericColumn", valueFormatter: fmtDeltaBal, cellStyle: deltaStyle },
          ],
        },
      ];
    }

    // Rate view (default)
    return [
      productCol,
      {
        headerName: date0,
        children: [
          { field: "balance_0", headerName: "Balance", width: 145, type: "numericColumn", valueFormatter: fmtNum },
          { field: "rate_0",    headerName: "Rate",    width: 90,  type: "numericColumn", valueFormatter: fmtRate },
        ],
      },
      {
        headerName: date1,
        children: [
          { field: "balance_1", headerName: "Balance", width: 145, type: "numericColumn", valueFormatter: fmtNum },
          { field: "rate_1",    headerName: "Rate",    width: 90,  type: "numericColumn", valueFormatter: fmtRate },
        ],
      },
      {
        headerName: "Δ Change",
        children: [
          { field: "delta_balance",  headerName: "Δ Balance",    width: 145, type: "numericColumn", valueFormatter: fmtDeltaBal, cellStyle: deltaStyle },
          { field: "delta_rate_bps", headerName: "Δ Rate (bps)", width: 120, type: "numericColumn", valueFormatter: fmtBps,       cellStyle: deltaStyle },
        ],
      },
    ];
  }

  function syncTblNav() {
    document.getElementById("tbl-prev").disabled = (rawTableView === 0);
    document.getElementById("tbl-next").disabled = (rawTableView === 1);
    document.getElementById("tbl-view-label").textContent = rawTableView === 0 ? "Rate" : "NII";
  }

  function setRawTableView(v) {
    rawTableView = v;
    syncTblNav();
    if (rawTableCache && rawGridApi) {
      rawGridApi.setGridOption("columnDefs", buildRawColDefs(rawTableCache.date0, rawTableCache.date1, rawTableView));
    }
  }

  function initRawGrid(records, summary, date0, date1) {
    rawTableCache = { records: records, summary: summary, date0: date0, date1: date1 };
    syncTblNav();
    if (rawGridApi) {
      rawGridApi.destroy();
      rawGridApi = null;
    }
    // Start fully collapsed: add every group ID (and expandable leaf IDs) so only
    // top-level rows are visible initially.
    collapsedGroups = new Set(
      records
        .filter(function(r) {
          return (r._type === "group" || (r._type === "leaf" && r._hasChildren)) && r._groupId;
        })
        .map(function(r) { return r._groupId; })
    );

    rawGridApi = agGrid.createGrid(document.getElementById("rawDataGrid"), {
      columnDefs: buildRawColDefs(date0, date1, rawTableView),
      rowData: records,
      pinnedBottomRowData: summary,
      isExternalFilterPresent: function() { return true; },
      doesExternalFilterPass: function(params) {
        const d = params.data;
        if (!d || !d._ancestors || d._ancestors.length === 0) return true;
        return !d._ancestors.some(function(id) { return collapsedGroups.has(id); });
      },
      onRowClicked: function(params) {
        const d = params.data;
        if (!d || (d._type !== "group" && !(d._type === "leaf" && d._hasChildren))) return;
        if (collapsedGroups.has(d._groupId)) collapsedGroups.delete(d._groupId);
        else collapsedGroups.add(d._groupId);
        rawGridApi.onFilterChanged();
        rawGridApi.refreshCells({ columns: ["PRODUCT_NAME"], force: true });
      },
      defaultColDef: { resizable: true, sortable: false },
      domLayout: "autoHeight",
      suppressMenuHide: true,
      animateRows: false,
      getRowStyle: function(params) {
        const d = params.data;
        if (!d) return {};
        if (params.node.rowPinned) return { fontWeight: "600", background: "rgba(74,107,138,0.12)" };
        if (d._type === "toplevel") return { background: "rgba(212,165,116,0.1)", fontWeight: "700" };
        if ((d._type === "leaf" || d._type === "sub-leaf") && highlightedProduct &&
            d.PRODUCT_NAME === highlightedProduct.productName &&
            Array.isArray(d._ancestors) &&
            d._ancestors[0] === highlightedProduct.bsType.toLowerCase().replace(/ /g, "_")) {
          return { background: "rgba(122,155,126,0.40)", borderLeft: "3px solid #9BBE9F", fontWeight: "600" };
        }
        if (d._type === "group" && d._level === 0) return { background: "rgba(255,255,255,0.06)", cursor: "pointer" };
        if (d._type === "group" && d._level === 1) return { background: "rgba(255,255,255,0.04)", cursor: "pointer" };
        return {};
      },
    });
  }

  async function fetchRawData() {
    if (currentPage !== "standard") return;
    const d0 = elDate0.value, d1 = elDate1.value;
    if (!d0 || !d1) return;
    const url = "/api/raw_data?source=" + encodeURIComponent(getSourceForApi()) +
                "&date_0=" + encodeURIComponent(d0) + "&date_1=" + encodeURIComponent(d1);
    try {
      const res = await fetch(url);
      const data = await res.json();
      if (!data.ok) { showError(data.error || "Raw data could not be loaded"); return; }
      sections.table.dirty = false;
      initRawGrid(data.records, data.summary || [], data.date_0, data.date_1);
      if (pendingNavigation) {
        var nav = pendingNavigation;
        pendingNavigation = null;
        setTimeout(function() { doNavigateInGrid(nav.bsType, nav.productName); }, 200);
      }
    } catch (e) {
      showError(e.message || "Raw data could not be loaded");
    }
  }

  async function fetchHistoric() {
    const url = "/api/historic?source=" + encodeURIComponent(getSourceForApi());
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) { showError(data.error || "Historic data could not be loaded"); return; }
    sections.historic.dirty = false;
    renderChart("hist1", data.fig);
  }

  async function fetchWaterfall() {
    showError("");
    const src = getSourceForApi(), d0 = elDate0.value, d1 = elDate1.value;
    if (!d0 || !d1) return;
    elStatus.textContent = "Loading...";
    const nimType = elNimType.value;
    const url = "/api/waterfalls?source=" + encodeURIComponent(src) +
                "&date_0=" + encodeURIComponent(d0) + "&date_1=" + encodeURIComponent(d1) +
                "&nim_type=" + encodeURIComponent(nimType);
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) { showError(data.error || "Hata"); elStatus.textContent = ""; return; }
    sections.waterfall.dirty = false;
    const ni = data.nim_info || {};
    wfFigs = data.figs || {};
    renderWfSlide(wfSlide);   // re-render current slide with fresh data
    elStatus.innerHTML = "nim_start: <code>" + (ni.nim_start != null ? (Number(ni.nim_start) * 10000).toFixed(1) + " bps" : "—") + "</code> | nim_end: <code>" + (ni.nim_end != null ? (Number(ni.nim_end) * 10000).toFixed(1) + " bps" : "—") + "</code> | ΔNIM: <code>" + (ni.nim_change != null ? (ni.nim_change * 10000).toFixed(1) + " bps" : "—") + "</code>";
  }

  // Standalone export: Realized NII tarih listesinin son elemanı (Scenario
  // Analysis'in otomatik Date(Start) değeri olarak kullanılır)
  function _exportRealizedLastDate() {
    try {
      var D   = window.__NIM_EMBEDDED__;
      var key = (D.aliases && D.aliases["Realized NII"]) || "Realized NII";
      var ds  = D.by_source[key].dates.dates;
      return ds[ds.length - 1] || null;
    } catch(e) { return null; }
  }

  async function refreshDates() {
    showError("");
    elStatus.textContent = "Loading dates...";
    try {
      const res = await fetch("/api/dates?source=" + encodeURIComponent(getSourceForApi()));
      const data = await res.json();
      if (!data.ok) { elStatus.textContent = ""; showError(data.error || "Dates could not be loaded"); return; }
      const dates = data.dates || [];
      elDate0.innerHTML = "";
      elDate1.innerHTML = "";
      dates.forEach(d => { elDate0.appendChild(new Option(d, d)); elDate1.appendChild(new Option(d, d)); });
      if (dates.length >= 2) {
        if (simScenarioMode) {
          if (IS_EXPORT) {
            // Export: Date(Start) = Realized NII'nin son tarihi, Date(End) = senaryonun son tarihi
            var rLast = _exportRealizedLastDate();
            elDate0.value = (rLast && dates.indexOf(rLast) >= 0) ? rLast : dates[0];
            elDate1.value = dates[dates.length - 1];
            if (elDate0.value === elDate1.value) elDate0.value = dates[dates.length - 2];
          } else {
            elDate0.value = dates[0];     // Scenario Analysis: first date
            elDate1.value = dates[1];     // Scenario Analysis: second date
          }
        } else {
          elDate0.value = dates[dates.length - 2]; // Historical NII: second-to-last
          elDate1.value = dates[dates.length - 1]; // Historical NII: last
        }
      } else if (dates.length === 1) { elDate0.value = dates[0]; elDate1.value = dates[0]; }
      elStatus.textContent = "";
      // Trigger fetch for any open sections with new dates
      onParamsChange();
    } catch(e) {
      elStatus.textContent = "";
      showError(e.message || "Dates could not be loaded");
    }
  }

  async function fetchSimulationResults() {
    showError("");
    elSimStatus.textContent = "Loading...";
    try {
      const res = await fetch("/api/simulation_results");
      const data = await res.json();
      if (!data.ok) { showError(data.error || "Hata"); elSimStatus.textContent = ""; return; }
      const figs = data.figs || {};

      // CBRT chart — full width, no carousel
      if (figs.cbrt) renderChart("sr0", figs.cbrt);

      // NIM carousel: [Total NIM, TRY NIM]
      simNimFigs = [];
      if (figs.total_nim) simNimFigs.push(figs.total_nim);
      if (figs.try_nim)   simNimFigs.push(figs.try_nim);
      simNimSlide = 0;
      if (simNimFigs.length > 0) renderSimNimSlide(0);

      // Loans carousel: one fig per scenario
      simLoansFigs = figs.loans || [];
      simLoansSlide = 0;
      // Reset extra product overlays and repopulate product dropdown
      loansExtraData   = {};
      loansColorIdx    = 0;
      loansAllProducts = (figs.products || []);
      syncLoansDropdown();
      renderLoansExtraTags();
      var loansCard = document.getElementById("loans-carousel-card");
      if (simLoansFigs.length > 0) {
        loansCard.style.display = "";
        renderSimLoansSlide(0);
      } else {
        loansCard.style.display = "none";
      }

      // Simulation Balance-Sheet table — build scenario list from loans figs
      simScenarios = (figs.loans || [])
        .filter(function(f) { return f.source; })
        .map(function(f) { return { name: f.source, source: f.source }; });
      simBsData   = {};   // clear cache on new load
      simBsSource = null;
      _renderSbtScnDropdown();
      if (simScenarios.length > 0) {
        fetchSimBsTable(simScenarios[0].source);
      }

      singleTitle.textContent = "Simulation Results";
      elSimStatus.textContent = "";
    } catch(e) {
      showError(e.message || "Hata");
      elSimStatus.textContent = "";
    }
  }

  var NP_PAGE_TITLES = {
    "np-volume-pricing":   "New Business — Volume & Pricing",
    "sector-comparison":   "Sector Comparison",
  };

  var DEPOSIT_PAGE_TITLES = {
    "cost-analysis":    "Outstanding Cost Analysis",
    "balance-analysis": "Outstanding Balance Analysis",
    "tenor-analysis":   "Outstanding Tenor Analysis",
    "weekly-report":    "Future Deposit Rollings",
  };

  function updateTitle() {
    singleTitle.textContent = NP_PAGE_TITLES[currentPage] ||
      DEPOSIT_PAGE_TITLES[currentPage] || "Deposit Dashboard";
  }

  function setDataSource(source) {
    simScenarioMode   = false;
    crossScenarioMode = false;
    currentPage = "standard";
    currentDataSource = source;
    wfSlide = 0; wfFigs = null;
    currentTab = "nim-evolution";
    document.querySelectorAll(".nim-tab-btn").forEach(function(b) {
      b.classList.toggle("active", b.dataset.tab === "nim-evolution");
    });
    Object.keys(sections).forEach(function(k) { sections[k].dirty = true; });
    bseDataA = null;
    Object.keys(bseSections).forEach(function(k) { bseSections[k].dirty = true; });
    ddFigs = null; ddSlide = 0;
    document.getElementById("scenario-label").style.display = "none";
    updatePageVisibility();
    setActiveNav();
    updateTitle();
    refreshDates();
  }

  function setPage(pageName) {
    simScenarioMode   = false;
    crossScenarioMode = false;
    currentPage = pageName;
    updatePageVisibility();
    setActiveNav();
    updateTitle();
    if (pageName.startsWith("np-")) {
      initNpMeta(function() { refreshNpPage(); });
    } else if (pageName === "simulation-results") {
      fetchSimulationResults();
    } else if (pageName === "cost-analysis") {
      if (caTab === "monthly-averages") {
        ensureCaMonDatesLoaded().then(fetchCaMonWaterfalls);
      } else if (caTab === "daily-evolution") {
        loadDailyDepositDates().then(function() {
          _setDailyDefaultDates("ddd", Array.from(dddDateSet || []).sort());
          fetchDailyDepositWaterfalls();
        });
      }
    } else if (pageName === "tenor-analysis") {
      ensureTenorDatesLoaded().then(function() {
        _renderTaFilterPanels();   // paylaşılan/ayna gruplar görünsün
        if (taTab === "monthly-averages") fetchTenorMonthly();
        else {
          _setDailyDefaultDates("ta-dly", taDailyDates);
          fetchTenorDaily();
        }
      });
    } else if (pageName === "balance-analysis") {
      ensureBalanceDatesLoaded().then(function() {
        _renderBaFilterPanels();   // paylaşılan/ayna gruplar görünsün
        if (baTab === "monthly-averages") fetchBalanceMonthly();
        else {
          _setDailyDefaultDates("ba-dly", baDailyDates);
          fetchBalanceDaily();
        }
      });
    } else if (pageName === "sector-comparison") {
      fetchSectorRates();
      fetchTcmbRates();
      fetchSectorBlotter();
      fetchSectorOutstanding();
      fetchSectorOutstandingMonthly();
      fetchSectorMixAttribution();
      fetchSectorVadeMix();
    }
    // Standard pages: sections handle their own lazy fetching
  }

  function setCrossScenarioMode() {
    crossScenarioMode = true;
    simScenarioMode   = false;
    currentPage = "cross-scenario";
    wfSlide = 0; wfFigs = null;
    Object.keys(sections).forEach(function(k) { sections[k].dirty = true; });
    updatePageVisibility();
    setActiveNav();
    updateTitle();
    refreshCrossDates();
  }

  async function refreshCrossDates() {
    showError("");
    elCrossStatus.textContent = "Loading dates...";
    try {
      var src1 = elCrossScn1.value, src2 = elCrossScn2.value;
      if (!src1 || !src2) return;
      var url = "/api/cross_dates?source1=" + encodeURIComponent(src1) +
                "&source2=" + encodeURIComponent(src2);
      var res = await fetch(url);
      var data = await res.json();
      if (!data.ok) { elCrossStatus.textContent = ""; showError(data.error || "Dates could not be loaded"); return; }
      var dates = data.dates || [];
      elCrossDate.innerHTML = "";
      dates.forEach(function(d) { elCrossDate.appendChild(new Option(d, d)); });
      if (dates.length > 0) elCrossDate.value = dates[dates.length - 1];
      elCrossStatus.textContent = "";
      onCrossParamsChange();
    } catch(e) {
      elCrossStatus.textContent = "";
      showError(e.message || "Dates could not be loaded");
    }
  }

  function onCrossParamsChange() {
    Object.keys(sections).forEach(function(k) { sections[k].dirty = true; });
    Object.keys(sections).forEach(function(k) { if (sections[k].open) fetchSection(k); });
  }

  async function fetchCrossWaterfall() {
    showError("");
    var src1 = elCrossScn1.value, src2 = elCrossScn2.value, date = elCrossDate.value;
    var nimType = elCrossNimType.value;
    if (!src1 || !src2 || !date) return;
    elCrossStatus.textContent = "Loading...";
    var url = "/api/cross_waterfalls?source1=" + encodeURIComponent(src1) +
              "&source2="  + encodeURIComponent(src2) +
              "&date="     + encodeURIComponent(date) +
              "&nim_type=" + encodeURIComponent(nimType);
    var res = await fetch(url);
    var data = await res.json();
    if (!data.ok) { showError(data.error || "Hata"); elCrossStatus.textContent = ""; return; }
    sections.waterfall.dirty = false;
    var ni = data.nim_info || {};
    wfFigs = data.figs || {};
    renderWfSlide(wfSlide);
    elCrossStatus.innerHTML = "nim_start: <code>" + (ni.nim_start != null ? (Number(ni.nim_start)*10000).toFixed(1)+" bps" : "—") + "</code> | nim_end: <code>" + (ni.nim_end != null ? (Number(ni.nim_end)*10000).toFixed(1)+" bps" : "—") + "</code> | ΔNIM: <code>" + (ni.nim_change != null ? (ni.nim_change*10000).toFixed(1)+" bps" : "—") + "</code>";
  }

  async function fetchCrossHistoric() {
    var src1 = elCrossScn1.value, src2 = elCrossScn2.value;
    if (!src1 || !src2) return;
    var url = "/api/cross_historic?source1=" + encodeURIComponent(src1) +
              "&source2=" + encodeURIComponent(src2);
    var res = await fetch(url);
    var data = await res.json();
    if (!data.ok) { showError(data.error || "Historic data could not be loaded"); return; }
    sections.historic.dirty = false;
    renderChart("hist1", data.fig);
  }

  async function fetchCrossRawData() {
    var src1 = elCrossScn1.value, src2 = elCrossScn2.value, date = elCrossDate.value;
    var nimType = elCrossNimType.value;
    if (!src1 || !src2 || !date) return;
    var url = "/api/cross_raw_data?source1=" + encodeURIComponent(src1) +
              "&source2="  + encodeURIComponent(src2) +
              "&date="     + encodeURIComponent(date) +
              "&nim_type=" + encodeURIComponent(nimType);
    try {
      var res = await fetch(url);
      var data = await res.json();
      if (!data.ok) { showError(data.error || "Raw data could not be loaded"); return; }
      sections.table.dirty = false;
      initRawGrid(data.records, data.summary || [], data.date_0, data.date_1);
    } catch(e) {
      showError(e.message || "Raw data could not be loaded");
    }
  }










  // Heatmap mode switches (iOS-style toggle) — delegated click handler.
  // Clicking anywhere on the switch flips the mode; the two labels also flip it.
  document.querySelectorAll(".hm-switch").forEach(function(sw) {
    sw.addEventListener("click", function(ev) {
      var prefix = sw.dataset.prefix;
      var kind   = sw.dataset.kind;
      var lbl    = ev.target.closest(".hm-lbl");
      // Tenor Analysis TENOR ↔ DTM anahtarı (mod değerleri delta/abs değil).
      if (kind === "tamode") {
        var tm = (lbl && lbl.dataset.mode)
          ? lbl.dataset.mode
          : (taTenorMode === "tenor" ? "dtm" : "tenor");
        _setTaTenorMode(tm);
        return;
      }
      // Sector Comparison mix ayrıştırması Compound ↔ Simple anahtarı.
      if (kind === "sectormix") {
        var sm = (lbl && lbl.dataset.mode)
          ? lbl.dataset.mode
          : (_sectorMixMode === "comp" ? "simple" : "comp");
        _setSectorMixMode(sm);
        return;
      }
      // Vade Dağılımı Monthly Averages ↔ Daily Evolution anahtarı.
      if (kind === "sectorvade") {
        var sv = (lbl && lbl.dataset.mode)
          ? lbl.dataset.mode
          : (_sectorVadeMode === "monthly" ? "daily" : "monthly");
        _setSectorVadeMode(sv);
        return;
      }
      // Slide 4 aylık tablo grafiği 0-1 M ↔ 1-3 M anahtarı.
      if (kind === "bscnptbl") {
        var bt = (lbl && lbl.dataset.mode)
          ? lbl.dataset.mode
          : ((_bsc && _bsc.npTblBucket === "m0_1") ? "m1_3" : "m0_1");
        _bscSetNpTblBucket(bt);
        return;
      }
      // BSC Presentation Monthly Averages ↔ Daily Evolution anahtarı.
      if (kind === "bscmode") {
        var bm = (lbl && lbl.dataset.mode)
          ? lbl.dataset.mode
          : (_bsc && _bsc.mode === "monthly" ? "daily" : "monthly");
        _bscSetMode(bm);
        return;
      }
      var newMode;
      if (lbl && lbl.dataset.mode) {
        newMode = lbl.dataset.mode;
      } else {
        // Toggle: clicking the knob/track flips the current mode
        var curr = (kind === "balance" ? baHmMode[prefix]
                    : kind === "customer" ? baCustHmMode[prefix]
                    : caRateHmMode[prefix]) || "delta";
        newMode = (curr === "delta") ? "abs" : "delta";
      }
      if (kind === "balance")       _setBaHmMode(prefix, newMode);
      else if (kind === "customer") _setBaCustHmMode(prefix, newMode);
      else                          _setCaRateHmMode(prefix, newMode);
    });
  });

  // Balance / Customer heatmap METRİK kaydırma slider'ları (ba-mon / ba-dly).
  // range input (0=Balance, 1=Customer) sürüklenince/değişince grafik şeridi
  // kayar; uçtaki etikete tıklamak da o metriğe kaydırır.
  ["ba-mon", "ba-dly"].forEach(function(prefix) {
    var range = document.getElementById(prefix + "-metric-range");
    if (range) range.addEventListener("input", function() {
      _setBaHmMetric(prefix, range.value === "1" ? "customer" : "balance");
    });
    var slider = document.getElementById(prefix + "-metric-slider");
    if (slider) slider.querySelectorAll(".hm-ms-lbl").forEach(function(lbl) {
      var pick = function() { _setBaHmMetric(prefix, lbl.dataset.metric); };
      lbl.addEventListener("click", pick);
      // Klavye erişilebilirliği: uç etiketler Enter/Space ile de çalışsın.
      lbl.addEventListener("keydown", function(ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); }
      });
    });
    // İlk kurulumda DOM'u JS state'iyle (baHmMetric) hizala — tek doğruluk
    // kaynağı; animasyonsuz uygula (açılışta kayma efekti olmasın).
    _setBaHmMetric(prefix, baHmMetric[prefix] || "balance", false);
  });

  // Cost Analysis > Monthly Averages carousel nav
  var caMonPrev = document.getElementById("ca-mon-prev");
  var caMonNext = document.getElementById("ca-mon-next");
  if (caMonPrev) caMonPrev.addEventListener("click", function() {
    if (caMonSlide > 0) renderCaMonSlide(caMonSlide - 1);
  });
  if (caMonNext) caMonNext.addEventListener("click", function() {
    if (caMonSlide < CA_MON_SLIDES.length - 1) renderCaMonSlide(caMonSlide + 1);
  });

  // PRODUCT ↔ SUBPRODUCT karşılıklı dışlama: biri AKTİF edilince diğeri pasifleşir
  // (ikisi birden seçilemez). Yalnız aktif-etme yönünde çalışır.
  function _applyProductSubMutex(dim, state, selector) {
    if (dim !== "PRODUCT" && dim !== "SUBPRODUCT") return;
    if (!state[dim]) return;
    var other = (dim === "PRODUCT") ? "SUBPRODUCT" : "PRODUCT";
    if (state[other]) {
      state[other] = false;
      var ob = document.querySelector(selector + '[data-dim="' + other + '"]');
      if (ob) ob.classList.remove("active");
    }
  }

  // Daily Deposit Detail dimension toggles
  document.querySelectorAll(".ddd-dim-btn").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var dim = btn.dataset.dim;
      var selectedCount = DDD_DIMS.filter(function(d) { return dddDims[d]; }).length;
      if (dddDims[dim] && selectedCount <= 1) return;
      dddDims[dim] = !dddDims[dim];
      btn.classList.toggle("active", dddDims[dim]);
      _applyProductSubMutex(dim, dddDims, ".ddd-dim-btn");
      fetchDailyDepositWaterfalls();
    });
  });

  // Rate Type seçicileri (Outstanding Cost) — değişince sekmenin TÜM faiz
  // gösterimleri yeniden çekilir (waterfall+bubble+KPI tek fetch'te; heatmap ayrı).
  [["ca-mon", function() { fetchCaMonWaterfalls(); _fetchCaRateHeatmap("ca-mon"); }],
   ["ddd",    function() { fetchDailyDepositWaterfalls(); _fetchCaRateHeatmap("ddd"); }]
  ].forEach(function(pair) {
    var el = document.getElementById(pair[0] + "-rate-conv");
    if (el) el.addEventListener("change", pair[1]);
  });

  // Daily Deposit Detail accordion toggle
  document.getElementById("acc-btn-ddd-wf").addEventListener("click", function() {
    dddWfOpen = !dddWfOpen;
    this.classList.toggle("open", dddWfOpen);
    var body = document.getElementById("acc-body-ddd-wf");
    if (dddWfOpen) {
      // Use "none" so newly-shown companions can grow the box freely.
      body.style.maxHeight = "none";
      body.style.overflow  = "visible";
      if (dddFigs) renderDddSlide(dddSlide);
    } else {
      body.style.maxHeight = "0";
      body.style.overflow  = "hidden";
    }
  });

  // Daily Deposit Detail carousel prev/next
  document.getElementById("ddd-prev").addEventListener("click", function() {
    if (dddSlide > 0) renderDddSlide(dddSlide - 1);
  });
  document.getElementById("ddd-next").addEventListener("click", function() {
    if (dddSlide < DDD_SLIDES.length - 1) renderDddSlide(dddSlide + 1);
  });

  // Tenor Analysis Monthly waterfall carousel
  var taMonPrev = document.getElementById("ta-mon-wf-prev");
  var taMonNext = document.getElementById("ta-mon-wf-next");
  if (taMonPrev) taMonPrev.addEventListener("click", function() {
    if (taMonWfSlide > 0) { taMonWfSlide--; _renderTenorWfSlide("ta-mon", taMonPayload, taMonWfSlide); }
  });
  if (taMonNext) taMonNext.addEventListener("click", function() {
    if (taMonWfSlide < TA_WF_SLIDES.length - 1) { taMonWfSlide++; _renderTenorWfSlide("ta-mon", taMonPayload, taMonWfSlide); }
  });

  // Tenor Analysis Daily waterfall carousel
  var taDlyPrev = document.getElementById("ta-dly-wf-prev");
  var taDlyNext = document.getElementById("ta-dly-wf-next");
  if (taDlyPrev) taDlyPrev.addEventListener("click", function() {
    if (taDlyWfSlide > 0) { taDlyWfSlide--; _renderTenorWfSlide("ta-dly", taDlyPayload, taDlyWfSlide); }
  });
  if (taDlyNext) taDlyNext.addEventListener("click", function() {
    if (taDlyWfSlide < TA_WF_SLIDES.length - 1) { taDlyWfSlide++; _renderTenorWfSlide("ta-dly", taDlyPayload, taDlyWfSlide); }
  });

  // Daily Deposit Detail date picker changes
  document.getElementById("ddd-date0").addEventListener("change", fetchDailyDepositWaterfalls);
  document.getElementById("ddd-date1").addEventListener("change", fetchDailyDepositWaterfalls);







  // Deposit Dashboard > Cost Analysis nav
  document.querySelectorAll("#deposit-nav a").forEach(a => {
    a.addEventListener("click", function(e) {
      e.preventDefault();
      // BSC Presentation normal sayfa DEĞİL — tam-ekran sunum kabuğunu açar.
      if (this.dataset.page === "bsc-presentation") { _bscOpen(); return; }
      setPage(this.dataset.page);
    });
  });

  // Collapsible sidebar groups (both start expanded)
  document.querySelectorAll(".sidebar-group-header").forEach(function(h) {
    h.addEventListener("click", function() {
      this.parentElement.classList.toggle("collapsed");
    });
  });

  // Cost Analysis sub-tab switch
  document.querySelectorAll('.nim-tab-btn[data-ca-tab]').forEach(function(btn) {
    btn.addEventListener("click", function() {
      caTab = btn.dataset.caTab;
      document.querySelectorAll('.nim-tab-btn[data-ca-tab]').forEach(function(b) {
        b.classList.toggle("active", b.dataset.caTab === caTab);
      });
      updatePageVisibility();
      if (caTab === "monthly-averages") {
        ensureCaMonDatesLoaded().then(fetchCaMonWaterfalls);
      } else if (caTab === "daily-evolution") {
        loadDailyDepositDates().then(function() {
          _setDailyDefaultDates("ddd", Array.from(dddDateSet || []).sort());
          fetchDailyDepositWaterfalls();
        });
      }
    });
  });

  // Monthly Averages date dropdowns → re-fetch on change
  ["ca-mon-date0", "ca-mon-date1"].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("change", fetchCaMonWaterfalls);
  });

  // Monthly Averages dimension toggles (parallel to dd-section)
  document.querySelectorAll(".ca-mon-dim-btn").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var dim = btn.dataset.dim;
      var selectedCount = DD_DIMS.filter(function(d) { return caMonDims[d]; }).length;
      if (caMonDims[dim] && selectedCount <= 1) return;
      caMonDims[dim] = !caMonDims[dim];
      btn.classList.toggle("active", caMonDims[dim]);
      _applyProductSubMutex(dim, caMonDims, ".ca-mon-dim-btn");
      fetchCaMonWaterfalls();
    });
  });

  // Tenor Analysis sub-tab switch
  document.querySelectorAll('.nim-tab-btn[data-ta-tab]').forEach(function(btn) {
    btn.addEventListener("click", function() {
      taTab = btn.dataset.taTab;
      document.querySelectorAll('.nim-tab-btn[data-ta-tab]').forEach(function(b) {
        b.classList.toggle("active", b.dataset.taTab === taTab);
      });
      updatePageVisibility();
      ensureTenorDatesLoaded().then(function() {
        if (taTab === "monthly-averages") fetchTenorMonthly();
        else {
          _setDailyDefaultDates("ta-dly", taDailyDates);
          fetchTenorDaily();
        }
      });
    });
  });

  // Tenor monthly date pickers
  ["ta-mon-date0", "ta-mon-date1"].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("change", fetchTenorMonthly);
  });

  // Tenor daily date pickers
  ["ta-dly-date0", "ta-dly-date1"].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("change", fetchTenorDaily);
  });

  // Balance Analysis sub-tab switch
  document.querySelectorAll('.nim-tab-btn[data-ba-tab]').forEach(function(btn) {
    btn.addEventListener("click", function() {
      baTab = btn.dataset.baTab;
      document.querySelectorAll('.nim-tab-btn[data-ba-tab]').forEach(function(b) {
        b.classList.toggle("active", b.dataset.baTab === baTab);
      });
      updatePageVisibility();
      ensureBalanceDatesLoaded().then(function() {
        if (baTab === "monthly-averages") fetchBalanceMonthly();
        else {
          _setDailyDefaultDates("ba-dly", baDailyDates);
          fetchBalanceDaily();
        }
      });
    });
  });

  // Decomposition Dim ↔ (Second Dec. Dim + Detail Dim) karşılıklı dışlama:
  // decomp'ta seçili boyut diğer iki select'te gizlenir + seçilemez; çakışma
  // olursa o select ilk uygun boyuta kayar.
  function _syncBaSecondDim(prefix) {
    var dsel = document.getElementById(prefix + "-decomp");
    if (!dsel) return;
    var dv = dsel.value;
    [prefix + "-second", prefix + "-decomp2"].forEach(function(id) {
      var ssel = document.getElementById(id);
      if (!ssel) return;
      Array.prototype.forEach.call(ssel.options, function(opt) {
        var hide = (opt.value === dv);
        opt.hidden = hide; opt.disabled = hide;
      });
      if (ssel.value === dv) {
        var alt = Array.prototype.filter.call(ssel.options, function(o) { return o.value !== dv; });
        if (alt.length) ssel.value = alt[0].value;
      }
    });
  }
  // Aktif Decomp / Second Dec. / Detail Dim değerleri (heatmap eksenleri + drill).
  function _baDecomp(prefix)  { var e = document.getElementById(prefix + "-decomp");  return e ? e.value : "SEGMENT"; }
  function _baDecomp2(prefix) { var e = document.getElementById(prefix + "-decomp2"); return e ? e.value : "AUM"; }
  function _baSecond(prefix)  { var e = document.getElementById(prefix + "-second");  return e ? e.value : "PRODUCT"; }
  function _baDimLabel(dim) {
    return ({ PRODUCT: "Product", SUBPRODUCT: "Sub-Product", SEGMENT: "Segment",
              AUM: "AUM", CUSTOMER_TYPE: "Customer Type", TENOR: "Tenor" }[dim] || dim);
  }
  // Heatmap satır/kolon boyutu → /api/hm_product_bar filtre opsiyonu adı.
  function _barKey(dim) {
    return ({ SEGMENT: "barSeg", CUSTOMER_TYPE: "barCustTp", PRODUCT: "barProd",
              SUBPRODUCT: "barSubp", AUM: "barAum" })[dim] || null;
  }

  // Balance monthly date / decomp pickers
  ["ba-mon-date0", "ba-mon-date1", "ba-mon-decomp", "ba-mon-decomp2"].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("change", function() {
      if (id === "ba-mon-decomp") _syncBaSecondDim("ba-mon");
      fetchBalanceMonthly();
    });
  });
  // Balance daily date / decomp pickers
  ["ba-dly-date0", "ba-dly-date1", "ba-dly-decomp", "ba-dly-decomp2"].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("change", function() {
      if (id === "ba-dly-decomp") _syncBaSecondDim("ba-dly");
      fetchBalanceDaily();
    });
  });
  _syncBaSecondDim("ba-mon");
  _syncBaSecondDim("ba-dly");

  // Cost Analysis rate heatmap — yalnız Decomposition / Second Dec. Dim eksenleri.
  // Drill (breakdown) kırılımı ARTIK üstteki TEK "Detailed Dim:" (break-dim) ile
  // belirlenir; kart içindeki ayrı "Detail Dim" (rate-second) kaldırıldı. Decomp veya
  // Second Dec. değişince heatmap yeniden kurulur; _syncBaSecondDim decomp2'yi
  // decomp'tan ayrık tutar (rate-second artık yok → onu sessizce atlar).
  ["ca-mon", "ddd"].forEach(function(pfx) {
    var dsel  = document.getElementById(pfx + "-rate-decomp");
    var d2sel = document.getElementById(pfx + "-rate-decomp2");
    if (dsel) dsel.addEventListener("change", function() {
      _syncBaSecondDim(pfx + "-rate");
      _fetchCaRateHeatmap(pfx);
    });
    if (d2sel) d2sel.addEventListener("change", function() { _fetchCaRateHeatmap(pfx); });
    _syncBaSecondDim(pfx + "-rate");
  });

  // (Ranked Growth grafiği kaldırıldı → sort-toggle wiring'i de gitti.)







  // Port: NII tarafi kaldirildi — acilis, deposit tarafinin ilk sayfasi.
  setPage("cost-analysis");

  // ══════════════════════════════════════════════════════════════════════════
  // NEW PRODUCTION DASHBOARD
  // ══════════════════════════════════════════════════════════════════════════

  var npFreq   = "W";
  var npMeta   = null;
  var npCharts = {};

  function npDestroyChart(id) {
    if (npCharts[id]) { try { npCharts[id].destroy(); } catch(e) {} delete npCharts[id]; }
    // ApexCharts destroy() is not fully synchronous — the old instance can hold
    // a DOM reference and re-render its previous data AFTER we create the new
    // chart (hence "TRY appears" with no renderNpSegAumBubble log).
    // Fix: replace the container element entirely so the old instance has no
    // valid DOM node to render into.
    var el = document.getElementById(id);
    if (el && el.parentNode) {
      var fresh = document.createElement("div");
      fresh.id        = id;
      fresh.style.cssText = el.style.cssText;
      fresh.className = el.className;
      el.parentNode.replaceChild(fresh, el);
    }
  }

  function initNpMeta(cb) {
    if (npMeta) { if (cb) cb(); return; }
    fetch("/api/np/meta").then(function(r) { return r.json(); }).then(function(d) {
      if (!d.ok) return;
      npMeta = d;
      var df = document.getElementById("np-date-from");
      var dt = document.getElementById("np-date-to");
      if (df) df.value = d.date_from;
      if (dt) dt.value = d.date_to;
      _npPopulateSelect("np-seg-select",  d.dimensions.segment);
      _npPopulateSelect("np-camp-select", d.dimensions.campaign);
      if (cb) cb();
    }).catch(function(e) { console.error("NP meta error:", e); });
  }

  function _npPopulateSelect(id, values) {
    var sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = "";
    values.forEach(function(v) {
      var opt = document.createElement("option");
      opt.value = v; opt.textContent = v; opt.selected = true;
      sel.appendChild(opt);
    });
  }

  function _npGetChecked(containerId) {
    var vals = [];
    var el = document.getElementById(containerId);
    if (!el) return vals;
    el.querySelectorAll("input[type=checkbox]:checked").forEach(function(cb) { vals.push(cb.value); });
    return vals;
  }

  function _npGetSelected(selId) {
    var vals = [];
    var sel = document.getElementById(selId);
    if (!sel) return vals;
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].selected) vals.push(sel.options[i].value);
    }
    return vals;
  }

  function _npBuildParams() {
    var p = new URLSearchParams();
    p.set("freq", npFreq);
    var df = document.getElementById("np-date-from");
    var dt = document.getElementById("np-date-to");
    if (df && df.value) p.set("date_from", df.value);
    if (dt && dt.value) p.set("date_to",   dt.value);
    var ccy = _npGetChecked("np-ccy-checks");
    if (ccy.length && ccy.length < 3) p.set("ccy", ccy.join(","));
    var cust = _npGetChecked("np-cust-checks");
    if (cust.length < 2) p.set("cust_tp", cust.join(","));
    var seg  = _npGetSelected("np-seg-select");
    var allSeg  = npMeta ? npMeta.dimensions.segment.length  : 99;
    if (seg.length  && seg.length  < allSeg)  p.set("segment",  seg.join(","));
    var camp = _npGetSelected("np-camp-select");
    var allCamp = npMeta ? npMeta.dimensions.campaign.length : 99;
    if (camp.length && camp.length < allCamp) p.set("campaign", camp.join(","));
    return p;
  }

  // Panel render'ı ayrı fonksiyonda: sayfaya her girişte yeniden çizilir ki
  // outstanding tarafında oluşturulan AUM ayna grupları görünür olsun.
  function _renderNpVpFilterPanel() {
    if (!npVpMeta) return;
    _renderBubFilters("np-vp-bub-filters", npVpMeta, npVpBubState, npVpBubMerges,
      function() { _fetchCurrentNpPage(); });
    // Grafik-üstü VADE (DTM) grouped multi-select → ortak tenor filtresi
    // (jenerik TENOR_GRP dropdown'un yerini alır; ortak panelde render edilir).
    _renderNpRvHmTenorFilter();
  }

  function refreshNpPage() {
    // Ayna grupları görünsün diye panel her sayfa girişinde tazelenir
    // (ilk girişte npVpMeta henüz null → no-op; _initNpVpFilters çizer).
    _renderNpVpFilterPanel();
    fetchNpVolumePricing();
  }

  // ── AUM Rate Chart ─────────────────────────────────────────────────────────
  var _npAumCcy = "TRY";

  function fetchNpAumChart(ccy) {
    if (ccy) _npAumCcy = ccy;
    // Sync with the inline currency select
    var ccySel = document.getElementById("np-aum-ccy-select");
    if (ccySel && !ccy) _npAumCcy = ccySel.value || _npAumCcy;
    var p = new URLSearchParams();
    p.set("ccy", _npAumCcy);
    // Use VP-specific date controls (not the global np-filters bar)
    var d0 = document.getElementById("np-vp-date0");
    var d1 = document.getElementById("np-vp-date1");
    var fr = document.getElementById("np-vp-freq");
    var dc = document.getElementById("np-vp-decomp");
    // Bu grafik HER ZAMAN Date(End)'in bulunduğu YILIN BAŞINDAN başlar (yıl-başı→
    // Date(End) görünümü), Date(Start) ne olursa olsun. Örn. Date(End)=2026-07-01
    // → date_from=2026-01-01. (Yıl prefix'i string slice — UTC tuzağı yok.)
    if (d1 && d1.value) {
      p.set("date_from", d1.value.slice(0, 4) + "-01-01");
      p.set("date_to",   d1.value);
    } else if (d0 && d0.value) {
      p.set("date_from", d0.value);
    }
    if (fr && fr.value) p.set("freq",      fr.value);
    if (dc && dc.value) p.set("decomp",    dc.value);
    // Status feedback
    var statusEl = document.getElementById("np-vp-status");
    if (statusEl) statusEl.textContent = "Loading...";
    fetch("/api/np/aum_rate_chart?" + p.toString() + _npVpBubStateToQuery() + _npRvHmTenorParam())
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (statusEl) statusEl.textContent = "";
        if (!d.ok) { if (statusEl) statusEl.textContent = "Hata: " + d.error; return; }
        renderNpAumChart(d, fr ? fr.value : "W");
      })
      .catch(function(e) {
        if (statusEl) statusEl.textContent = "Hata: " + (e.message || e);
        console.error("AUM chart error:", e);
      });
  }

  function renderNpAumChart(d, freq) {
    var freqLabel = (freq === "D") ? "Daily" : "Weekly";

    // ── Palette: ordinal gradient when dim is ordered, else categorical PRISMA ─
    var AUM_COLORS = {
      "0-5M":     "#5C6478",
      "5-25M":    "#7B8FA0",
      "25-50M":   "#8B8F86",
      "50-100M":  "#B8946A",
      "100-200M": "#D4A574",
      "200M+":    "#E8B988",
    };
    var PRISMA_EXT = ["#5C6478","#7B8FA0","#9BAE8A","#B8946A","#D4A574","#E8B988",
                      "#4A6B8A","#6B8FA8","#8B95A7","#A89380","#C29670","#B8826B"];
    var TENOR_LABELS_LOCAL = {
      "01_1-3":"1-3 D","02_4-31":"4-31 D","03_32-35":"32-35 D",
      "04_36-45":"36-45 D","05_46-60":"46-60 D","06_61-91":"61-91 D",
      "07_92-181":"92-181 D","08_182-273":"182-273 D",
      "09_274-365":"274-365 D","10_366-540":"366-540 D",
      "11_540+":"540+ D","99_DIGER":"Other",
    };
    var CUST_LABELS = { "G": "Retail (G)", "T": "Corporate (T)" };
    var DIM_LABELS  = {
      "AUM_BAND":    "AUM Band",
      "TENOR_GRP":   "Tenor",
      "SUB_SEGMENT": "Segment",
      "CUST_TP":     "Customer Type",
      "RELATED_PC":  "Campaign",
    };
    var decompKey = d.decomp || "AUM_BAND";
    var decompLbl = DIM_LABELS[decompKey] || decompKey;

    function labelFor(band) {
      if (decompKey === "TENOR_GRP") return TENOR_LABELS_LOCAL[band] || band;
      if (decompKey === "CUST_TP")   return CUST_LABELS[band] || band;
      return band;
    }
    function colorFor(band, i) {
      if (decompKey === "AUM_BAND") return AUM_COLORS[band] || PRISMA_EXT[i % PRISMA_EXT.length];
      return PRISMA_EXT[i % PRISMA_EXT.length];
    }

    // Bar serisi (toplam hacim) + band çizgileri
    var series = [{ name: "New Business Volume (TL mn)", type: "bar", data: d.volumes }];
    d.bands.forEach(function(b) {
      series.push({ name: labelFor(b), type: "line", data: d.rates[b] });
    });

    // Rate yaxis min/max: tüm band'lardaki null olmayan değerlerin aralığı +/- küçük padding
    var allRates = [];
    d.bands.forEach(function(b) {
      (d.rates[b] || []).forEach(function(v) { if (v != null) allRates.push(v); });
    });
    var rateMin = allRates.length ? Math.min.apply(null, allRates) : 0;
    var rateMax = allRates.length ? Math.max.apply(null, allRates) : 1;
    var ratePad  = (rateMax - rateMin) * 0.10 || 0.5;   // aralığın %10'u, en az 0.5pp
    var yRateMin = Math.max(0, parseFloat((rateMin - ratePad).toFixed(4)));
    var yRateMax = parseFloat((rateMax + ratePad).toFixed(4));

    var lineColors = d.bands.map(function(b, i) { return colorFor(b, i); });
    // Bar: çok soluk, arka plan rolünde
    var BAR_COLOR = "rgba(139,149,167,0.25)";

    npDestroyChart("np-aum-chart");
    npCharts["np-aum-chart"] = new ApexCharts(
      document.getElementById("np-aum-chart"), {
        series: series,
        chart: {
          type: "line",
          height: 400,
          toolbar: { show: false },
          fontFamily: "'JetBrains Mono','Roboto Mono',monospace",
          animations: { enabled: false },
          background: "transparent",
        },

        // ── Renkler ────────────────────────────────────────────────────────
        colors: [BAR_COLOR].concat(lineColors),

        // ── Çizgi & marker stilleri ────────────────────────────────────────
        stroke: {
          width: series.map(function(s) { return s.type === "bar" ? 0 : 2; }),
          curve: "straight",
          lineCap: "round",
        },
        markers: {
          size: series.map(function(s) { return s.type === "bar" ? 0 : 3; }),
          strokeColors: series.map(function(s) {
            return s.type === "bar" ? "transparent" : "#0A0E1A";
          }),
          strokeWidth: 1,
          fillOpacity: 1,
        },

        // ── Bar stili ──────────────────────────────────────────────────────
        fill: {
          type: series.map(function(s) { return s.type === "bar" ? "solid" : "solid"; }),
          opacity: series.map(function(s) { return s.type === "bar" ? 1 : 1; }),
        },
        plotOptions: { bar: { columnWidth: "80%", borderRadius: 0 } },

        // ── X ekseni ───────────────────────────────────────────────────────
        xaxis: {
          categories: d.dates,
          tickAmount: 6,
          labels: {
            rotate: -30,
            style: {
              fontSize: "11px",
              fontFamily: "'JetBrains Mono','Roboto Mono',monospace",
              colors: "#8B95A7",
            },
          },
          axisBorder: { show: false },
          axisTicks:  { show: false },
        },

        // ── Y eksenleri (dual-axis) ────────────────────────────────────────
        yaxis: [
          {
            seriesName: "New Business Volume (TL mn)",
            opposite: true,
            title: {
              text: "TL MILLION",
              style: {
                fontSize: "11px",
                fontFamily: "'JetBrains Mono','Roboto Mono',monospace",
                fontWeight: 400,
                cssClass: "apexcharts-yaxis-title",
                color: "#8B95A7",
              },
            },
            labels: {
              style: { fontSize: "11px", fontFamily: "'JetBrains Mono',monospace", colors: "#8B95A7" },
              formatter: function(v) { return Math.round(v).toLocaleString("en-US"); },
            },
            axisBorder: { show: false },
            axisTicks:  { show: false },
          }
        ].concat([
          // Tüm line serileri için TEK paylaşılan rate yaxis.
          // - Bar serisi yaxis[0]'a seriesName ile bağlı.
          // - Bu yaxis'ın seriesName'i YOK → eşleşmeyen tüm seriler (yani 6
          //   rate line'ı) buraya düşer ve aynı scale'i paylaşır.
          // Her band için ayrı yaxis tanımlamak hata: her biri kendi autoscale'i
          // ile çizilirdi, tooltip ile görüntü tutarsız olurdu.
          {
            min: yRateMin,
            max: yRateMax,
            title: {
              text: "RATE (%)",
              style: {
                fontSize: "11px",
                fontFamily: "'JetBrains Mono','Roboto Mono',monospace",
                fontWeight: 400,
                color: "#8B95A7",
              },
            },
            labels: {
              style: { fontSize: "11px", fontFamily: "'JetBrains Mono',monospace", colors: "#8B95A7" },
              formatter: function(v) { return v != null ? v.toFixed(2) + "%" : ""; },
            },
            axisBorder: { show: false },
            axisTicks:  { show: false },
          }
        ]),

        // ── Grid ───────────────────────────────────────────────────────────
        grid: {
          borderColor: "#1F2433",
          strokeDashArray: 0,
          xaxis: { lines: { show: false } },
          yaxis: { lines: { show: true } },
          padding: { left: 0, right: 0 },
        },

        // ── Tooltip (PRISMA koyu tema) ─────────────────────────────────────
        tooltip: {
          shared: true,
          intersect: false,
          theme: "dark",
          style: {
            fontSize: "12px",
            fontFamily: "'JetBrains Mono','Roboto Mono',monospace",
          },
          y: {
            formatter: function(val, opts) {
              if (opts.seriesIndex === 0)
                return val != null ? Math.round(val).toLocaleString("tr-TR") + " mn" : "–";
              return val != null ? val.toFixed(2) + "%" : "–";
            },
          },
        },

        // ── Legend ────────────────────────────────────────────────────────
        legend: {
          show: true,
          position: "right",
          horizontalAlign: "left",
          floating: false,
          offsetY: 30,
          fontSize: "11px",
          fontFamily: "'JetBrains Mono','Roboto Mono',monospace",
          fontWeight: 400,
          labels: { colors: "#B6BECD", useSeriesColors: false },
          markers: {
            // custom markers: kare for bar, dash for lines
            width:  series.map(function(s) { return s.type === "bar" ? 12 : 16; }),
            height: series.map(function(s) { return s.type === "bar" ? 12 : 2;  }),
            radius: 1,
          },
          itemMargin: { horizontal: 8, vertical: 6 },
          // Üzerine tıklayınca series toggle edilebilsin
          onItemClick:  { toggleDataSeries: true },
          onItemHover:  { highlightDataSeries: true },
        },

        // ── Başlık ────────────────────────────────────────────────────────
        title: {
          text: _npAumCcy + "  —  New Business Volume & Interest Rate  (Dim by " + decompLbl + ", " + freqLabel + ")",
          align: "left",
          style: {
            fontSize: "12px",
            fontFamily: "'JetBrains Mono','Roboto Mono',monospace",
            fontWeight: 400,
            color: "#B6BECD",
          },
        },

        dataLabels: { enabled: false },
      });
    npCharts["np-aum-chart"].render();
  }

  // ── Volume & Pricing — state & helpers ─────────────────────────────────────
  var npVpBubState  = {};
  var npVpBubMerges = sharedDimMerges;   // gruplama ortak hafızada (NP dim adları ayrık)
  var npVpMeta      = null;   // {PRODUCT:[], CUSTOMER_TYPE:[], AUM:[], SEGMENT:[], CAMPAIGN:[], CURRENCY:[]}
  // Bubble Analysis (Cost muadili) state'i — kaynak fig'ler /api/np/rate_volume_bubble'dan.
  var npVpBubPdims      = {};    // PRODUCT → {dim:value} (client filtre/merge için)
  var npVpBubAggMembers = {};    // merge sonrası bubble → alt ürünler
  var npVpBubFigs       = null;  // {bubble_balance, bubble_rate, ...}
  var _npBubLastKey     = null;  // t0|t1|freq|tenor — dim-filtre değişiminde yeniden ÇEKMEZ
  // TENOR_GRP jenerik dropdown'dan çıkarıldı; yerini grafik-üstü VADE (DTM)
  // grouped multi-select aldı (ortak panelde, _renderNpRvHmTenorFilter → tenor_buckets).
  var NP_VP_DIMS    = ["CCY_CODE","CUST_TP","AUM_BAND","SUB_SEGMENT","RELATED_PC"];
  // Bubble "Dimensions" toggle (Cost muadili): hangi NP boyutlarına göre gruplanacak.
  // Etiketler CCY / CUSTOMER_TYPE / AUM / SEGMENT; SEGMENT = RELATED_PC (kullanıcı
  // isteği; SUB_SEGMENT gruplamadan çıktı, PRODUCT butonu kaldırıldı). SUB_SEGMENT
  // filtre panelinde kalır (heatmap/curve'ü server-side filtreler).
  var NP_VP_GROUP_DIMS = ["CCY_CODE","CUST_TP","AUM_BAND","RELATED_PC"];
  var npVpDims = {};
  NP_VP_GROUP_DIMS.forEach(function(d) { npVpDims[d] = true; });

  function _npVpBubStateToQuery() {
    var parts = [];
    var activeMerges = {};
    NP_VP_DIMS.forEach(function(dim) {
      var dimState  = npVpBubState[dim]  || {};
      var dimMerges = npVpBubMerges[dim] || [];
      var allowed = [];
      var activeForDim = [];
      Object.keys(dimState).forEach(function(v) {
        if (dimState[v] === false) return;
        var grp = dimMerges.find(function(g) { return g.name === v; });
        if (grp) { grp.members.forEach(function(m) { allowed.push(m); });
                   activeForDim.push({ name: grp.name, members: grp.members.slice() }); }
        else allowed.push(v);
      });
      if (allowed.length) {
        var allVals = (npVpMeta || {})[dim] || [];
        if (allVals.some(function(v) { return !allowed.includes(v); }))
          parts.push("filter_" + dim + "=" + encodeURIComponent(allowed.join("|")));
      }
      // RELATED_PC / CUST_TP gruplaması → merges= (heatmap backend collapse eder).
      // AUM_BAND aum_merge ile ayrı taşınır; diğer dim'ler heatmap ekseni değil.
      if (activeForDim.length && (dim === "RELATED_PC" || dim === "CUST_TP"))
        activeMerges[dim] = activeForDim;
    });
    if (Object.keys(activeMerges).length)
      parts.push("merges=" + encodeURIComponent(JSON.stringify(activeMerges)));
    return parts.length ? "&" + parts.join("&") : "";
  }

  // AUM band merge gruplarını heatmap backend'ine taşıyan param.
  // npVpBubMerges["AUM_BAND"] = [{name, members:[...]}, ...]
  // Çıktı: "&aum_merge=g1:m1,m2|g2:m3,m4" (her token encodeURIComponent'li,
  // ayraçlar — : , | — query value'da literal kalır).
  function _npAumMergeParam() {
    var merges = (npVpBubMerges && npVpBubMerges["AUM_BAND"]) || [];
    if (!merges.length) return "";
    var parts = merges
      .filter(function(g) { return g && g.members && g.members.length; })
      .map(function(g) {
        return encodeURIComponent(g.name) + ":"
             + g.members.map(function(m) { return encodeURIComponent(m); }).join(",");
      });
    return parts.length ? "&aum_merge=" + parts.join("|") : "";
  }

  function _npVpBuildParams() {
    var p = new URLSearchParams();
    var d0 = document.getElementById("np-vp-date0");
    var d1 = document.getElementById("np-vp-date1");
    var fr = document.getElementById("np-vp-freq");
    var dc = document.getElementById("np-vp-decomp");
    if (d0 && d0.value) p.set("date_from", d0.value);
    if (d1 && d1.value) p.set("date_to",   d1.value);
    if (fr) p.set("freq", fr.value);
    if (dc) p.set("decomp", dc.value);
    // VADE (DTM) ortak filtresi → tenor_buckets (tenor-mix / campaign de honor eder).
    return p.toString() + _npVpBubStateToQuery() + _npRvHmTenorParam();
  }

  function _initNpVpFilters() {
    if (npVpMeta) return;
    var df = load_np_data_meta_from_cache();  // read from already-loaded npMeta
    var metaForBub = {};
    NP_VP_DIMS.forEach(function(dim) {
      if (npMeta && npMeta.dimensions) {
        var key = { CCY_CODE:"ccy", CUST_TP:"cust_tp", AUM_BAND:"aum_band",
                    SUB_SEGMENT:"segment", RELATED_PC:"campaign", TENOR_GRP:"tenor_grp" }[dim];
        metaForBub[dim] = (npMeta.dimensions[key] || []).slice();
      }
    });
    npVpMeta = metaForBub;
    // Default: CCY_CODE = yalnız TRY seçili (bir kez, ilk init'te). Diğer para
    // birimleri kapalı başlar; kullanıcı sonradan açabilir.
    if (npVpMeta["CCY_CODE"] && !npVpBubState["CCY_CODE"]) {
      npVpBubState["CCY_CODE"] = {};
      npVpMeta["CCY_CODE"].forEach(function(v) {
        npVpBubState["CCY_CODE"][v] = (v === "TRY");
      });
    }
    // Set default date range from npMeta.
    // Date (End) = veri sonu; Date (Start) = Date(End)'in bir önceki Perşembe'si
    // (default 7-günlük hafta penceresi). Veri başlangıcının altına düşmez.
    var d0 = document.getElementById("np-vp-date0");
    var d1 = document.getElementById("np-vp-date1");
    if (d1 && npMeta && npMeta.date_to) {
      // Datepicker ileri tarihleri (veri varsa) SEÇİLEBİLİR bıraksın → max = veri sonu.
      d1.max = npMeta.date_to;
      if (npMeta.date_from) d1.min = npMeta.date_from;
      // DEFAULT: son tarih bugün ya da ileri ise otomatik t-1'e (dün) çek — ama
      // yalnız default; kullanıcı sonra datepicker'dan ileriyi seçebilir.
      var _today = new Date();
      var _y = new Date(_today.getTime() - 864e5);   // -1 gün
      var _yStr = _y.getFullYear() + "-" + String(_y.getMonth() + 1).padStart(2, "0")
                + "-" + String(_y.getDate()).padStart(2, "0");
      var _end = (npMeta.date_to > _yStr) ? _yStr : npMeta.date_to;   // min(veri sonu, dün)
      if (npMeta.date_from && _end < npMeta.date_from) _end = npMeta.date_from;
      d1.value = _end;
    }
    if (d0) {
      if (npMeta && npMeta.date_from) d0.min = npMeta.date_from;
      if (npMeta && npMeta.date_to)   d0.max = npMeta.date_to;
      var _pt = (d1 && d1.value) ? _prevThursday(null, d1.value) : null;
      if (_pt && npMeta && npMeta.date_from && _pt < npMeta.date_from) _pt = npMeta.date_from;
      d0.value = _pt || (npMeta && npMeta.date_from) || d0.value;
    }
    // Ortak panel: filtre/tarih/freq değişince AKTİF New Business sayfasını yenile.
    _renderNpVpFilterPanel();
    ["np-vp-date0","np-vp-date1","np-vp-decomp","np-vp-decomp2","np-vp-freq"].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("change", function() {
        if (id === "np-vp-decomp") _syncNpDecomp2();
        _fetchCurrentNpPage();
      });
    });
    // Bubble "Dimensions" toggle (Cost muadili) — yalnız bubble'ı etkiler (heatmap/
    // curve DEĞİL) → sadece fetchNpBubble. En az bir boyut açık kalmalı.
    document.querySelectorAll(".np-vp-dim-btn").forEach(function(btn) {
      btn.addEventListener("click", function() {
        var dim = btn.getAttribute("data-npdim");
        var active = NP_VP_GROUP_DIMS.filter(function(g) { return npVpDims[g]; });
        if (npVpDims[dim] && active.length === 1) return;   // sonuncuyu kapatma
        npVpDims[dim] = !npVpDims[dim];
        btn.classList.toggle("active", npVpDims[dim]);
        _renderNpVpBubbles();   // CLIENT-side yeniden gruplama (refetch YOK, anlık)
      });
    });
    _syncNpDecomp2();
  }

  // Ortak filtre paneli New Business (tek sayfa: Volume & Pricing) fetch eder.
  function _fetchCurrentNpPage() {
    fetchNpVolumePricing();
  }

  // Decomp / Second Dec. Dim yalnız Volume & Pricing'de anlamlı → diğer
  // sayfalarda gizle.
  function _syncNpSharedFilterUI() {
    ["np-vp-decomp", "np-vp-decomp2"].forEach(function(id) {
      var dec = document.getElementById(id);
      var lbl = dec ? dec.closest("label") : null;
      if (lbl) lbl.style.display = (currentPage === "np-volume-pricing") ? "" : "none";
    });
  }

  function load_np_data_meta_from_cache() { return null; }  // npMeta used directly above

  function fetchNpVolumePricing() {
    if (!npMeta) { initNpMeta(function() { fetchNpVolumePricing(); }); return; }
    _initNpVpFilters();
    _initNpRvHmControls();
    var statusEl = document.getElementById("np-vp-status");
    if (statusEl) statusEl.textContent = "Loading...";
    // Sayfa artık üç grafik: üstte heatmap, altında volume & rate combo, en altta
    // faiz × kümülatif hacim eğrisi.
    fetchNpRvHeatmap();
    fetchNpAumChart();
    fetchNpRvCurve();
    fetchNpBubble();
  }

  // ── Bubble Analysis (New Business) — Cost bubble makinesinin jenerik pipeline'ı
  //    (filtre/merge/min-size/WAvg/tam ekran) prefix "np-vp" ile. Kaynak fig'ler
  //    /api/np/rate_volume_bubble'dan; dim-filtre/merge client-side uygulanır.
  //    Bubble boyutu = Date(End) penceresi hacmi (sizeMode "t1"). Drill YOK (v1).
  function _renderNpVpBubbles() {
    if (!npVpBubFigs || !npVpBubFigs.bubble_balance) return;
    // "Dimensions" toggle → CLIENT-SIDE gruplama: kaynak fig'de 5 boyut var; aktif
    // boyut alt-kümesine göre yeniden aggregate edilir (backend'e bağlı DEĞİL, anlık).
    var activeDims = NP_VP_GROUP_DIMS.filter(function(g) { return npVpDims[g]; });
    if (!activeDims.length) activeDims = NP_VP_GROUP_DIMS.slice();
    _renderBubbles("np-vp-bub-bal", "np-vp-bub-rate",
                   npVpBubFigs.bubble_balance, npVpBubFigs.bubble_rate,
                   npVpBubState, npVpBubMerges, npVpBubPdims, npVpBubAggMembers, "np-vp", activeDims);
    requestAnimationFrame(function() {
      _toggleBubLabels("np-vp-bub-bal", "np-vp-bub-rate", false);
    });
  }

  function fetchNpBubble() {
    var d0 = document.getElementById("np-vp-date0");
    var d1 = document.getElementById("np-vp-date1");
    var fr = document.getElementById("np-vp-freq");
    var t0 = d0 ? d0.value : "", t1 = d1 ? d1.value : "";
    var freq = (fr && fr.value) ? fr.value : "W";
    if (!t0 || !t1) return;
    var tenorQ = _npRvHmTenorParam();   // "&tenor_buckets=..." (dim-filtreler DEĞİL)
    // Kaynak HER ZAMAN 5 boyutun tamamıyla çekilir (ince hücreler). "Dimensions"
    // toggle'ı bu ince hücreleri CLIENT-side yeniden gruplar (_renderNpVpBubbles →
    // activeDims), refetch yok. Böylece toggle backend'e bağlı değil ve anlık.
    var key = t0 + "|" + t1 + "|" + freq + "|" + tenorQ;
    // Kaynak dim-filtre/Dimensions'tan bağımsız: yalnız tarih/freq/tenor değişince
    // yeniden çek. Filtre/merge/min-size/Dimensions değişiminde sadece client re-render.
    if (key === _npBubLastKey && npVpBubFigs) { _renderNpVpBubbles(); return; }
    _npBubLastKey = key;
    var statusEl = document.getElementById("np-vp-bub-status");
    if (statusEl) statusEl.textContent = "Loading...";
    fetch("/api/np/rate_volume_bubble?t0=" + encodeURIComponent(t0)
          + "&t1=" + encodeURIComponent(t1)
          + "&freq=" + encodeURIComponent(freq) + tenorQ)
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (statusEl) statusEl.textContent = "";
        if (!d || !d.ok) {
          if (statusEl) statusEl.textContent = (d && d.error) || "Bubble data could not be loaded.";
          return;
        }
        npVpBubFigs = d;
        npVpBubPdims = d.bubble_product_dims || {};
        npVpBubAggMembers = {};
        // Bubble dim değerleri npMeta ile aynı uzayda; panel boşsa doldur (init sırası).
        if (d.bubble_filter_meta && npVpMeta) {
          NP_VP_DIMS.forEach(function(dim) {
            if (d.bubble_filter_meta[dim] && (!npVpMeta[dim] || !npVpMeta[dim].length)) {
              npVpMeta[dim] = d.bubble_filter_meta[dim].slice();
            }
          });
        }
        _renderNpVpBubbles();
      })
      .catch(function() {
        if (statusEl) statusEl.textContent = "Bubble request error.";
      });
  }

  // ── Faiz × Kümülatif Hacim Eğrisi (Rate–Volume Concentration Curve) ──────────
  // Seçilen Date(Start)/Date(End) pencerelerinde bağlanan mevduatları faize göre
  // artan sıralayıp bakiyeyi kümülatif normalize eder (X=Volume %, Y=faiz).
  var _npRvcAbortCtrl = null;
  function fetchNpRvCurve() {
    if (_npRvcAbortCtrl) _npRvcAbortCtrl.abort();
    _npRvcAbortCtrl = new AbortController();
    var t0El = document.getElementById("np-vp-date0");
    var t1El = document.getElementById("np-vp-date1");
    var statusEl = document.getElementById("np-rvc-status");
    if (statusEl) statusEl.textContent = "Loading...";
    var p = new URLSearchParams();
    if (t0El && t0El.value) p.set("t0", t0El.value);
    if (t1El && t1El.value) p.set("t1", t1El.value);
    p.set("freq", _npSharedFreq());
    // Aynı section filtreleri + AUM merge + vade bucket (heatmap ile birebir).
    var url = "/api/np/rate_volume_curve?" + p.toString()
            + _npVpBubStateToQuery() + _npAumMergeParam() + _npRvHmTenorParam();
    fetch(url, { signal: _npRvcAbortCtrl.signal, cache: "no-store" })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (statusEl) statusEl.textContent = "";
        if (!d.ok) { if (statusEl) statusEl.textContent = "Error: " + d.error; return; }
        renderNpRvCurve(d);
      })
      .catch(function(e) {
        if (e.name === "AbortError") return;
        if (statusEl) statusEl.textContent = "Error: " + (e.message || e);
      });
  }

  function renderNpRvCurve(d) {
    var el = document.getElementById("np-rvc-chart");
    if (!el || typeof Plotly === "undefined") return;
    var series = (d.series || []).filter(function(s) { return s && s.x && s.x.length; });
    var statusEl = document.getElementById("np-rvc-status");
    if (!series.length) {
      // Boş durum: 420px'lik alanı bomboş bırakma — nötr placeholder kutusu.
      Plotly.purge(el);
      el.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;'
        + 'height:100%;min-height:180px;border:1px dashed var(--border-mid,#252B3D);border-radius:3px;'
        + 'color:var(--text-muted,#8B95A7);font-family:\'JetBrains Mono\',monospace;'
        + 'font-size:11px;letter-spacing:0.08em;text-transform:uppercase;">'
        + 'No booked deposits in the selected windows</div>';
      el.__npEmptyPh = true;
      if (statusEl) statusEl.textContent = "";
      return;
    }
    if (el.__npEmptyPh) { el.innerHTML = ""; delete el.__npEmptyPh; }
    var freqLabel = (d.freq === "D") ? "Day" : "Week";
    // t0 = Date(Start), t1 = Date(End). Renkler: Start=denim, End=amber.
    var COLORS = ["#5C6478", "#D4A574"];
    var _fmtDate = function(iso) {
      // "2026-06-23" → "23/06/2026" (UTC tuzağı yok, string slice).
      var pp = String(iso).split("-");
      return pp.length === 3 ? pp[2] + "/" + pp[1] + "/" + pp[0] : iso;
    };
    var _lbl = function(s, i) {
      var role = (i === 0) ? "Date(Start)" : "Date(End)";
      var win = (d.freq === "D") ? _fmtDate(s.window_end)
                                 : _fmtDate(s.window_start) + " – " + _fmtDate(s.window_end);
      var tot = (s.total_mio != null) ? Math.round(s.total_mio).toLocaleString("tr-TR") + " mn TL" : "";
      return role + " · " + freqLabel + " " + win + "  ·  " + tot;
    };
    var traces = series.map(function(s, i) {
      return {
        x: s.x, y: s.y, type: "scatter", mode: "lines",
        name: _lbl(s, i),
        line: { color: COLORS[i % COLORS.length], width: 2, shape: "vh" },
        hovertemplate: "Volume: %{x:.1f}%<br>Faiz: %{y:.2f}%<extra></extra>",
      };
    });
    var lt = (typeof _hmLight === "function") ? _hmLight() : false;
    var ink = lt ? "#2C2A26" : "#C8CDD6";
    var grid = lt ? "rgba(0,0,0,0.08)" : "rgba(255,255,255,0.07)";
    var layout = {
      height: 420,
      margin: { l: 56, r: 20, t: 12, b: 48 },
      paper_bgcolor: "transparent", plot_bgcolor: "transparent",
      font: { family: "'JetBrains Mono',monospace", size: 11, color: ink },
      xaxis: { title: { text: "Volume (%)", font: { size: 11 } }, range: [0, 100],
               gridcolor: grid, zeroline: false, ticksuffix: "" },
      yaxis: { title: { text: "Interest Rate (%)", font: { size: 11 } },
               gridcolor: grid, zeroline: false },
      legend: { orientation: "h", x: 0, y: -0.16, font: { size: 11 },
                bgcolor: "rgba(0,0,0,0)" },
      hovermode: "closest",
    };
    Plotly.react(el, traces, layout,
                 { displayModeBar: false, responsive: true });
  }

  // ── Rate × Volume Heatmap (Segment × AUM) ───────────────────────────────────
  var _npRvHmData        = null;   // last API response
  var _npRvHmMode        = "delta"; // "delta" | "level"
  // Freq artık ortak Data Type select'inden okunur → _npSharedFreq().
  var _npRvHmControlsOk  = false;
  var _npRvHmAbortCtrl   = null;

  // Vade (tenor) bucket multi-select. Etiketler backend _TENOR_BUCKET_MAP ile
  // birebir aynı; backend bunları TENOR_GRP değerlerine map'ler. Default: yalnız
  // "32-45" seçili. Hepsi seçilirse filtre gönderilmez → tüm mevduatlar.
  var _NP_TENOR_BUCKETS = ["1-3","4-31","32-45","46-91","92-181","182-273","274-365","366+"];
  var _npRvHmTenorSel   = {};
  _NP_TENOR_BUCKETS.forEach(function(b) { _npRvHmTenorSel[b] = (b === "32-45"); });

  function _npRvHmTenorParam() {
    var sel = _NP_TENOR_BUCKETS.filter(function(b) { return _npRvHmTenorSel[b]; });
    if (sel.length === _NP_TENOR_BUCKETS.length) return "";          // hepsi → filtre yok
    if (sel.length === 0) return "&tenor_buckets=__none__";          // hiçbiri → boş sonuç
    return "&tenor_buckets=" + sel.map(encodeURIComponent).join("|");
  }

  // Heatmap Y (decomp) + X (decomp2) ekseni boyutları → drill/hover'ın satır ve
  // kolon değerlerini doğru boyuta filtrelemesi için.
  function _npDecompParam() {
    var e  = document.getElementById("np-vp-decomp");
    var e2 = document.getElementById("np-vp-decomp2");
    return ((e  && e.value)  ? "&decomp="  + encodeURIComponent(e.value)  : "")
         + ((e2 && e2.value) ? "&decomp2=" + encodeURIComponent(e2.value) : "");
  }

  // Decomp ↔ Second Dec. Dim karşılıklı dışlama (Balance/Cost sayfalarıyla aynı
  // davranış): decomp'ta seçili boyut decomp2'de gizlenir; çakışırsa kayar.
  function _syncNpDecomp2() {
    var dsel = document.getElementById("np-vp-decomp");
    var ssel = document.getElementById("np-vp-decomp2");
    if (!dsel || !ssel) return;
    var dv = dsel.value;
    Array.prototype.forEach.call(ssel.options, function(opt) {
      var hide = (opt.value === dv);
      opt.hidden = hide; opt.disabled = hide;
    });
    if (ssel.value === dv) {
      var alt = Array.prototype.filter.call(ssel.options, function(o) { return o.value !== dv; });
      if (alt.length) ssel.value = alt[0].value;
    }
  }

  // Üstteki bub-filter dropdown'larıyla aynı görünümde çok-seçimli vade filtresi.
  function _renderNpRvHmTenorFilter() {
    var mount = document.getElementById("np-rvhm-tenor-wrap");
    if (!mount) return;
    mount.innerHTML = "";

    var wrap = document.createElement("div");
    wrap.className = "bub-filter-dd";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "bub-filter-dd-btn";

    function updateBtnLabel() {
      var n = _NP_TENOR_BUCKETS.filter(function(b) { return _npRvHmTenorSel[b]; }).length;
      var sub;
      if (n === _NP_TENOR_BUCKETS.length) sub = "All (" + n + ")";
      else if (n === 0)                   sub = "None";
      else                                sub = n + " / " + _NP_TENOR_BUCKETS.length;
      btn.innerHTML = '<span><b>VADE (DTM):</b> ' + sub + '</span><span class="caret">▾</span>';
    }
    updateBtnLabel();

    var popup = document.createElement("div");
    popup.className = "bub-filter-dd-popup hidden";

    var actions = document.createElement("div");
    actions.className = "bub-filter-dd-actions";
    var allLink = document.createElement("a");
    allLink.textContent = "All";
    allLink.addEventListener("click", function(ev) {
      ev.preventDefault();
      _NP_TENOR_BUCKETS.forEach(function(b) { _npRvHmTenorSel[b] = true; });
      _renderNpRvHmTenorFilter(); _fetchCurrentNpPage();
    });
    var noneLink = document.createElement("a");
    noneLink.textContent = "None";
    noneLink.addEventListener("click", function(ev) {
      ev.preventDefault();
      _NP_TENOR_BUCKETS.forEach(function(b) { _npRvHmTenorSel[b] = false; });
      _renderNpRvHmTenorFilter(); _fetchCurrentNpPage();
    });
    actions.appendChild(allLink);
    actions.appendChild(document.createTextNode(" | "));
    actions.appendChild(noneLink);
    popup.appendChild(actions);

    _NP_TENOR_BUCKETS.forEach(function(b) {
      var lblEl = document.createElement("label");
      lblEl.className = "bub-filter-dd-opt";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!_npRvHmTenorSel[b];
      cb.addEventListener("change", function() {
        _npRvHmTenorSel[b] = cb.checked;
        updateBtnLabel();
        _fetchCurrentNpPage();
      });
      lblEl.appendChild(cb);
      lblEl.appendChild(document.createTextNode(" " + b + " days"));
      popup.appendChild(lblEl);
    });

    btn.addEventListener("click", function(ev) {
      ev.stopPropagation();
      document.querySelectorAll(".bub-filter-dd-popup").forEach(function(p) {
        if (p !== popup) p.classList.add("hidden");
      });
      popup.classList.toggle("hidden");
    });
    popup.addEventListener("click", function(ev) { ev.stopPropagation(); });

    wrap.appendChild(btn);
    wrap.appendChild(popup);
    mount.appendChild(wrap);
  }

  // Ortak Data Type (Daily/Weekly) select'inden freq okur — heatmap'in kendi
  // freq toggle'ı kaldırıldı; tarih (T1/T2) de ortak np-vp-date0/date1'den gelir.
  function _npSharedFreq() {
    var e = document.getElementById("np-vp-freq");
    var v = e && e.value ? e.value.toUpperCase() : "W";
    return (v === "D" || v === "W") ? v : "W";
  }

  function _initNpRvHmControls() {
    if (_npRvHmControlsOk) return;

    // T1/T2 tarih ve Data Type (freq) artık ortak filtrelerden okunur; VADE (DTM)
    // selektörü ortak panelde (_initNpVpFilters) render edilir. Burada yalnız
    // grafiğe özel Rate Δ / Rate Level modu bağlanır.
    var mDelta = document.getElementById("np-rvhm-mode-delta");
    var mLevel = document.getElementById("np-rvhm-mode-level");
    function _setMode(m) {
      _npRvHmMode = m;
      if (mDelta) { mDelta.style.background = m === "delta" ? "var(--accent,#D4A574)" : "transparent";
                    mDelta.style.color      = m === "delta" ? "#0A0E1A" : "var(--text-muted,#8B95A7)"; }
      if (mLevel) { mLevel.style.background = m === "level" ? "var(--accent,#D4A574)" : "transparent";
                    mLevel.style.color      = m === "level" ? "#0A0E1A" : "var(--text-muted,#8B95A7)"; }
    }
    if (mDelta) mDelta.onclick = function() { _setMode("delta"); if (_npRvHmData) renderNpRvHeatmap(_npRvHmData); };
    if (mLevel) mLevel.onclick = function() { _setMode("level"); if (_npRvHmData) renderNpRvHeatmap(_npRvHmData); };

    _npRvHmControlsOk = true;
  }

  function fetchNpRvHeatmap() {
    _initNpRvHmControls();
    if (_npRvHmAbortCtrl) _npRvHmAbortCtrl.abort();
    _npRvHmAbortCtrl = new AbortController();

    var t0El = document.getElementById("np-vp-date0");
    var t1El = document.getElementById("np-vp-date1");
    var statusEl = document.getElementById("np-rvhm-status");
    if (statusEl) statusEl.textContent = "Loading...";

    var p = new URLSearchParams();
    if (t0El && t0El.value) p.set("t0", t0El.value);
    if (t1El && t1El.value) p.set("t1", t1El.value);
    p.set("freq", _npSharedFreq());
    // Decomp. Dim = heatmap Y ekseni, Second Dec. Dim = X ekseni boyutu.
    var _dc = document.getElementById("np-vp-decomp");
    if (_dc && _dc.value) p.set("decomp", _dc.value);
    var _dc2 = document.getElementById("np-vp-decomp2");
    if (_dc2 && _dc2.value) p.set("decomp2", _dc2.value);

    // Append section-level filter state (AUM + segment + diğer dim filtreleri)
    var fq = _npVpBubStateToQuery();
    // AUM band gruplama (merge) → heatmap kolonları da gruplansın diye backend'e
    // mapping gönderilir. Format: name:m1,m2|name2:m3,m4 (token'lar encode'lu).
    var aumMerge = _npAumMergeParam();
    // Vade (tenor) bucket seçimi → backend TENOR_GRP'lere map'leyip filtreler.
    var tenorQ = _npRvHmTenorParam();
    var url = "/api/np/rate_volume_heatmap?" + p.toString() + fq + aumMerge + tenorQ;

    fetch(url, { signal: _npRvHmAbortCtrl.signal, cache: "no-store" })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (statusEl) statusEl.textContent = "";
        if (!d.ok) { if (statusEl) statusEl.textContent = "Error: " + d.error; return; }
        _npRvHmData = d;
        renderNpRvHeatmap(d);
        _npPrefetchDrillWindow();   // çift-tık detayını arka planda hazırla
      })
      .catch(function(e) {
        if (e.name === "AbortError") return;
        if (statusEl) statusEl.textContent = "Error: " + (e.message || e);
      });
  }

  // Heatmap açılınca o [t0,t1] penceresinin drill-down detayını arka planda
  // cache'e aldır (fire-and-forget) → kullanıcı çift-tıkladığında bekleme olmaz.
  var _npPrefetchedKey = null;
  function _npPrefetchDrillWindow() {
    var t0 = document.getElementById("np-vp-date0");
    var t1 = document.getElementById("np-vp-date1");
    if (!t0 || !t1 || !t0.value || !t1.value) return;
    var key = t0.value + "|" + t1.value;
    if (key === _npPrefetchedKey) return;   // aynı pencere zaten prefetch edildi
    _npPrefetchedKey = key;
    fetch("/api/np/detail_prewarm?t0=" + encodeURIComponent(t0.value)
          + "&t1=" + encodeURIComponent(t1.value), { cache: "no-store" })
      .catch(function() { _npPrefetchedKey = null; });   // hata → sonraki denemede tekrar
  }

  // ── Color helpers ─────────────────────────────────────────────────────────

  // Heatmap light mode? (body.light-mode) — hücre renkleri buna göre seçilir.
  function _hmLight() { return document.body.classList.contains("light-mode"); }

  function _rvHmDivColor(bps, maxAbsBps) {
    // Diverging: negative=blue-ish, zero=nötr, positive=amber.
    var lt = _hmLight();
    if (bps == null) return lt ? { bg: "rgba(0,0,0,0.02)", fg: "#8A8680" }
                               : { bg: "#0D111C", fg: "#4A5260" };
    var norm = maxAbsBps > 0 ? Math.min(1, Math.abs(bps) / maxAbsBps) : 0;
    if (lt) {
      // Light: krem üzerine amber/denim WASH + koyu grafit yazı (okunur kalır).
      var a = (0.10 + 0.48 * norm).toFixed(2);
      if (bps > 0) return { bg: "rgba(184,134,11," + a + ")", fg: "#2C2A26" };
      return { bg: "rgba(45,75,110," + a + ")", fg: "#2C2A26" };
    }
    var alpha = 0.12 + 0.68 * norm;
    if (bps > 0) {
      // amber: var(--accent) → rgba
      var r = Math.round(80  + 132 * norm);
      var g = Math.round(40  + 125 * norm);
      var b = Math.round(10  + 106 * norm);
      return { bg: "rgba(" + r + "," + g + "," + b + "," + alpha.toFixed(2) + ")", fg: "#F0E6D5" };
    } else {
      // blue-slate
      var r2 = Math.round(20  + 50 * norm);
      var g2 = Math.round(30  + 90 * norm);
      var b2 = Math.round(60 + 130 * norm);
      return { bg: "rgba(" + r2 + "," + g2 + "," + b2 + "," + alpha.toFixed(2) + ")", fg: "#C8D8E8" };
    }
  }

  // DELTA modu hücre yazısı. Tek-gün karşılaştırmasında yeni iş (flow) her
  // mikro-hücreye düşmediğinden, hücrelerin çoğunda T1 veya T2'den biri eksik
  // olur → bps Δ hesaplanamaz. ESKİDEN bu hücreler "—" gösteriliyordu ve veri
  // varmış gibi görünmüyordu. Artık tek-taraflı hücrelerde mevcut dönemin
  // simple oranını küçük bir T1/T2 işaretçisiyle gösteriyoruz (italik = tek
  // dönem, karşılaştırma yok).
  function _rvHmDeltaText(c) {
    var bps = c.rate_delta_bps;
    if (bps != null) {
      return (bps > 0 ? "+" : "") + bps.toFixed(0) + " bps";
    }
    // T2-only: bu dönem yeni iş çıktı, T1 baseline yok.
    if (c.t1_simple != null) {
      return '<span style="font-style:italic;opacity:0.9;">'
           + c.t1_simple.toFixed(1) + '%'
           + '<span style="font-size:10px;vertical-align:super;color:#9BAE8A;'
           + 'margin-left:1px;">T2</span></span>';
    }
    // T1-only: T1'de vardı, T2'de yeni iş yok.
    if (c.t0_simple != null) {
      return '<span style="font-style:italic;opacity:0.6;">'
           + c.t0_simple.toFixed(1) + '%'
           + '<span style="font-size:10px;vertical-align:super;color:#B8826B;'
           + 'margin-left:1px;">T1</span></span>';
    }
    return "—";
  }

  function _rvHmSeqColor(vol, maxVol) {
    // Sequential amber: low=near-dark, high=bright amber var(--accent)
    var lt = _hmLight();
    if (vol == null || vol <= 0 || maxVol <= 0) return lt ? { bg: "rgba(0,0,0,0.02)", fg: "#8A8680" }
                                                          : { bg: "#0D111C", fg: "#4A5260" };
    var norm = Math.min(1, vol / maxVol);
    if (lt) {
      // Light: krem üzerine amber WASH + koyu grafit yazı.
      var al = (0.10 + 0.52 * norm).toFixed(2);
      return { bg: "rgba(184,134,11," + al + ")", fg: "#2C2A26" };
    }
    var alpha = 0.12 + 0.72 * norm;
    var r = Math.round(50  + 162 * norm);
    var g = Math.round(25  + 140 * norm);
    var b = Math.round(5   + 111 * norm);
    return { bg: "rgba(" + r + "," + g + "," + b + "," + alpha.toFixed(2) + ")", fg: norm > 0.5 ? "#F0E6D5" : "#B6BECD" };
  }

  // ── Tooltip builder ───────────────────────────────────────────────────────

  function _rvHmTooltip(cell, rowLabel, colLabel, isDelta) {
    if (!cell) return "";
    var fmtPct = function(v) {
      if (v == null) return "—";
      return parseFloat(v).toFixed(2) + "%";
    };
    var fmtVol = function(v) {
      if (v == null) return "—";
      return parseFloat(v).toLocaleString("en-US", { minimumFractionDigits:1, maximumFractionDigits:1 }) + " mn";
    };
    var fmtBps = function(v) {
      if (v == null) return "—";
      var s = v > 0 ? "+" : "";
      return s + parseFloat(v).toFixed(0) + " bps";
    };
    var fmtDay = function(v) {
      if (v == null) return "—";
      return Math.round(parseFloat(v)) + " days";
    };
    var lbl = function(t) {
      return '<span style="color:var(--text-muted);letter-spacing:0.12em;text-transform:uppercase;font-size:10px;">' + t + '</span>';
    };
    var val = function(v, accent) {
      return '<b style="color:' + (accent ? "#D4A574" : "#E8ECF1") + ';">' + v + '</b>';
    };
    var row = function(l, v, accent) {
      return '<div style="display:flex;justify-content:space-between;gap:16px;margin:2px 0;">'
           + lbl(l) + val(v, accent) + '</div>';
    };
    var secTitle = function(t) {
      return '<div style="font-size:10px;font-weight:500;letter-spacing:0.2em;'
           + 'text-transform:uppercase;color:var(--accent);opacity:0.7;margin:6px 0 4px;">'
           + t + '</div>';
    };
    // Rengin temsil ettiği değeri belirgin bir banner ile göster:
    //   DELTA modu → renk = Balance Δ; LEVEL modu → renk = T2 New Volume.
    var colorLabel = isDelta ? "Balance Δ" : "T2 Volume";
    var colorVal   = isDelta ? fmtVol(cell.bal_delta) : fmtVol(cell.t1_np_vol);
    var colorBanner = '<div style="display:flex;justify-content:space-between;'
         + 'align-items:center;gap:16px;margin:4px 0 2px;padding:4px 7px;border-radius:2px;'
         + 'background:rgba(212,165,116,0.12);border-left:2px solid var(--accent);">'
         + '<span style="color:var(--accent);letter-spacing:0.1em;text-transform:uppercase;'
         +              'font-size:10px;">&#9635; Renk &middot; ' + colorLabel + '</span>'
         + '<b style="color:#F0E6D5;font-size:12px;">' + colorVal + '</b></div>';

    // Gerçekten veri yok (ne new-prod ne outstanding) → kısa not.
    if (cell.t0_compound == null && cell.t1_compound == null
        && cell.t0_os == null && cell.t1_os == null) {
      return '<div style="font-family:\'JetBrains Mono\',monospace;font-size:11px;">'
           + '<div style="font-weight:600;color:var(--text-primary);margin-bottom:6px;font-size:12px;">'
           + rowLabel + ' × ' + colLabel + '</div>'
           + '<div style="color:var(--text-muted);">No data in this period</div></div>';
    }
    // T1/T2/Δ MATRİS: satırlar T1/T2/Δ, kolonlar metrikler. Karşılaştırma için
    // en okunaklı düzen (göz dikeyde T1→T2→Δ tarar).
    var dBps = function(a, b) {   // iki yüzde arası bps farkı
      return (a != null && b != null) ? fmtBps((a - b) * 100) : "—";
    };
    var dDay = function(a, b) {
      if (a == null || b == null) return "—";
      var x = Math.round(a - b); return (x > 0 ? "+" : "") + x + " g";
    };
    var dVol = function(a, b) {
      if (a == null || b == null) return "—";
      var x = (a - b); return (x > 0 ? "+" : "") + parseFloat(x).toFixed(1) + " mn";
    };
    var COLS = [
      { h: "Simple",    t0: fmtPct(cell.t0_simple),   t1: fmtPct(cell.t1_simple),   d: dBps(cell.t1_simple, cell.t0_simple) },
      { h: "Compound",  t0: fmtPct(cell.t0_compound), t1: fmtPct(cell.t1_compound), d: dBps(cell.t1_compound, cell.t0_compound) },
      { h: "Tenor",     t0: fmtDay(cell.t0_tenor),    t1: fmtDay(cell.t1_tenor),    d: dDay(cell.t1_tenor, cell.t0_tenor) },
      { h: "OS Bakiye", t0: fmtVol(cell.t0_os),       t1: fmtVol(cell.t1_os),       d: dVol(cell.t1_os, cell.t0_os) },
      { h: "OS Rate",   t0: fmtPct(cell.t0_os_rate),  t1: fmtPct(cell.t1_os_rate),  d: dBps(cell.t1_os_rate, cell.t0_os_rate) },
      { h: "Booked",  t0: fmtVol(cell.t0_np_vol),   t1: fmtVol(cell.t1_np_vol),   d: dVol(cell.t1_np_vol, cell.t0_np_vol) },
    ];
    var th = "padding:4px 12px;text-align:right;font-size:11px;letter-spacing:0.1em;"
           + "text-transform:uppercase;color:var(--text-muted);font-weight:500;white-space:nowrap;";
    var rh = "padding:4px 12px;text-align:left;font-size:10px;letter-spacing:0.08em;"
           + "color:var(--text-muted);font-weight:500;white-space:nowrap;";
    var td = "padding:4px 12px;text-align:right;font-size:12px;color:var(--text-primary);white-space:nowrap;";
    var tdD = td + "color:var(--accent);";
    var head = '<tr><th style="' + rh + '"></th>'
             + COLS.map(function(c) { return '<th style="' + th + '">' + c.h + '</th>'; }).join("")
             + '</tr>';
    var rowT = function(label, key, isD) {
      return '<tr><td style="' + rh + (isD ? 'color:var(--accent);' : '') + '">' + label + '</td>'
           + COLS.map(function(c) { return '<td style="' + (isD ? tdD : td) + '">' + c[key] + '</td>'; }).join("")
           + '</tr>';
    };
    return '<div style="font-family:\'JetBrains Mono\',monospace;">'
         + '<div style="display:flex;align-items:center;gap:14px;margin-bottom:6px;">'
         +   '<span style="font-weight:600;color:var(--text-primary);font-size:13px;">'
         +     rowLabel + ' × ' + colLabel + '</span>'
         +   '<span style="font-size:10px;color:#6B7689;">double-click → new-prod time series</span>'
         +   '<span style="margin-left:auto;">' + colorBanner.replace('margin:4px 0 2px;', 'margin:0;') + '</span>'
         + '</div>'
         + '<table style="border-collapse:collapse;"><thead>' + head + '</thead><tbody>'
         + rowT("T1", "t0", false) + rowT("T2", "t1", false) + rowT("Δ", "d", true)
         + '</tbody></table></div>';
  }

  // ── Main render ───────────────────────────────────────────────────────────

  function renderNpRvHeatmap(d) {
    var wrap = document.getElementById("np-rvhm-wrap");
    var tip  = document.getElementById("np-rvhm-tip");
    if (!wrap) return;

    var rows      = d.rows || [];
    var cols      = d.cols || [];
    var cells     = d.cells || {};
    var rowTotals = d.row_totals || {};
    var colTotals = d.col_totals || {};
    var grand     = d.grand_total || {};
    var isDelta   = _npRvHmMode !== "level";
    // Y (Decomp) + X (Second Dec. Dim) ekseni boyutları: başlık etiketleri +
    // görünüm map'leri (ör. G→Gerçek).
    var rowLabel  = d.row_label || "Segment";
    var rowDisp   = d.row_display || {};
    var colLabel  = d.col_label || "AUM";
    var colDisp   = d.col_display || {};
    var _rd = function(v) { return rowDisp[v] || v; };   // satır değeri → görünüm etiketi
    var _cd = function(v) { return colDisp[v] || v; };   // kolon değeri → görünüm etiketi

    // Compute color scale bounds.
    //   DELTA modu: hücre rengi BALANCE DELTA diverging (spec). Renk ölçeği
    //     SADECE veri hücreleri üzerinden — total satır/sütunu dahil etmiyoruz
    //     (total'lar büyük balance delta'larıyla ölçeği ezerdi).
    //   LEVEL modu: hücre rengi T2 NEW VOLUME sequential.
    var maxAbsBal = 0, maxVol = 0;
    rows.forEach(function(pc) {
      cols.forEach(function(auc) {
        var c = cells[pc + "|" + auc];
        if (!c) return;
        if (c.bal_delta != null) maxAbsBal = Math.max(maxAbsBal, Math.abs(c.bal_delta));
        if (c.t1_np_vol != null) maxVol = Math.max(maxVol, c.t1_np_vol);
      });
    });

    var _lt = _hmLight();
    var _gridB = _lt ? "#E2DAC8" : "#131824";   // hücre ızgara çizgisi (light: soft krem)
    var cellStyle = "padding:6px 10px;text-align:center;font-size:11px;"
                  + "font-family:'JetBrains Mono',monospace;cursor:default;"
                  + "border:1px solid " + _gridB + ";white-space:nowrap;";
    var hdrStyle  = "padding:6px 10px;font-size:10px;font-family:'JetBrains Mono',monospace;"
                  + "letter-spacing:0.10em;text-transform:uppercase;font-weight:500;"
                  + "color:var(--text-muted);background:var(--bg-base);border:1px solid " + _gridB + ";white-space:nowrap;";

    function _cellHtml(c, rowL, colL) {
      // Hücre "gerçekten boş" sadece HEM new-prod oran/hacim HEM outstanding
      // bakiye yoksa. Outstanding-only hücre (yeni iş yok ama stok var) renkli
      // gösterilir (balance Δ) — bu kanal/AUM'da yeni iş olmasa da stok değişimi
      // anlamlıdır.
      var _noNp = !c || (c.t0_compound == null && c.t1_compound == null);
      var _noOs = !c || (c.t0_os == null && c.t1_os == null);
      if (_noNp && _noOs) {
        // Boş hücrede de satır/sütun başlığı parlasın (rk/ck + hover handler'ları
        // her zaman eklenir; data-tip yalnız hücre verisi varsa).
        var nAttr = ' data-rk="' + String(rowL).replace(/"/g, "&quot;") + '"'
                  + ' data-ck="' + String(colL).replace(/"/g, "&quot;") + '"'
                  + ' onmouseenter="_rvHmShowTip(this,event)" onmouseleave="_rvHmHideTip()"';
        if (c) nAttr += ' data-tip="' + _rvHmTooltip(c, rowL, colL, isDelta).replace(/"/g, "&quot;") + '"';
        return '<td style="' + cellStyle + 'background:var(--bg-hover);color:var(--text-faint);"' + nAttr + '>—</td>';
      }
      var col, txt;
      if (isDelta) {
        // DELTA modu: yazı = SIMPLE rate Δ (bps, weighted-tenor eşdeğeri);
        // renk = BALANCE DELTA. (Hücre-içi oran hesabı compound, delta simple.)
        col = _rvHmDivColor(c.bal_delta, maxAbsBal);
        txt = _rvHmDeltaText(c);
      } else {
        // LEVEL modu: yazı = reverse-converted simple rate (%); renk = T2 vol.
        col = _rvHmSeqColor(c.t1_np_vol, maxVol);
        txt = c.t1_simple == null ? "—" : c.t1_simple.toFixed(2) + "%";
      }
      var tipHtml = _rvHmTooltip(c, rowL, colL, isDelta).replace(/"/g, "&quot;");
      // data-ch/data-au: çift-tık combo (new-prod zaman serisi) için kanal+AUM.
      return '<td style="' + cellStyle + 'background:' + col.bg + ';color:' + col.fg + ';cursor:pointer;"'
           + ' data-tip="' + tipHtml + '"'
           + ' data-ch="' + String(rowL).replace(/"/g, "&quot;") + '"'
           + ' data-au="' + String(colL).replace(/"/g, "&quot;") + '"'
           + ' data-rk="' + String(rowL).replace(/"/g, "&quot;") + '"'
           + ' data-ck="' + String(colL).replace(/"/g, "&quot;") + '"'
           + ' onmouseenter="_rvHmShowTip(this,event)"'
           + ' onmouseleave="_rvHmHideTip()"'
           + ' ondblclick="_rvHmOpenDrill(this)">'
           + txt + '</td>';
    }

    function _totalCellHtml(c, rowL, colL) {
      // _cellHtml ile aynı: total ancak HEM new-prod HEM outstanding yoksa boş.
      // Outstanding-only total'lar (yeni iş yok ama stok var) gösterilir.
      var _noNp = !c || (c.t0_compound == null && c.t1_compound == null);
      var _noOs = !c || (c.t0_os == null && c.t1_os == null);
      if (_noNp && _noOs) {
        return '<td style="' + cellStyle + 'background:var(--bg-input);color:var(--text-faint);"'
             + ' data-rk="' + String(rowL).replace(/"/g, "&quot;") + '"'
             + ' data-ck="' + String(colL).replace(/"/g, "&quot;") + '"'
             + ' onmouseenter="_rvHmShowTip(this,event)" onmouseleave="_rvHmHideTip()">—</td>';
      }
      var txt, fgColor;
      if (isDelta) {
        var bps = c.rate_delta_bps;
        if (_lt) fgColor = bps == null ? "#6B6862" : (bps > 0 ? "#8A5A00" : "#2D4B6E");
        else     fgColor = bps == null ? "#8B95A7" : (bps > 0 ? "#D4A574" : "#8FA8C8");
        txt = _rvHmDeltaText(c);   // rate Δ yoksa outstanding tek-taraflı fallback
      } else {
        fgColor = _lt ? "#8A5A00" : "#D4A574";
        txt = c.t1_simple == null ? "—" : c.t1_simple.toFixed(2) + "%";
      }
      // Total satır/sütun emphasis zemini: dark = near-black; light = warm koyu krem.
      var totalBg = _lt ? "#EFE6D2" : "#111520";
      var tipHtml = _rvHmTooltip(c, rowL, colL, isDelta).replace(/"/g, "&quot;");
      // Total satır/sütun için data-ch/data-au: "All"→__ALL__ (o boyutta filtre yok)
      // → hover combo total'larda da çalışır (row=kanal/tüm AUM, col=tüm kanal/AUM…).
      var tch = (rowL === "All") ? "__ALL__" : rowL;
      var tau = (colL === "All AUM") ? "__ALL__" : colL;
      return '<td style="' + cellStyle + 'background:' + totalBg + ';color:' + fgColor + ';font-weight:600;cursor:pointer;"'
           + ' data-tip="' + tipHtml + '"'
           + ' data-ch="' + String(tch).replace(/"/g, "&quot;") + '"'
           + ' data-au="' + String(tau).replace(/"/g, "&quot;") + '"'
           + ' data-rk="' + String(rowL).replace(/"/g, "&quot;") + '"'
           + ' data-ck="' + String(colL).replace(/"/g, "&quot;") + '"'
           + ' onmouseenter="_rvHmShowTip(this,event)"'
           + ' onmouseleave="_rvHmHideTip()"'
           + ' ondblclick="_rvHmOpenDrill(this)">'
           + txt + '</td>';
    }

    var t0Lbl = (d.t0 || "T1").substring(0, 10);
    var t1Lbl = (d.t1 || "T2").substring(0, 10);
    var freqLbl = d.freq === "W" ? "7-day window" : "single day";

    var html = '<table style="border-collapse:collapse;width:max-content;min-width:100%;">';

    // Sub-header row: T1 and T2 date labels
    html += '<thead>';
    html += '<tr>';
    html += '<th colspan="2" style="' + hdrStyle + 'text-align:left;">'
          + rowLabel + ' / ' + colLabel + '</th>';
    html += '<th colspan="' + cols.length + '" style="' + hdrStyle + 'text-align:center;border-bottom:2px solid var(--border-line);">'
          + t1Lbl + ' vs ' + t0Lbl + ' (' + freqLbl + ')</th>';
    html += '<th style="' + hdrStyle + 'text-align:center;">Total</th>';
    html += '</tr>';

    // Column headers
    html += '<tr>';
    html += '<th style="' + hdrStyle + '">' + rowLabel + '</th>';
    html += '<th style="' + hdrStyle + '"></th>';
    cols.forEach(function(auc) {
      html += '<th style="' + hdrStyle + 'text-align:center;" data-col-hdr="'
            + String(auc).replace(/"/g, "&quot;") + '">' + _cd(auc) + '</th>';
    });
    html += '<th style="' + hdrStyle + 'text-align:center;color:var(--accent);" data-col-hdr="All AUM">TOTAL</th>';
    html += '</tr>';
    html += '</thead><tbody>';

    // Data rows
    rows.forEach(function(pc) {
      html += '<tr>';
      html += '<td style="' + hdrStyle + 'background:var(--bg-input);" data-row-hdr="'
            + String(pc).replace(/"/g, "&quot;") + '">' + _rd(pc) + '</td>';
      html += '<td style="' + hdrStyle + 'background:var(--bg-input);color:var(--text-faint);font-size:10px;">'
            + (isDelta ? "bps" : "%") + '</td>';
      cols.forEach(function(auc) {
        var c = cells[pc + "|" + auc];
        html += _cellHtml(c, pc, auc);
      });
      html += _totalCellHtml(rowTotals[pc], pc, "All AUM");
      html += '</tr>';
    });

    // Totals row
    html += '<tr style="border-top:2px solid var(--border-line);">';
    html += '<td style="' + hdrStyle + 'color:var(--accent);background:var(--bg-base);" data-row-hdr="All">TOTAL</td>';
    html += '<td style="' + hdrStyle + 'background:var(--bg-base);color:var(--text-faint);font-size:10px;">'
          + (isDelta ? "bps" : "%") + '</td>';
    cols.forEach(function(auc) {
      html += _totalCellHtml(colTotals[auc], "All", auc);
    });
    html += _totalCellHtml(grand, "All", "All AUM");
    html += '</tr>';

    html += '</tbody></table>';

    // Caption: faiz bazı + renk kodlaması açıklaması
    var colorNote = isDelta
      ? 'Cell = simple rate Δ (bps, weighted-tenor equiv) · color = balance Δ (TL mn)'
      : 'Cell = simple rate (%, T2) · color = T2 new volume';
    // Tek-taraflı hücre açıklaması: yalnız DELTA modunda görünür.
    var oneSidedNote = isDelta
      ? ' &nbsp;·&nbsp; <i>italic</i> = single-period (no T1↔T2 pair); '
        + '<span style="color:#9BAE8A;">ᵀ²</span> new this period, '
        + '<span style="color:#B8826B;">ᵀ¹</span> only in T1'
      : '';
    html += '<div style="margin-top:8px;font-family:\'JetBrains Mono\',monospace;'
          + 'font-size:10px;color:#6B7689;letter-spacing:0.04em;line-height:1.5;">'
          + '<span style="color:var(--accent);">●</span> '
          + 'Rates compound-annualized (act/365); INTEREST RATE mode shows the '
          + 'simple-equivalent via weighted tenor. &nbsp;·&nbsp; ' + colorNote
          + oneSidedNote
          + ' &nbsp;·&nbsp; Outstanding = stok defteri (daily_deposit), avg-daily-balance.'
          + '</div>';

    // Veri kalitesi / filtre uyarıları (sessiz drop yok — #9).
    var warns = d.dq_warnings || [];
    if (warns.length) {
      html += '<div style="margin-top:6px;font-family:\'JetBrains Mono\',monospace;'
            + 'font-size:10px;color:#C8A24A;letter-spacing:0.04em;line-height:1.5;">'
            + warns.map(function(w) { return '⚠ ' + w; }).join('<br>')
            + '</div>';
    }

    wrap.innerHTML = html;

    // Matris paneli default'u = grand total (hover'da hücreye göre güncellenir).
    var mxEl = document.getElementById("np-rvhm-matrix");
    if (mxEl && grand) {
      mxEl.innerHTML = _rvHmMatrixShell(_rvHmTooltip(grand, "TOTAL", "All AUM", isDelta));
    }
    _initNpRvHmCombo();
    // Eksen-başlığı seçimi → gruplama (NP tablo heatmap'i).
    _attachNpHmAxisSelect(d);
  }

  // NP decomp değeri → merge filtre dim'i (Segment=RELATED_PC, CustType=CUST_TP,
  // AUM=AUM_BAND; Tenor gruplanmaz → null).
  function _npAxisMergeDim(dv) {
    return ({ SUB_SEGMENT: "RELATED_PC", RELATED_PC: "RELATED_PC",
              CUST_TP: "CUST_TP", AUM_BAND: "AUM_BAND" })[dv] || null;
  }
  // Ortak AUM band listesinden temiz aralık adı: ["1M-5M","5M-10M"] → "1M-10M".
  function _npCommonRange(vals) {
    if (!vals.length) return "";
    var first = String(vals[0]), last = String(vals[vals.length - 1]);
    var lo = first.indexOf("-") >= 0 ? first.split("-")[0] : first.replace("+", "");
    var hi = (last.slice(-1) === "+") ? "+" : (last.indexOf("-") >= 0 ? last.split("-")[1] : last);
    return hi === "+" ? (lo + "+") : (lo + "-" + hi);
  }

  // NP tablo heatmap eksen-başlığı seçimi. Plotly muadili _attachHmAxisSelect ile
  // aynı davranış (tık/parlat/ctrl-multi/AUM range-fill/Enter→merge) ama DOM tablo.
  function _attachNpHmAxisSelect(d) {
    var wrap = document.getElementById("np-rvhm-wrap");
    if (!wrap) return;
    var decomp  = (d.decomp  || "SUB_SEGMENT");
    var decomp2 = (d.decomp2 || "AUM_BAND");
    var cfg = {
      rowVals: d.rows || [], colVals: d.cols || [],
      rowDim: _npAxisMergeDim(decomp), colDim: _npAxisMergeDim(decomp2),
      rowNumeric: (decomp === "AUM_BAND" || decomp === "TENOR_GRP"),
      colNumeric: (decomp2 === "AUM_BAND" || decomp2 === "TENOR_GRP"),
      meta:   function() { return npVpMeta || {}; },
      state:  function() { return npVpBubState; },
      merges: function() { return npVpBubMerges; },
      apply:  function() { _renderNpVpFilterPanel(); _fetchCurrentNpPage(); },
      // NP grup KUR (mutate only) — AUM ortak-band aralık adı, diğerleri join.
      buildMerge: function(dim, vals) {
        npVpBubMerges[dim] = npVpBubMerges[dim] || [];
        var grp = (dim === "AUM_BAND")
          ? { name: _npCommonRange(vals), members: vals.slice() }
          : { name: vals.join(","), members: vals.slice() };
        if (npVpBubMerges[dim].some(function(g) { return g.name === grp.name; }))
          grp.name = grp.name + " (" + (npVpBubMerges[dim].length + 1) + ")";
        npVpBubMerges[dim].push(grp);
        npVpBubState[dim] = npVpBubState[dim] || {};
        grp.members.forEach(function(m) { if (npVpBubState[dim][m] === undefined) npVpBubState[dim][m] = true; });
        npVpBubState[dim][grp.name] = true;
      },
    };
    wrap.__hmSelCfg = cfg;
    wrap.__axisSel = { axis: null, vals: [] };

    function order(axis) { return axis === "x" ? cfg.colVals : cfg.rowVals; }
    function isNumeric(axis) { return axis === "x" ? cfg.colNumeric : cfg.rowNumeric; }

    function markCells(axis, val, add) {
      wrap.querySelectorAll("td,th").forEach(function(c) {
        var ds = c.dataset || {};
        var m = (axis === "x") ? (ds.au === val || ds.colHdr === val)
                               : (ds.ch === val || ds.rowHdr === val);
        if (m) c.classList.toggle("hm-axsel", add);
      });
    }
    function redraw() {
      wrap.querySelectorAll(".hm-axsel").forEach(function(e) { e.classList.remove("hm-axsel"); });
      var sel = wrap.__axisSel;
      (sel.vals || []).forEach(function(v) { markCells(sel.axis, v, true); });
    }
    function toggle(axis, val, ctrl) {
      var sel = wrap.__axisSel;
      if (sel.axis !== axis) { sel.axis = axis; sel.vals = [val]; }
      else if (!ctrl) {
        sel.vals = (sel.vals.length === 1 && sel.vals[0] === val) ? [] : [val];
        if (!sel.vals.length) sel.axis = null;
      } else {
        var i = sel.vals.indexOf(val);
        if (i >= 0) sel.vals.splice(i, 1); else sel.vals.push(val);
        if (isNumeric(axis) && sel.vals.length >= 2) {
          var ord = order(axis);
          var idxs = sel.vals.map(function(v) { return ord.indexOf(v); }).filter(function(k) { return k >= 0; });
          if (idxs.length) sel.vals = ord.slice(Math.min.apply(null, idxs), Math.max.apply(null, idxs) + 1);
        }
        if (!sel.vals.length) sel.axis = null;
      }
      _hmSelActive = "np-rvhm-wrap";
      redraw();
    }
    // Kolon başlığı = <th data-col-hdr>, satır başlığı = <td data-row-hdr> →
    // eleman-bağımsız seç.
    wrap.querySelectorAll("[data-col-hdr]").forEach(function(h) {
      var v = h.getAttribute("data-col-hdr");
      if (v === "All AUM") return;
      h.style.cursor = "pointer";
      h.addEventListener("click", function(ev) { ev.stopPropagation(); toggle("x", v, ev.ctrlKey || ev.metaKey); });
    });
    wrap.querySelectorAll("[data-row-hdr]").forEach(function(h) {
      var v = h.getAttribute("data-row-hdr");
      if (v === "All") return;
      h.style.cursor = "pointer";
      h.addEventListener("click", function(ev) { ev.stopPropagation(); toggle("y", v, ev.ctrlKey || ev.metaKey); });
    });
  }

  // Hover → ALTTAKİ SABİT MATRİS panelini güncelle (floating tooltip yerine).
  // Hücre HTML'i `data-tip` içinde matris HTML'ini taşır (eski isim korundu).
  // Border hover'da; leave'de border kalkar ama matris son hücrede sabit kalır.
  var _rvHmHoverEl = null;

  // Hover'daki hücrenin satır (segment) ve sütun (AUM) başlıklarını parlatır.
  // Başlıkların özgün rengi/zemini saklanır, leave'de birebir geri yüklenir.
  var _rvHmHotHdrs = [];
  function _rvHmClearHdrs() {
    _rvHmHotHdrs.forEach(function(h) {
      h.el.style.color = h.color;
      h.el.style.background = h.background;
    });
    _rvHmHotHdrs = [];
  }
  function _rvHmHotHdr(sel) {
    var wrap = document.getElementById("np-rvhm-wrap");
    if (!wrap) return;
    var lt = _hmLight();
    wrap.querySelectorAll(sel).forEach(function(el) {
      _rvHmHotHdrs.push({ el: el, color: el.style.color, background: el.style.background });
      // Vurgu: dark = parlak metin/koyu zemin; light = koyu amber metin/amber wash.
      el.style.color = lt ? "#3D2E00" : "#EAECF2";
      el.style.background = lt ? "rgba(184,134,11,0.20)" : "#1B2233";
    });
  }
  function _rvHmHiliteHdrs(el) {
    _rvHmClearHdrs();
    var esc = function(v) { return String(v).replace(/"/g, '\\"'); };
    if (el.dataset.rk != null) _rvHmHotHdr('[data-row-hdr="' + esc(el.dataset.rk) + '"]');
    if (el.dataset.ck != null) _rvHmHotHdr('[data-col-hdr="' + esc(el.dataset.ck) + '"]');
  }

  window._rvHmShowTip = function(el, evt) {
    if (_rvHmHoverEl && _rvHmHoverEl !== el) _rvHmHoverEl.style.boxShadow = "";
    _rvHmHoverEl = el;
    el.style.boxShadow = "inset 0 0 0 2px var(--accent)";
    _rvHmHiliteHdrs(el);   // satır/sütun başlığını parlat
    if (!el.dataset.tip) return;
    var mx = document.getElementById("np-rvhm-matrix");
    if (mx) mx.innerHTML = _rvHmMatrixShell(el.dataset.tip);
    // Sağdaki combo: hover hücresinin YIL BAŞINDAN itibaren geçmişi (debounce'lu).
    if (el.dataset.ch && el.dataset.au) _npHoverCombo(el.dataset.ch, el.dataset.au);
  };
  window._rvHmHideTip = function() {
    if (_rvHmHoverEl) { _rvHmHoverEl.style.boxShadow = ""; _rvHmHoverEl = null; }
    _rvHmClearHdrs();
  };

  // Hover hücresinin yıl-başı→T2 combo'su (bar=bağlanan, line=faiz, seçili freq).
  // Debounce: imleç bir hücrede oturunca çeker; aynı hücreyi tekrar çekmez;
  // uçuştaki isteği iptal eder.
  var _npHoverComboTimer = null, _npHoverComboAbort = null, _npHoverComboKey = null;
  function _npHoverCombo(ch, au) {
    var key = ch + "|" + au;
    if (_npHoverComboTimer) clearTimeout(_npHoverComboTimer);
    _npHoverComboTimer = setTimeout(function() {
      if (key === _npHoverComboKey) return;
      _npHoverComboKey = key;
      var t1El = document.getElementById("np-vp-date1");
      var t1v  = t1El && t1El.value ? t1El.value : "";
      var yearStart = t1v ? (t1v.slice(0, 4) + "-01-01") : "";
      var titleEl = document.getElementById("np-rvhm-hover-combo-title");
      var _lbl = function(v) { return v === "__ALL__" ? "ALL" : v; };
      if (titleEl) titleEl.textContent = _lbl(ch) + " × " + _lbl(au) + "  ·  " + (yearStart || "year start") + " → " + (t1v || "T2");
      if (_npHoverComboAbort) _npHoverComboAbort.abort();
      _npHoverComboAbort = new AbortController();
      var p = new URLSearchParams();
      p.set("channel", ch); p.set("aum", au);
      if (yearStart) p.set("t0", yearStart);
      if (t1v) p.set("t1", t1v);
      p.set("freq", _npSharedFreq());
      fetch("/api/np/cell_timeseries?" + p.toString()
            + _npVpBubStateToQuery() + _npAumMergeParam() + _npRvHmTenorParam() + _npDecompParam(),
            { signal: _npHoverComboAbort.signal, cache: "no-store" })
        .then(function(r) { return r.json(); })
        .then(function(d) { if (d.ok) _renderNpRvHmCombo(d, "np-rvhm-hover-combo"); })
        .catch(function(e) { if (e.name !== "AbortError") { /* sessiz */ } });
    }, 140);
  }
  // Matris panelini kart kabuğuna sar (overflow-x: dar ekranda kaydırılabilsin).
  function _rvHmMatrixShell(inner) {
    return '<div style="border:1px solid var(--border-mid,#252B3D);border-radius:3px;'
         + 'background:var(--bg-input,#0D111C);padding:8px 4px;overflow-x:auto;">'
         + inner + '</div>';
  }

  // ── Çift-tık combo chart: hücrenin new-prod zaman serisi (heatmap'in sağında) ──
  var _npRvHmComboChart = null;
  var _npRvHmComboOk    = false;
  function _initNpRvHmCombo() {
    if (_npRvHmComboOk) return;
    var x = document.getElementById("np-rvhm-combo-close");
    if (x) x.onclick = _rvHmCloseCombo;
    _npRvHmComboOk = true;
  }
  function _rvHmCloseCombo() {
    var p = document.getElementById("np-rvhm-chart-panel");
    if (p) p.style.display = "none";
    if (_npRvHmComboChart) { try { _npRvHmComboChart.destroy(); } catch(e) {} _npRvHmComboChart = null; }
  }
  window._rvHmOpenCombo = function(el) {
    var ch = el.dataset.ch, au = el.dataset.au;
    if (!ch || !au) return;
    var panel   = document.getElementById("np-rvhm-chart-panel");
    var titleEl = document.getElementById("np-rvhm-combo-title");
    var statusEl= document.getElementById("np-rvhm-combo-status");
    if (titleEl) titleEl.textContent = ch + " × " + au;
    if (panel) panel.style.display = "block";
    if (statusEl) statusEl.textContent = "Loading...";

    var t0 = document.getElementById("np-vp-date0");
    var t1 = document.getElementById("np-vp-date1");
    var p = new URLSearchParams();
    p.set("channel", ch); p.set("aum", au);
    if (t0 && t0.value) p.set("t0", t0.value);
    if (t1 && t1.value) p.set("t1", t1.value);
    p.set("freq", _npSharedFreq());
    var url = "/api/np/cell_timeseries?" + p.toString()
            + _npVpBubStateToQuery() + _npAumMergeParam() + _npRvHmTenorParam() + _npDecompParam();
    fetch(url, { cache: "no-store" })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (!d.ok) { if (statusEl) statusEl.textContent = "Hata: " + d.error; return; }
        if (statusEl) statusEl.textContent = (d.records || []).length
          ? "" : "No new-prod data in this cell.";
        _renderNpRvHmCombo(d);
      })
      .catch(function(e) { if (statusEl) statusEl.textContent = "Hata: " + (e.message || e); });
  };
  var _npComboCharts = {};
  function _renderNpRvHmCombo(d, elId) {
    elId = elId || "np-rvhm-combo";
    var recs = d.records || [];
    // Date(End)'i geçen hiçbir nokta gösterilmez (Date(End)'den sonra veri olsa
    // da) — haftalık bin taşmasına/gelecek tarihe karşı sert güvence.
    var _t1El = document.getElementById("np-vp-date1");
    var _t1 = _t1El && _t1El.value ? _t1El.value : "";
    if (_t1) recs = recs.filter(function(r) { return r.date && r.date <= _t1; });
    var cats = recs.map(function(r) { return r.date; });
    var rate = recs.map(function(r) { return r.rate; });
    var bal  = recs.map(function(r) { return r.balance; });
    var rv = rate.filter(function(v) { return v != null; });
    var bv = bal.filter(function(v) { return v != null; });
    var rMin = rv.length ? Math.min.apply(null, rv) : 0;
    var rMax = rv.length ? Math.max.apply(null, rv) : 1;
    var bMin = bv.length ? Math.min.apply(null, bv) : 0;
    var bMax = bv.length ? Math.max.apply(null, bv) : 1;
    var rPad = (rMax - rMin) * 0.12 || 0.5;
    var bPad = (bMax - bMin) * 0.12 || 1;
    if (_npComboCharts[elId]) { try { _npComboCharts[elId].destroy(); } catch(e) {} delete _npComboCharts[elId]; }
    var el = document.getElementById(elId);
    if (!el || typeof ApexCharts === "undefined") return;
    _npComboCharts[elId] = new ApexCharts(el, {
      series: [
        { name: "Booked (mn)", type: "column", data: bal },
        { name: "WAvg Faiz (%)", type: "line",   data: rate },
      ],
      chart: { type: "line", height: 320, toolbar: { show: false }, zoom: { enabled: false },
               animations: { enabled: false }, background: "transparent",
               fontFamily: "'JetBrains Mono',monospace" },
      colors: ["#5C6478", "#D4A574"],
      stroke: { width: [0, 2.5], curve: "straight" },
      plotOptions: { bar: { columnWidth: "55%", borderRadius: 1 } },
      dataLabels: { enabled: false },
      markers: { size: [0, 3] },
      xaxis: { categories: cats, labels: { rotate: -30, hideOverlappingLabels: true,
               style: { fontSize: "11px", colors: "#8B95A7", fontFamily: "'JetBrains Mono',monospace" } },
               axisBorder: { show: false }, axisTicks: { show: false }, tooltip: { enabled: false } },
      yaxis: [
        { seriesName: "Booked (mn)", min: Math.max(0, bMin - bPad), max: bMax + bPad,
          title: { text: "Booked (mn)", style: { color: "#8B95A7", fontSize: "11px" } },
          labels: { style: { colors: "#8B95A7", fontSize: "11px" },
                    formatter: function(v) { return v != null ? Math.round(v) : ""; } } },
        { seriesName: "WAvg Faiz (%)", opposite: true, min: Math.max(0, rMin - rPad), max: rMax + rPad,
          title: { text: "WAvg Faiz (%)", style: { color: "#D4A574", fontSize: "11px" } },
          labels: { style: { colors: "#D4A574", fontSize: "11px" },
                    formatter: function(v) { return v != null ? v.toFixed(1) : ""; } } },
      ],
      legend: { show: true, position: "top", fontSize: "11px",
                labels: { colors: "#B6BECD", useSeriesColors: false } },
      grid: { borderColor: "#1F2433", padding: { left: 6, right: 6 } },
      tooltip: { theme: "dark", shared: true, intersect: false,
                 style: { fontFamily: "'JetBrains Mono',monospace" } },
    });
    _npComboCharts[elId].render();
  }

  // ── Çift-tık → MÜŞTERİ DETAY DRILL-DOWN MODAL'ı ──────────────────────────────
  var _npDrillOk = false;
  function _initNpDrillModal() {
    if (_npDrillOk) return;
    var x = document.getElementById("np-drill-close");
    if (x) x.onclick = _rvHmCloseDrill;
    var modal = document.getElementById("np-drill-modal");
    if (modal) modal.addEventListener("click", function(ev) {
      if (ev.target === modal) _rvHmCloseDrill();   // dışına tıkla → kapat
    });
    _npDrillOk = true;
  }
  function _rvHmCloseDrill() {
    var m = document.getElementById("np-drill-modal");
    if (m) m.classList.add("hidden");
    if (_npDrillGridApi) { try { _npDrillGridApi.destroy(); } catch(e) {} _npDrillGridApi = null; }
    if (_npComboCharts["np-drill-combo"]) {
      try { _npComboCharts["np-drill-combo"].destroy(); } catch(e) {}
      delete _npComboCharts["np-drill-combo"];
    }
  }
  window._rvHmOpenDrill = function(el) {
    _initNpDrillModal();
    var ch = el.dataset.ch, au = el.dataset.au;
    if (!ch || !au) return;
    var modal = document.getElementById("np-drill-modal");
    var titleEl = document.getElementById("np-drill-title");
    var subEl   = document.getElementById("np-drill-subtitle");
    var statusEl= document.getElementById("np-drill-status");
    var t0 = document.getElementById("np-vp-date0");
    var t1 = document.getElementById("np-vp-date1");
    var d0 = t0 ? t0.value : "", d1 = t1 ? t1.value : "";
    var _dl = function(v) { return v === "__ALL__" ? "ALL" : v; };
    if (titleEl) titleEl.textContent = _dl(ch) + " × " + _dl(au);
    if (subEl) subEl.textContent = d0 + " → " + d1 + "  ·  booked deposits (new production)";
    if (modal) modal.classList.remove("hidden");
    if (statusEl) statusEl.textContent = "Loading...";

    var p = new URLSearchParams();
    p.set("channel", ch); p.set("aum", au);
    if (d0) p.set("t0", d0);
    if (d1) p.set("t1", d1);
    var q = p.toString() + _npVpBubStateToQuery() + _npAumMergeParam() + _npRvHmTenorParam() + _npDecompParam();

    // 1) Drill payload (müşteri + histogram + KPI)
    fetch("/api/np/cell_drilldown?" + q, { cache: "no-store" })
      .then(function(r) { return r.json(); })
      .then(function(dd) {
        if (statusEl) statusEl.textContent = "";
        if (!dd.ok) { if (statusEl) statusEl.textContent = "Hata: " + dd.error; return; }
        _renderNpDrill(dd);
      })
      .catch(function(e) { if (statusEl) statusEl.textContent = "Hata: " + (e.message || e); });

    // 2) Zaman serisi (combo) — ayrı endpoint
    var pc = new URLSearchParams();
    pc.set("channel", ch); pc.set("aum", au);
    if (d0) pc.set("t0", d0);
    if (d1) pc.set("t1", d1);
    pc.set("freq", _npSharedFreq());
    fetch("/api/np/cell_timeseries?" + pc.toString() + _npVpBubStateToQuery() + _npAumMergeParam() + _npRvHmTenorParam() + _npDecompParam(),
          { cache: "no-store" })
      .then(function(r) { return r.json(); })
      .then(function(d) { if (d.ok) _renderNpRvHmCombo(d, "np-drill-combo"); })
      .catch(function() {});
  };

  function _renderNpDrill(dd) {
    // KPI şeridi
    var k = dd.kpis || {};
    var kpiEl = document.getElementById("np-drill-kpis");
    if (kpiEl) {
      var fmtMn = function(v) { return v == null ? "—" : Number(v).toLocaleString("en-US",{maximumFractionDigits:1}) + " mn"; };
      var cards = [
        ["Deposits", (k.deposit_count != null ? k.deposit_count : "—")],
        ["Customers", (k.customer_count != null ? k.customer_count : "—")],
        ["Total Balance", fmtMn(k.total_balance_m)],
        ["WAvg Rate", k.wavg_rate != null ? k.wavg_rate.toFixed(2) + "%" : "—"],
        ["WAvg Maturity", k.wavg_dtm != null ? Math.round(k.wavg_dtm) + " g" : "—"],
        ["Campaign", k.kampanya_pct != null ? k.kampanya_pct + "%" : "—"],
      ];
      if (k.yeni_para_m != null) cards.push(["New Money", fmtMn(k.yeni_para_m)]);
      kpiEl.innerHTML = cards.map(function(c) {
        return '<div style="flex:1;min-width:120px;background:var(--bg-input,#0D111C);'
             + 'border:1px solid var(--border-mid,#252B3D);border-left:2px solid var(--accent);'
             + 'border-radius:2px;padding:8px 12px;">'
             + '<div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;'
             + 'letter-spacing:0.12em;text-transform:uppercase;color:var(--text-muted);">' + c[0] + '</div>'
             + '<div style="font-family:\'JetBrains Mono\',monospace;font-size:17px;'
             + 'font-weight:600;color:var(--text-primary);margin-top:3px;">' + c[1] + '</div></div>';
      }).join("");
    }
    // Histogramlar (Plotly)
    _npDrillBar("np-drill-rate-hist", dd.rate_histogram || [], "Price (rate) distribution", "#D4A574");
    _npDrillBar("np-drill-dtm-hist",  dd.dtm_histogram  || [], "Maturity (DTM) distribution",  "#7B8FA0");
    // Mevduat dökümü tablosu
    _renderNpDrillGrid(dd.deposits || []);
    var cnt = document.getElementById("np-drill-count");
    if (cnt) cnt.textContent = "(" + (dd.row_count || 0) + " records"
           + ((dd.deposits || []).length < (dd.row_count || 0) ? ", ilk " + dd.deposits.length + " shown)" : ")");
  }

  function _npDrillBar(elId, hist, title, color) {
    var el = document.getElementById(elId);
    if (!el || typeof Plotly === "undefined") return;
    var x = hist.map(function(h) { return h.bucket; });
    var y = hist.map(function(h) { return h.volume_m; });
    Plotly.newPlot(el, [{
      type: "bar", x: x, y: y, marker: { color: color },
      hovertemplate: "%{x}<br>%{y:.1f} mn<extra></extra>",
    }], {
      title: { text: title, font: { size: 11, color: "#B6BECD", family: "'JetBrains Mono',monospace" } },
      paper_bgcolor: "transparent", plot_bgcolor: "transparent",
      font: { color: "#8B95A7", family: "'JetBrains Mono',monospace", size: 11 },
      margin: { l: 40, r: 10, t: 30, b: 50 },
      xaxis: { tickangle: -30, gridcolor: "#1F2433" },
      yaxis: { title: "mn", gridcolor: "#1F2433" },
    }, { displayModeBar: false, responsive: true });
  }

  // Mevduat dökümü — PRISMA AG Grid (sort + filtre; default FAIZ azalan).
  var _npDrillGridApi = null;
  function _renderNpDrillGrid(deps) {
    var host = document.getElementById("np-drill-grid");
    if (!host || typeof agGrid === "undefined") return;
    if (_npDrillGridApi) { try { _npDrillGridApi.destroy(); } catch(e) {} _npDrillGridApi = null; }
    if (!deps || !deps.length) {
      host.classList.remove("ag-theme-alpine");
      host.innerHTML = '<div style="padding:18px;color:var(--text-muted);font-family:\'JetBrains Mono\',monospace;'
                     + 'font-size:11px;">No booked deposits in this cell.</div>';
      return;
    }
    host.classList.add("ag-theme-alpine");
    host.innerHTML = "";
    var pctF = function(p) { return p.value != null ? p.value.toFixed(2) + "%" : "—"; };
    var mnF  = function(p) { return p.value != null ? p.value.toFixed(2) : "—"; };
    _npDrillGridApi = agGrid.createGrid(host, {
      columnDefs: [
        { field: "full_nm", headerName: "Customer", minWidth: 190, flex: 1.4,
          filter: "agTextColumnFilter",
          cellRenderer: function(p) {
            return '<span class="wr-cust-name">' + (p.data.full_nm || "—") + '</span>'
                 + ' <span style="color:var(--text-faint);">#' + (p.data.cust_id != null ? p.data.cust_id : "") + '</span>';
          }},
        { field: "balance_m", headerName: "Balance (mn)", width: 122, type: "numericColumn",
          cellClass: "wr-num-accent", valueFormatter: mnF, filter: "agNumberColumnFilter" },
        { field: "rate", headerName: "Rate", width: 108, type: "numericColumn",
          cellClass: "wr-num-strong", valueFormatter: pctF, filter: "agNumberColumnFilter",
          sort: "desc" },                                  // DEFAULT: faize göre azalan
        { field: "dtm", headerName: "Maturity", width: 95, type: "numericColumn",
          cellClass: "wr-num-soft", filter: "agNumberColumnFilter",
          valueFormatter: function(p) { return p.value != null ? p.value + " g" : "—"; } },
        { field: "segment", headerName: "Segment", width: 132, filter: "agTextColumnFilter" },
        { field: "kampanya_adi", headerName: "Kampanya", width: 150, filter: "agTextColumnFilter",
          cellRenderer: function(p) {
            return p.data.kampanya
              ? '<span style="color:#9BAE8A;">' + (p.value || "Var") + '</span>'
              : '<span style="color:var(--text-faint);">—</span>';
          }},
        { field: "yeni_para_m", headerName: "New Money", width: 112, type: "numericColumn",
          cellClass: "wr-num-soft", valueFormatter: mnF, filter: "agNumberColumnFilter" },
        { field: "ekstrem", headerName: "Ekstrem", width: 96, filter: false,
          cellStyle: { display: "flex", alignItems: "center", justifyContent: "center" },
          cellRenderer: function(p) {
            return p.value == null ? '<span style="color:var(--text-faint);">—</span>'
                 : (p.value ? '<span style="color:var(--accent);font-size:13px;">●</span>'
                            : '<span style="color:var(--text-faint);">·</span>');
          }},
        { field: "acct_id", headerName: "Hesap", width: 108, type: "numericColumn",
          cellClass: "wr-num-soft", filter: "agNumberColumnFilter" },
        { field: "val_dt", headerName: "Booking", width: 120, filter: "agTextColumnFilter",
          cellStyle: { color: "#8B95A7" } },
        { field: "mtrty_dt", headerName: "Maturity Date", width: 122, filter: "agTextColumnFilter",
          cellStyle: { color: "#8B95A7" } },
        { field: "share_pct", headerName: "Share", width: 92, type: "numericColumn",
          valueFormatter: function(p) { return p.value != null ? p.value.toFixed(1) + "%" : "—"; },
          filter: "agNumberColumnFilter" },
      ],
      rowData: deps,
      headerHeight: 38,
      rowHeight: 34,
      suppressCellFocus: true,
      defaultColDef: { sortable: true, resizable: true, filter: true, floatingFilter: true },
    });
  }


  // ── NP event handlers ───────────────────────────────────────────────────────
  (function() {
    var aumCcySel = document.getElementById("np-aum-ccy-select");
    if (aumCcySel) {
      aumCcySel.addEventListener("change", function() {
        fetchNpAumChart(aumCcySel.value);
      });
    }
  })();

  document.querySelectorAll("#np-nav a").forEach(function(a) {
    a.addEventListener("click", function(e) {
      e.preventDefault();
      setPage(a.dataset.page);
    });
  });

  document.querySelectorAll("#sector-nav a").forEach(function(a) {
    a.addEventListener("click", function(e) {
      e.preventDefault();
      setPage(a.dataset.page);
    });
  });

  (function() {
    var runBtn = document.getElementById("np-run-btn");
    if (runBtn) runBtn.addEventListener("click", function() { refreshNpPage(); });
    document.querySelectorAll(".np-freq-btn").forEach(function(btn) {
      btn.addEventListener("click", function() {
        npFreq = btn.dataset.freq;
        document.querySelectorAll(".np-freq-btn").forEach(function(b) {
          b.classList.toggle("active", b.dataset.freq === npFreq);
        });
        refreshNpPage();
      });
    });
  })();

  /* ══════════════════════════════════════════════════════════════════════
     TEMA (Dark / Light) — toggle + chart re-theming
     Dark = varsayılan; light class body'ye eklenir. Her oturum dark başlar
     (localStorage YOK — session-scope bilinçli karar). Statik/UI katmanı CSS
     değişkenleriyle otomatik switch olur; chart kütüphaneleri CSS var okumadığı
     için runtime tema uygulanır. KRİTİK: dark modda hiçbir chart config'ine
     dokunulmaz (regresyon yok) — tema sadece light aktifken veya toggle anında
     uygulanır. Trace/data renkleri v1'de dark palet kalır (earthy tonlar kremde
     okunur); ince ayar iterasyona bırakıldı.
     ══════════════════════════════════════════════════════════════════════ */
  (function initTheme() {
    function isLight() { return document.body.classList.contains("light-mode"); }

    // ── Plotly: newPlot/react monkey-patch — layout'u YALNIZ light modda
    //    tema-uyumlu yap (transparan zemin + krem font/grid). Dark = passthrough.
    if (typeof Plotly !== "undefined" && !Plotly.__prismaThemed) {
      Plotly.__prismaThemed = true;
      var _origNewPlot = Plotly.newPlot, _origReact = Plotly.react;
      var _themeLayout = function(layout) {
        if (!isLight()) return layout;                 // DARK: dokunma
        layout = Object.assign({}, layout || {});
        layout.paper_bgcolor = "rgba(0,0,0,0)";        // kart zemini (krem) görünsün
        layout.plot_bgcolor  = "rgba(0,0,0,0)";
        layout.font = Object.assign({}, layout.font, { color: "#4A4844" });
        // Başlık: dark için zorlanan açık renk (#E4E8F0) kremde kaybolur →
        // light modda koyu grafit başlığa çevir.
        if (layout.title && typeof layout.title === "object")
          layout.title = Object.assign({}, layout.title,
            { font: Object.assign({}, layout.title.font, { color: "#2C2A26" }) });
        if (layout.legend) layout.legend = Object.assign({}, layout.legend,
          { font: Object.assign({}, layout.legend.font, { color: "#4A4844" }) });
        Object.keys(layout).forEach(function(k) {
          if (/^[xy]axis\d*$/.test(k) && layout[k] && typeof layout[k] === "object") {
            var ax = Object.assign({}, layout[k]);
            ax.gridcolor     = "#E5DFD1";
            ax.zerolinecolor = "#D4CDB8";
            ax.linecolor     = "#D4CDB8";
            ax.tickfont = Object.assign({}, ax.tickfont, { color: "#6B6862" });
            if (ax.title && typeof ax.title === "object")
              ax.title = Object.assign({}, ax.title,
                { font: Object.assign({}, ax.title.font, { color: "#4A4844" }) });
            layout[k] = ax;
          }
        });
        return layout;
      };
      Plotly.newPlot = function(el, data, layout, config) {
        return _origNewPlot.call(Plotly, el, data, _themeLayout(layout), config);
      };
      Plotly.react = function(el, data, layout, config) {
        return _origReact.call(Plotly, el, data, _themeLayout(layout), config);
      };
    }

    // ── Plotly: canlı toggle'da render edilmiş grafikleri relayout et ──
    // KRİTİK: dark'a dönüşte SABİT değer TAHMİN ETME (grafiklerin gerçek dark
    // config'i transparan zemin + rgba-beyaz grid). İlk sweep'te her grafiğin
    // pristine değerlerini yakala, dark'a dönüşte BİREBİR geri yükle → dark
    // pixel-identical kalır (toggle round-trip'ten sonra bile).
    var _PLOTLY_AX = ["xaxis","yaxis","xaxis2","yaxis2","xaxis3","yaxis3","xaxis4","yaxis4"];
    function sweepPlotly() {
      if (typeof Plotly === "undefined") return;
      var light = isLight();
      document.querySelectorAll(".js-plotly-plot").forEach(function(gd) {
        var lay = gd.layout || {};
        if (!gd.__prismaOrig) {
          var o = {
            paper_bgcolor: lay.paper_bgcolor,
            plot_bgcolor:  lay.plot_bgcolor,
            "font.color":  lay.font && lay.font.color,
            "title.font.color": lay.title && lay.title.font && lay.title.font.color,
            "hoverlabel.bgcolor":     lay.hoverlabel && lay.hoverlabel.bgcolor,
            "hoverlabel.font.color":  lay.hoverlabel && lay.hoverlabel.font && lay.hoverlabel.font.color
          };
          _PLOTLY_AX.forEach(function(ax) {
            if (lay[ax]) {
              o[ax + ".gridcolor"]      = lay[ax].gridcolor;
              o[ax + ".zerolinecolor"]  = lay[ax].zerolinecolor;
              o[ax + ".linecolor"]      = lay[ax].linecolor;
              o[ax + ".tickfont.color"] = lay[ax].tickfont && lay[ax].tickfont.color;
            }
          });
          // Legend arka planı (ör. Maturity Ladder'da koyu translucent) → light
          // modda grafikle aynı krem tonuna dönsün diye orijinali sakla.
          if (lay.legend && lay.legend.bgcolor) o["legend.bgcolor"] = lay.legend.bgcolor;
          gd.__prismaOrig = o;
        }
        var upd;
        if (light) {
          upd = {
            paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
            "font.color": "#4A4844",
            "title.font.color": "#2C2A26",
            "hoverlabel.bgcolor": "#FFFEFA", "hoverlabel.font.color": "#2C2A26"
          };
          _PLOTLY_AX.forEach(function(ax) {
            if (lay[ax]) {
              upd[ax + ".gridcolor"]      = "#E5DFD1";
              upd[ax + ".zerolinecolor"]  = "#D4CDB8";
              upd[ax + ".linecolor"]      = "#D4CDB8";
              upd[ax + ".tickfont.color"] = "#6B6862";
            }
          });
          // Yalnız legend bg'si olan grafiklerde (Maturity Ladder) → krem translucent
          // (--bg-base #F5F1E8) → legend grafikle aynı zeminde görünür.
          if (gd.__prismaOrig["legend.bgcolor"]) upd["legend.bgcolor"] = "rgba(245,241,232,0.72)";
        } else {
          upd = gd.__prismaOrig;   // pristine dark'ı BİREBİR geri yükle
        }
        try { Plotly.relayout(gd, upd); } catch (e) {}
        // Trace-seviyesi metinler (bubble üstü etiketler vb.) layout sweep'iyle
        // değişmez → textfont.color'ı capture/restore ile temala. Dark değerleri
        // pristine saklanır, light'ta koyu grafite çevrilir.
        try {
          (gd.data || []).forEach(function(tr, ti) {
            if (!tr || !/text/.test(tr.mode || "") || !tr.textfont) return;
            if (!gd.__prismaTextOrig) gd.__prismaTextOrig = {};
            if (!(ti in gd.__prismaTextOrig)) gd.__prismaTextOrig[ti] = tr.textfont.color || null;
            var col = light ? "#4A4844" : gd.__prismaTextOrig[ti];
            if (col) Plotly.restyle(gd, { "textfont.color": col }, [ti]);
          });
        } catch (e) {}
      });
    }

    // ── ApexCharts: izlenen instance'ları capture/restore ile temala ──
    // Sadece foreColor + grid.borderColor dokunulur (theme.mode ve eksen etiket
    // renkleri ELLENMEZ → combo'nun amber sağ ekseni korunur). Dark'a dönüşte
    // yakalanan pristine değerler geri yüklenir. window.Apex GLOBAL'İ KİRLETİLMEZ.
    function sweepApex() {
      if (typeof ApexCharts === "undefined") return;
      var light = isLight();
      var maps = [];
      try { if (typeof npCharts === "object" && npCharts) maps.push(npCharts); } catch (e) {}
      try { if (typeof _npComboCharts === "object" && _npComboCharts) maps.push(_npComboCharts); } catch (e) {}
      // Waterfall / bridge chart'ları (chartInstances) da temala — eskiden
      // kapsam dışıydı, light modda başlık/foreColor koyu zeminde kalıyordu.
      try { if (typeof chartInstances === "object" && chartInstances) maps.push(chartInstances); } catch (e) {}
      maps.forEach(function(m) {
        Object.keys(m).forEach(function(k) {
          var inst = m[k];
          if (!inst || !inst.updateOptions) return;
          try {
            if (!inst.__prismaOrig) {
              var cfg = (inst.w && inst.w.config) || {};
              inst.__prismaOrig = {
                chart: { foreColor: (cfg.chart && cfg.chart.foreColor) },
                grid:  { borderColor: (cfg.grid && cfg.grid.borderColor) },
                title: { style: { color: (cfg.title && cfg.title.style && cfg.title.style.color) } },
                // Nokta/bar üstü değer etiketleri (dark: #E4E8F0) — light'ta
                // krem zeminde kaybolur, capture/restore ile temala.
                dataLabels: { style: { colors: (cfg.dataLabels && cfg.dataLabels.style &&
                                                cfg.dataLabels.style.colors) } }
              };
            }
            var opts = light
              ? { chart: { foreColor: "#4A4844" }, grid: { borderColor: "#E5DFD1" },
                  title: { style: { color: "#2C2A26" } },
                  dataLabels: { style: { colors: ["#2C2A26"] } } }
              : inst.__prismaOrig;
            // dataLabels'a YALNIZ pristine'de explicit renk varsa dokun —
            // yoksa iki yönde de gönderme (Apex default'u bozulmasın, dark'a
            // dönüşte restore edilecek değer de yok).
            if (!(inst.__prismaOrig.dataLabels && inst.__prismaOrig.dataLabels.style &&
                  inst.__prismaOrig.dataLabels.style.colors)) {
              opts = { chart: opts.chart, grid: opts.grid, title: opts.title };
            }
            inst.updateOptions(opts, false, false);
          } catch (e) {}
        });
      });
    }

    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", function() {
        // Geçişi yumuşat: sadece toggle anında 450ms transition (dark hover
        // davranışını DEĞİŞTİRMEZ — kalıcı transition kuralı yok).
        document.body.classList.add("theme-anim");
        setTimeout(function() { document.body.classList.remove("theme-anim"); }, 450);
        document.body.classList.toggle("light-mode");
        window.dispatchEvent(new CustomEvent("themeChange",
          { detail: { mode: isLight() ? "light" : "dark" } }));
      });
    }
    // Chart'lara mode değişimini bildir (harici dinleyiciler + kendi sweep'lerimiz)
    window.addEventListener("themeChange", function() {
      sweepPlotly();
      sweepApex();
      // Renkleri render anında string/trace olarak üreten heatmap'ler sweep
      // ile değişmez → GÖRÜNÜR olanları cache'ten yeniden render et.
      var _vis = function(id) { var e = document.getElementById(id); return e && e.offsetParent !== null; };
      // New Business — Rate × Volume heatmap (HTML tablo)
      try {
        if (typeof _npRvHmData !== "undefined" && _npRvHmData && _vis("np-rvhm-wrap") &&
            typeof renderNpRvHeatmap === "function") renderNpRvHeatmap(_npRvHmData);
      } catch (e) {}
      // Future Deposit Rollings — dönüş heatmap'leri (AG-Grid)
      try {
        if (typeof weeklyReportState === "object" && weeklyReportState && weeklyReportState.payload &&
            _vis("wr-grid-1") && typeof _renderWeeklySlide1 === "function")
          _renderWeeklySlide1(weeklyReportState.payload);
      } catch (e) {}
      // Outstanding Balance / Cost — Balance Heatmap (Plotly, Segment × AUM)
      try {
        if (typeof baHmState === "object" && baHmState && typeof _renderBaHeatmap === "function") {
          Object.keys(baHmState).forEach(function(pfx) {
            var st = baHmState[pfx];
            if (!st || !st.payload) return;
            if (_vis(pfx + "-heatmap"))      _renderBaHeatmap(pfx, st.payload, st.lbl0, st.lbl1);
            if (_vis(pfx + "-cust-heatmap")) _renderBaHeatmap(pfx, st.payload, st.lbl0, st.lbl1, "customer");
          });
        }
      } catch (e) {}
      // Cost Analysis — Interest Rate heatmap (Plotly)
      try {
        if (typeof _renderCaRateHeatmap === "function") {
          [["ca-mon", typeof caMonFigs !== "undefined" ? caMonFigs : null],
           ["ddd",    typeof dddFigs    !== "undefined" ? dddFigs    : null]].forEach(function(pr) {
            var pfx = pr[0], figs = pr[1];
            if (figs && figs.rate_heatmap && _vis(pfx + "-rate-heatmap")) {
              var d0 = document.getElementById(pfx + "-date0"), d1 = document.getElementById(pfx + "-date1");
              _renderCaRateHeatmap(pfx, figs.rate_heatmap, d0 ? d0.value : "t0", d1 ? d1.value : "t1");
            }
          });
        }
      } catch (e) {}
    });
  })();

})();
