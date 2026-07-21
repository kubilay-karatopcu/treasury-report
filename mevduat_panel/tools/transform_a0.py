#!/usr/bin/env python3
"""Faz A0 donusum scripti: NIM_calculation index.html -> mevduat_panel modulu.

Kaynak SPA'yi uc parcaya ayirir (template markup / CSS / JS), NII bloklarini
kirpar, CDN'leri vendor static'e cevirir. Idempotent: her kosum kaynaktan
yeniden uretir.
"""
import os
import re
from pathlib import Path

SRC = Path(os.environ.get(
    "NIM_SRC",
    "/tmp/claude-0/-home-user-treasury-report/352d97fa-5f6e-5022-ba03-cb7f8d08e7cc/scratchpad/NIM_calculation",
)) / "templates" / "index.html"
MOD = Path("/home/user/treasury-report/mevduat_panel")

s = SRC.read_text()

def cut_between(s, start_marker, end_marker, keep_end=True):
    """start_marker'dan end_marker'a kadar olan blogu cikarir.
    keep_end=True ise end_marker kalir (kesim ondan once biter)."""
    i = s.index(start_marker)
    j = s.index(end_marker, i + len(start_marker))
    if not keep_end:
        j += len(end_marker)
    removed = s[i:j]
    return s[:i] + s[j:], removed

# ── 1. CSS'i cikar ─────────────────────────────────────────────────────────
i = s.index("<style>")
j = s.index("</style>") + len("</style>")
css = s[s.index("<style>") + len("<style>"): s.index("</style>")]
s = s[:i] + '  <link rel="stylesheet" href="{{ url_for(\'mevduat_panel.static\', filename=\'mevduat_panel.css\') }}?v={{ mevduat_version }}">' + s[j:]

# ── 2. JS'i cikar ──────────────────────────────────────────────────────────
i = s.index("<script>\n(function() {")
j = s.rindex("</script>") + len("</script>")
js = s[i + len("<script>\n"): s.rindex("</script>")]
inject = (
    '<script>\n'
    '  // Port bootstrap konfigurasyonu — mevduat_panel.js bunu okur.\n'
    '  window.MEVDUAT_CONFIG = {\n'
    '    apiBase: {{ url_for("mevduat_panel.index") | tojson }},\n'
    '    masaUrl: {{ masa_url | tojson }}\n'
    '  };\n'
    '</script>\n'
    '<script src="{{ url_for(\'mevduat_panel.static\', filename=\'mevduat_panel.js\') }}?v={{ mevduat_version }}"></script>'
)
s = s[:i] + inject + s[j:]

# ── 3. CDN -> vendor static ────────────────────────────────────────────────
V = "{{ url_for('mevduat_panel.static', filename='vendor/%s') }}"
cdn_map = {
    'https://cdn.jsdelivr.net/npm/apexcharts@3.54.0/dist/apexcharts.min.js': V % 'apexcharts.min.js',
    'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js': V % 'plotly.min.js',
    'https://cdn.jsdelivr.net/npm/ag-grid-community@31.3.4/styles/ag-grid.css': V % 'ag-grid.css',
    'https://cdn.jsdelivr.net/npm/ag-grid-community@31.3.4/styles/ag-theme-alpine.css': V % 'ag-theme-alpine.css',
    'https://cdn.jsdelivr.net/npm/ag-grid-community@31.3.4/dist/ag-grid-community.min.js': V % 'ag-grid-community.min.js',
    'https://cdn.jsdelivr.net/npm/jspdf@2.5.2/dist/jspdf.umd.min.js': V % 'jspdf.umd.min.js',
    'https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/flatpickr.min.css': V % 'flatpickr.min.css',
    'https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/flatpickr.min.js': V % 'flatpickr.min.js',
}
for old, new in cdn_map.items():
    assert old in s, old
    s = s.replace(old, new)

