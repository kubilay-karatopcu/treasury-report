#!/usr/bin/env python3
"""A0: nim_panel.js'ten NII boot bağlama bloklarını söker.

Her silme (start_marker, end_marker) çifti ile tanımlı; end_marker dahil
silinir. Marker bulunamazsa hata verir (sessiz atlama yok).
"""
from pathlib import Path

P = Path("/home/user/treasury-report/nim_panel/static/nim_panel.js")
js = P.read_text()

DELETIONS = [
    # (aciklama, start_marker, end_marker)
    ("scenario dropdown IIFE",
     "  // Populate Scenario Name and Cross-Scenario dropdowns\n  (function() {",
     "  })();"),
    ("NII accordion listeners",
     '  // Accordion button listeners\n  ["historic", "waterfall", "table"]',
     "toggleSection(name);\n    });\n  });"),
    ("BSE accordion listeners",
     "  // BSE section accordion listeners",
     "toggleBseSection(key);\n    });\n  });"),
    ("BSE currency tab listeners",
     "  // Section A currency tab (TRY / FX) listeners",
     "if (bseDataA) renderBseSectionA(bseDataA);\n    });\n  });"),
    ("NII ust-tab listeners (nim-evolution/bs-evolution/deposit-detail)",
     "  // Tab navigation listeners",
     "fetchDepositDetailWaterfalls();\n      }\n    });\n  });"),
    ("raw table view toggle",
     "  // Balance Sheet Table view toggle (Rate ↔ NII)",
     "setRawTableView(rawTableView + 1);\n  });"),
    ("NII waterfall carousel",
     '  // Waterfall carousel prev/next\n  document.getElementById("wf-prev")',
     "renderWfSlide(wfSlide + 1);\n  });"),
    ("dd- dim toggles",
     "  // Deposit Detail dimension toggles (scoped to dd-section only)",
     "fetchDepositDetailWaterfalls();\n    });\n  });"),
    ("dd- accordion toggle",
     "  // Deposit Detail accordion toggle",
     'body.style.overflow  = "hidden";\n    }\n  });'),
    ("dd- carousel",
     "  // Deposit Detail carousel prev/next",
     "renderDdSlide(ddSlide + 1);\n  });"),
    ("sim PDF export btn",
     "  // Simulation Results PDF export",
     "exportSimResultsPdf();\n  });"),
    ("sim NIM carousel",
     "  // Simulation NIM carousel prev/next",
     "renderSimNimSlide(simNimSlide + 1);\n  });"),
    ("sim Loans carousel",
     "  // Simulation Loans carousel prev/next",
     "renderSimLoansSlide(simLoansSlide + 1);\n  });"),
    ("loans product dropdown",
     "  // Loans extra-product dropdown",
     "addLoansProduct(p.name, p.currency);\n  });"),
    ("report-nav wiring",
     '  document.querySelectorAll("#report-nav a").forEach(a => {\n'
     '    a.addEventListener("click", function(e) {\n'
     "      e.preventDefault();\n"
     "      setDataSource(this.dataset.source);",
     "    });\n  });"),
    ("scenarioName change",
     '  document.getElementById("scenarioName").addEventListener("change", function() {',
     "refreshDates();\n  });"),
    ("analysis-nav wiring",
     '  document.querySelectorAll("#analysis-nav a").forEach(a => {',
     "setPage(this.dataset.page);\n      }\n    });\n  });"),
    ("manual-nav wiring",
     '  document.querySelectorAll("#manual-nav a").forEach(a => {',
     "setPage(this.dataset.page);\n    });\n  });"),
    ("sim BS table view toggle",
     "  // Sim Balance-Sheet table view toggle",
     "_buildSbtColDefs(simBsData[simBsSource].dates, simBsView));\n      }\n    });\n  });"),
    ("sim BS scenario dropdown",
     "  // Sim Balance-Sheet scenario dropdown",
     "if (src) fetchSimBsTable(src);\n  });"),
    ("cross + std filtre listeners",
     "  // Cross-scenario filter change listeners",
     '  elDate1.addEventListener("change", function() {\n'
     '    if (currentPage === "standard") onParamsChange();\n'
     "  });"),
    ("standalone HTML export IIFE",
     "  // ── Generate standalone HTML export",
     "  })();"),
]

for desc, start, end in DELETIONS:
    i = js.find(start)
    assert i >= 0, f"START bulunamadi: {desc}"
    j = js.find(end, i + len(start))
    assert j >= 0, f"END bulunamadi: {desc}"
    j += len(end)
    # blok sonrasi tek bos satiri da yut
    while js[j:j+1] == "\n" and js[j+1:j+2] == "\n":
        j += 1
    removed = js[i:j]
    n = removed.count(chr(10)) + 1
    assert n < 80, f"SUPHELI BUYUK SILME ({n} satir): {desc}"
    js = js[:i] + js[j:]
    print(f"OK  {desc}: {n} satir silindi")

# ── Boot: NII acilisi yerine deposit acilisi ────────────────────────────────
old_boot = """  if (IS_EXPORT) {
    // Standalone export opens on the User's Manual page
    setPage("users-manual");
  } else {
    updatePageVisibility();
    setActiveNav();
    updateTitle();
  }
  refreshDates();"""
new_boot = """  // Port: NII tarafi kaldirildi — acilis, deposit tarafinin ilk sayfasi.
  setPage("cost-analysis");"""
assert old_boot in js, "boot blogu bulunamadi"
js = js.replace(old_boot, new_boot)
print("OK  boot -> setPage('cost-analysis')")

# ── updatePageVisibility: NII satirlari ─────────────────────────────────────
old = """    standardFilters.classList.toggle("hidden", !isStd);
    crossFilters.classList.toggle("hidden", !isCross);
    simulationFilters.classList.toggle("hidden", !isSim);
    stdSection.classList.toggle("hidden", isSim || isManual || isCost || isTenor || isBal || isWeekly || isNp || isSector);
    var sectorSec = document.getElementById("sector-comparison-section");"""
new = """    var sectorSec = document.getElementById("sector-comparison-section");"""
assert old in js
js = js.replace(old, new)

old = """    simSection.classList.toggle("hidden", !isSim);
    var npFiltersEl"""
new = """    var npFiltersEl"""
assert old in js
js = js.replace(old, new)

old = """    if (manualSection) manualSection.classList.toggle("hidden", !isManual);
    var costSec"""
new = """    var costSec"""
assert old in js
js = js.replace(old, new)

# NII tab-nav blogu (isRealized ... ddSec) — caMonSec blogundan oncesi
i = js.find("    // Tab nav visible on historical sources AND in Scenario Analysis mode")
j = js.find("    // Cost Analysis sub-tabs: Monthly Averages / Daily Evolution.")
assert 0 <= i < j, "updatePageVisibility NII tab blogu bulunamadi"
js = js[:i] + js[j:]
print("OK  updatePageVisibility NII satirlari silindi")

# ── updateTitle: NII dallari yerine deposit basliklari ──────────────────────
old = """  function updateTitle() {
    if (NP_PAGE_TITLES[currentPage]) {
      singleTitle.textContent = NP_PAGE_TITLES[currentPage];
    } else if (currentPage === "users-manual") {
      singleTitle.textContent = "User's Manual";
    } else if (currentPage === "simulation-results") {
      singleTitle.textContent = "Simulation Results";
    } else if (currentPage === "cross-scenario") {
      var n1 = elCrossScn1.value, n2 = elCrossScn2.value;
      var nt = elCrossNimType.value === "FX" ? "FX NIM" : "TRY NIM";
      singleTitle.textContent = n1 + " vs " + n2 + " – " + nt;
    } else {
      singleTitle.textContent = currentDataSource + " – " + (elNimType.value === "FX" ? "FX NIM" : "TRY NIM");
    }
  }"""
new = """  var DEPOSIT_PAGE_TITLES = {
    "cost-analysis":    "Outstanding Cost Analysis",
    "balance-analysis": "Outstanding Balance Analysis",
    "tenor-analysis":   "Outstanding Tenor Analysis",
    "weekly-report":    "Future Deposit Rollings",
  };

  function updateTitle() {
    singleTitle.textContent = NP_PAGE_TITLES[currentPage] ||
      DEPOSIT_PAGE_TITLES[currentPage] || "Deposit Dashboard";
  }"""
assert old in js, "updateTitle bulunamadi"
js = js.replace(old, new)
print("OK  updateTitle deposit basliklariyla yeniden yazildi")

P.write_text(js)
print("\ntoplam:", len(js.splitlines()), "satir")