# ── 4. NII markup kirpimlari ───────────────────────────────────────────────
# 4a. Manual nav (standalone export kalintisi)
s, _ = cut_between(s, '    <!-- Visible only in the standalone HTML export -->',
                   '    <!-- ── NII Dashboard group')
# 4b. NII sidebar grubu
s, _ = cut_between(s, '    <!-- ── NII Dashboard group',
                   '    <!-- ── Deposit Dashboard group')
# 4c. NII filtre barlari (#standard-filters ... #cross-filters dahil), #err'e kadar
s, _ = cut_between(s, '    <div class="filters" id="standard-filters">',
                   '    <div id="err"')
# 4d. #std-section (NIM Evolution + BS Evolution + gomulu Deposit Detail sekmesi)
idx_cost = s.index('Cost Analysis page (under Deposit Dashboard)')
idx_cost = s.rindex('<!--', 0, idx_cost)
i = s.index('    <!-- Standard pages: Realized NII')
s = s[:i] + s[idx_cost - 4:]  # "    <!--" girintisini koru
# 4e. #sim-section (Results Comparison)
s, _ = cut_between(s, "    <!-- Simulation Results comparison page -->",
                   "    <!-- User's Manual")
# 4f. #manual-section
s, _ = cut_between(s, "    <!-- User's Manual", '  </main>')

# ── 5. Sidebar: masa linki + baslik ────────────────────────────────────────
s = s.replace(
    '    <div class="sidebar-brand">Balance Sheet Dashboard</div>',
    '    <div class="sidebar-brand">Deposit Dashboard</div>\n'
    '    <a class="masa-link" href="{{ masa_url }}">&#8592; Masa</a>'
)

# ── 6. JS Jinja satirlari -> notr sabitler ─────────────────────────────────
js = js.replace('var SIM_SCENARIOS = {{ sim_scenarios | tojson }};',
                'var SIM_SCENARIOS = [];')
js = js.replace('let currentDataSource = "{{ default_source }}";',
                'let currentDataSource = "";')

# ── 7. JS bootstrap onsozu (fetch prefix + tema koprusu) ───────────────────
prelude = """\
/* mevduat_panel.js — NIM_calculation @ bs_evolution5 SPA'sinin deposit-only portu.
   Kaynak: templates/index.html satir 3073-15668 (dogutan/NIM_calculation).
   Asagidaki onsoz porta ozgudur; gerisi kaynak SPA kodudur (NII kirpimli). */
(function () {
  var cfg = window.MEVDUAT_CONFIG || {};
  // Blueprint url_prefix + OpenShift SCRIPT_NAME uyumu: SPA'nin "/api/..."
  // cagrilari blueprint tabanina yonlendirilir. Sayfa-scoped tek shim.
  var base = (cfg.apiBase || "/").replace(/\\/$/, "");
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

"""
js = prelude + js

# ── 7b. Port'a ozgu CSS ekleri ─────────────────────────────────────────────
css += """
/* ═══ Port eki (mevduat_panel): Masa'ya donus linki — PRISMA kabuguna kopru ═══ */
.masa-link {
  display: block;
  padding: 0 20px 10px;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-family: var(--font-mono, monospace);
  color: var(--text-secondary);
  text-decoration: none;
}
.masa-link:hover { color: var(--accent); }
"""

# ── 8. Yaz ─────────────────────────────────────────────────────────────────
(MOD / "templates" / "mevduat_panel").mkdir(parents=True, exist_ok=True)
(MOD / "static").mkdir(parents=True, exist_ok=True)
(MOD / "templates" / "mevduat_panel" / "index.html").write_text(s)
(MOD / "static" / "mevduat_panel.css").write_text(css)
(MOD / "static" / "mevduat_panel.js").write_text(js)
print("template:", len(s.splitlines()), "satir")
print("css     :", len(css.splitlines()), "satir")
print("js      :", len(js.splitlines()), "satir")
