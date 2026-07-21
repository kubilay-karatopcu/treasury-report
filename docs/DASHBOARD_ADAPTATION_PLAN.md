# Dashboard Adaptation — NIM_calculation → PRISMA Entegrasyon Planı

> Branch: `dashboard_adaptation` · Kaynak: `doguctan/NIM_calculation@bs_evolution5` (c569ae3)
> Durum: **Faz A0 tamamlandı** (kabuk + navigasyon canlı, endpoint'ler stub).
> Kullanıcı onayları: K1 izolasyon (kritik), K2 kaynak SQL (PRISMA_DEP_* kesinlikle
> kullanılmayacak), K3 temiz aktarım (sadeleştirme/refaktör gözetilerek), K4
> modül-scoped Plotly. Bu doküman kaynak repo envanteri + treasury-report
> entegrasyon yüzeyi analizinin çıktısıdır; uygulama fazları aşağıdadır.

---

## 1. Amaç ve konumlandırma

`NIM_calculation` reposundaki dashboard'u (önce **deposit tarafı**) PRISMA
altyapısını kullanarak treasury-report uygulamasına taşımak. Konumlandırma:

- **Masa ve atölye DIŞINDA yaşar** — standart Keşif → Hazırlık → Sunum
  akışının parçası DEĞİLDİR. Kendi sayfası, kendi endpoint'leri, kendi
  frontend'i olan bağımsız bir süreçtir.
- **Masadan görünür** — `prisma_home` landing sayfasına kart/link eklenir.
- **Fonksiyonalite birebir** — kaynak dashboard'un davranışı (drill'ler,
  hover-linked paneller, slider'lar, carousel'ler, AG Grid modalları, BSC
  overlay) aynen korunur. Blok/manifest sistemine sığıp sığmaması bir kısıt
  DEĞİLDİR; bu modül manifest üretmez, kendi SPA'sını taşır.
- Mevcut blok-tabanlı deposits dashboard'ları (`jobs/deposits_dashboards.py`
  → 5 sunum manifest'i) **olduğu gibi kalır**; bu modül onların yerine
  geçmez, yanında yaşar.

## 2. Kaynak envanter özeti (bs_evolution5)

Deposit tarafı **7 sayfadır** (sidebar `#grp-deposit`), görev tanımındaki
"5 sayfa" Outstanding + Rollings + NP'yi kapsar; sektör sayfaları da deposit
query'lerini kullanan tek yerler olduğundan kapsama dahildir:

| # | Sayfa | Ana endpoint'ler | Engine |
|---|-------|------------------|--------|
| 1 | Outstanding Cost Analysis (Monthly/Daily) | `/api/deposit_detail_*`, `/api/daily_deposit_*`, `/api/cost_rate_heatmap`, `/api/hm_product_bar`, `/api/deposit_product_daily`, `/api/bubble_series`, `/api/rate_drill` | `DepositDetailEngine`, `DailyDepositEngine` |
| 2 | Outstanding Balance Analysis | `/api/balance_dates`, `/api/balance_monthly`, `/api/balance_daily`, `/api/balance_drill` | `BalanceAnalysisEngine`, `DailyBalanceEngine` |
| 3 | Outstanding Tenor Analysis | `/api/tenor_dates`, `/api/tenor_monthly`, `/api/tenor_daily` | `TenorAnalysisEngine`, `DailyTenorEngine`, `SwapHedgeEngine` |
| 4 | Future Deposit Rollings | `/api/weekly_rollings`, `/api/weekly_segments`, `/api/weekly_drilldown` | `WeeklyRollingsEngine` |
| 5 | New Business — Volume & Pricing | `/api/np/*` (11 endpoint) | `engine/np_agg.py`, `engine/outstanding_daily.py` |
| 6 | Sector Comparison | `/api/sector_*`, `/api/tcmb_rate_table` | `engine/sector_data.py` (1622 satır) |
| 7 | BSC Presentation (tam-ekran overlay) | `/api/bsc_np_rate_series`, `/api/bsc_np_monthly_table`, `/api/sector_vade_mix_pres` | sector_data + DOM taşıma |

**Veri kaynakları** (11 SQL, `queries/prod/` Oracle lehçesi, `A16438.` şema
prefix'li): `DEPOSITUSAGE_NEW` (stok), `MEVDUAT_DONUSLER_FULLDATA` (akım),
`BDDK_AMT_KIRILIM/VADE/MALIYET`, `tcmb_deposit_rates`, `bist_tlref`, `SWAPS`.
Dev ortamı: `data/dev.db` (SQLite, 8.9 MB, `seed_dev_db.py` üretir; kısmen
sentetik) + `queries/dev/` aynaları.

**Frontend**: tek dosyalık SPA (`templates/index.html`, 15.7k satır, inline
JS). Chart motorları: **Plotly** (bubble, heatmap, ladder, curve, mix,
sparkline) + **ApexCharts** (waterfall, bridge, AUM combo) + **AG Grid
Community 31.3.4** (weekly/NP tabloları + drill modalları). Tema: PRISMA
paleti (navy + amber) dark/light, `sweepPlotly`/`sweepApex` canlı geçiş,
`initChartFullscreen` (başlığa tıkla → tam ekran). `DEPOSIT_REDESIGN_REPORT.md`
ile görsel dil zaten PRISMA'ya hizalanmış — görsel adaptasyon büyük ölçüde
kaynak tarafta bitmiş durumda.

**Kritik hesap sözleşmeleri** (portta birebir korunacak):

- Oranlar asla `mean()` ile alınmaz; wavg = `Σ(B×r)/ΣB`, yeniden gruplamada
  `TRY_X_*` additive çarpım toplamları kullanılır.
- Rate Type dönüşümü satır bazında: simple ↔ compound ↔ O/N eşleniği
  (act/365; `_convert_rate_series`, `np_agg.simple_to_compound*`).
- "Apply Demand Effect": KGH/BTH satırlarında bakiye ×(1+p), simple oran
  ÷(1+p) — rate dönüşümünden ÖNCE uygulanır.
- Stok (`DEPOSITUSAGE_NEW`) günler arası toplanamaz, akım
  (`MEVDUAT_DONUSLER_FULLDATA`) toplanabilir; NP outstanding'i point-in-time
  as-of ile stoktan gelir.
- Sektör faiz serisi: kümülatif faiz gideri farkı + ACT/ACT yıllıklandırma;
  TCMB(BDDK Mix) vintage ağırlıklama; mix kimliği `Σ(wB−wS)(R_b−R̄)`.
- Bilinmeyen band/bucket eşlemesi sessiz drop edilmez → `ValueError`.
- KVKK: `FULL_NM` API katmanında maskelenir (`_mask_full_nm`), düz PII asla
  client'a inmez.

## 3. Mevcut PRISMA deposits portu ile ilişki

`jobs/deposits_pipeline.py` + `jobs/deposits_dashboards.py` hattı **farklı bir
üründür**: NIM motorlarının SQL'e portu → `PRISMA_DEP_*`/`PRISMA_NP_*`
tabloları → 5 blok-tabanlı sunum manifest'i. Bu hat şunları veremez (blok
modelinin doğal sınırları): hücre drill modalları, hover-linked cell history,
bubble tarih slider'ı/play, Balance↔Customer metrik kaydırma, BSC overlay'i,
client-side merge/gruplama hafızası (`sharedDimMerges`), tarih slider'lı
tam-ekran etkileşimleri. Kullanıcı kararı gereği bu modül **kaynak SPA'yı
taşır**, blok sistemine çevirmez.

**Paylaşım kararı:** yeni modül `PRISMA_DEP_*` tablolarını DEĞİL, kaynağın
kendi 11 SQL'ini kullanır (birebir sayı garantisi; `PRISMA_DEP_*` pay/payda
agregasyonu SPA'nın satır-seviyesi ihtiyaçlarını — ör. `new_production_detail`
müşteri drill'i — karşılamıyor). İki hat aynı Oracle kaynak tablolarını okur;
çakışma yok.

## 4. Hedef mimari

### 4.1 Modül şekli — izole blueprint (deposit_panel kalıbı)

```
nim_panel/                        # yeni modül (isim önerisi; bkz. §8 Açık sorular)
├── __init__.py                   # nim_panel_bp = Blueprint("nim_panel", __name__,
│                                 #   template_folder="templates", static_folder="static")
├── routes.py                     # sayfa + tüm /api/* endpoint'leri (kaynaktan port)
├── engine/
│   ├── deposit_detail.py         # DepositDetailEngine + DailyDepositEngine (app.py'den çıkarılır)
│   ├── balance.py                # BalanceAnalysisEngine + DailyBalanceEngine
│   ├── tenor.py                  # TenorAnalysisEngine + DailyTenorEngine + SwapHedgeEngine
│   ├── weekly.py                 # WeeklyRollingsEngine
│   ├── np_agg.py                 # kaynaktan birebir
│   ├── outstanding_daily.py      # kaynaktan birebir
│   ├── sector_data.py            # kaynaktan birebir
│   └── common.py                 # _wavg, _convert_rate_series, _apply_demand_deposit,
│                                 # _build_bubble_charts, _cost_bubble_source,
│                                 # _rate_heatmap_seg_aum, _aum_numeric_key, _parse_* …
├── data_source.py                # load_dataframe(name, params) — dispatcher (bkz. 4.3)
├── queries/
│   ├── prod/*.sql                # 11 Oracle SQL (şema prefix parametrik)
│   └── dev/*.sql                 # SQLite aynaları (kaynaktan birebir)
├── data/dev.db + seed_dev_db.py  # lokal geliştirme (kaynaktan)
├── templates/nim_panel/
│   └── index.html                # kaynak SPA'nın deposit-only kırpımı
└── static/
    ├── nim_panel.css             # index.html'den çıkarılan CSS
    └── nim_panel.js              # index.html'den çıkarılan JS (?v= cache-busting)
```

Kayıt: `app.py` → `app.register_blueprint(nim_panel_bp, url_prefix="/nim-panel")`.
Auth: her route `@login_required`, kullanıcı kimliği `current_user.sicil`.
Tüm iç linkler/fetch URL'leri `url_for` ile (OpenShift `SCRIPT_NAME` uyumu);
fetch tabanları template'te tek `<script type="application/json">` payload'ıyla
gömülür (keşif atölyesi deseni, `routes_kesif.py:_build_workbench_payload`).

### 4.2 Kabuk ve masa görünürlüğü

- Sayfa `home/_base_prisma.html`'i extend eder: `mode=consumer`,
  `canvas_bleed=True`, `no_atolye_sidebar=True` (editor.html/kesif.html
  deseni). Kaynak SPA'nın **kendi iç sidebar'ı** (7 deposit sayfası) korunur;
  PRISMA topbar üstte kalır, "masaya dön" doğal olarak topbar'dan.
- Kaynak SPA'nın kendi tema toggle'ı ve `body.light-mode` mekanizması yerine
  PRISMA'nın `<html data-theme>` + localStorage `prisma-theme` mekanizmasına
  bağlanır (tek toggle; `themeChange` event köprüsü `sweepPlotly`/`sweepApex`'i
  tetikler). Yeni renk token'ı tanımlanmaz; kaynak CSS değişkenleri
  `--editor-*`/`--gold`/`--ink` token'larına map edilir.
- **Masa:** `prisma_home/templates/home/landing.html` (footer producer
  linkleri bölgesi, ~:108-114) yeni bir kart/link: "NIM Paneli — Mevduat".
  Gerekirse `prisma_nav.json` R grubuna da link (legacy sayfalardan erişim).
- Erişim kısıtı gerekiyorsa `app.py::ROUTE_ACCESS_MAP`'e departman kuralı.

### 4.3 Veri katmanı

Kaynaktaki `engine/db_source.py::load_dataframe(name, params)` imzası korunur,
iç yönlendirme treasury-report'a uyarlanır:

- **PROD:** `current_app.config["DATA_CLIENT"]` (dc) üzerinden Oracle.
  `jobs/deposits_pipeline.py`'nin kullandığı `dc.edw_query_to_pandas(con, sql,
  params)` yolu birebir uyar (named bind `:NAME` destekli). Route'lar Oracle'ı
  doğrudan çağırmaz — tüm sorgular `data_source.py`'de.
- **DEV:** `nim_panel/data/dev.db` (SQLite) + `queries/dev/` — kaynaktaki
  düzen aynen taşınır; `run_local` akışında ekstra stub gerekmez.
- Şema prefix'i (`A16438.`) SQL'lerde hardcoded kalmaz → `{schema}` template
  veya config anahtarı (`NIM_PANEL_ORACLE_SCHEMA`).
- **Cache:** kaynağın process-lifetime engine cache'leri korunur (dict +
  snapshot deseni; ağır iş kilit dışında, atomik referans swap kilit içinde —
  CLAUDE.md threading kuralı). `_prewarm_deposit_caches()` startup'ta DEĞİL,
  config bayrağıyla (`NIM_PANEL_PREWARM=0/1`) arka plan thread'inde veya ilk
  istekte lazy koşar — pod açılışını bloklamaz.

### 4.4 Frontend taşıma stratejisi

**React'e/blok sistemine çevirme YOK** — kaynak SPA (vanilla JS) kırpılıp
taşınır. Gerekçe: 15.7k satırlık, davranışı ince ayarlanmış (redesign raporu
+ 50 test), build-step'siz bir SPA'yı yeniden yazmak "birebir fonksiyonalite"
hedefinin en büyük riski olur. esbuild bundle'ı gerekmez; JS ayrı statik
dosyaya çıkarılır, `?v=` cache-busting uygulanır.

Kırpım kuralları:

- `#grp-nii` sidebar grubu, NII sayfaları, `DF_CACHE`/`DATES_CACHE`,
  `NIMDecompositionEngine`/`NIMChartBuilder`/`hierarchy.py`/`bs_evolution.py`/
  `positions.py` **taşınmaz** (Faz B'ye kalır).
- Realized NII içindeki "Deposit Detail" üst-sekmesi (`dd-*` prefix)
  taşınmaz — Cost Analysis zaten aynı içeriği veriyor.
- BSC Presentation taşınır ama yalnız deposit/sektör slide'ları; NII
  slide'larına bağımlı DOM taşıma adımları Faz B'ye kadar devre dışı.
- Paylaşılan render/tema yardımcıları (`renderFig`, `renderPlotlyFig`,
  `renderWaterfall`, `renderBarChart*`, `initChartFullscreen`,
  `_plotInk`, `_ordinalRamp`, `_PRISMA_CAT`, `sweepPlotly`, `sweepApex`,
  `_renderBubbles`, `_smartBubbleLabels`) modülün kendi JS'ine kopyalanır —
  presentations bundle'ına dokunulmaz.

**Kütüphane yükleme** (CDN kuralı: `@` YOK → cdnjs.cloudflare.com):

| Kütüphane | Kaynak | Not |
|---|---|---|
| Plotly.js | cdnjs `plotly.js/…/plotly.min.js` | Repoya YENİ giriyor; yalnız bu modüle scoped. |
| ApexCharts | cdnjs `apexcharts/…` | Legacy sayfalar zaten CDN'den yüklüyor. |
| AG Grid Community 31.3.x | cdnjs `ag-grid/…` | JS + tema CSS; editor.html'in jsdelivr `@`'li deseni DEĞİL, cdnjs. |

CDN erişimi ofis ortamında sorunluysa fallback: minified dosyalar
`nim_panel/static/vendor/` altına vendorlanır (üçü toplam ~5 MB, git'te
taşınabilir; bundle politikasına aykırı değil çünkü build gerektirmez).

## 5. Kilit kararlar

| # | Karar | Gerekçe |
|---|-------|---------|
| K1 | İzole blueprint, presentations'a route eklenmez | Süreç akış-dışı; presentations'ın manifest/oturum kavramlarıyla bağı yok. `deposit_panel` kalıbı kanıtlı. |
| K2 | Kaynak SQL'ler kullanılır, `PRISMA_DEP_*` değil | Birebir sayı; satır-seviyesi drill ihtiyacı; pay/payda agregasyonu SPA sözleşmesiyle uyumsuz. |
| K3 | Vanilla SPA taşınır, React'e çevrilmez | 1:1 fonksiyonalite hedefi; yeniden yazım riski/maliyeti; kaynak zaten PRISMA görsel dilinde. |
| K4 | Plotly bu modüle scoped olarak kabul edilir | Bubble/heatmap/ladder/curve etkileşimleri Apex'te birebir yok; çevirme "birebir" hedefini bozar. Editör tarafı ApexCharts'ta kalır. |
| K5 | Tema PRISMA `data-theme` mekanizmasına köprülenir | İki ayrı tema toggle'ı kafa karıştırır; kaynak zaten dark/light çift destekli. |
| K6 | Prewarm lazy/arka plan, config bayraklı | Çok-worker OpenShift pod'unda startup bloklamamalı. |
| K7 | Engine'ler `app.py`'den modül dosyalarına ayrıştırılır ama **hesap mantığına dokunulmaz** | Kaynakta satır-referanslı port disiplini var (`jobs/deposits_pipeline.py` örneği); aynı disiplin: her fonksiyon başına kaynak `app.py:satır` yorumu. |
| K8 | KVKK maskesi API katmanında korunur | Platform kuralı (CLAUDE.md): düz PII asla depolanmaz/servis edilmez. |
| K9 | JSON serileştirme kaynaktaki gibi `json.dumps(..., cls=PlotlyJSONEncoder)` | Plotly figürleri + Timestamp'ler için şart; `jsonify` NaN tuzağına da girmez. Plotly'siz saf DataFrame endpoint'i eklenirse `df.to_json(orient="records")`. |
| K10 | Sektör sayfaları (6-7) deposit kapsamına dahil | Sektör query'lerini kullanan tek sayfalar; BSC slide 4 NP verisine bağımlı; deposit hikâyesinin parçası. |

## 6. Faz planı (granüler — her faz deploy edilebilir biter)

### Faz A0 — İskelet + kabuk (veri yok) — ✅ TAMAMLANDI
- `nim_panel/` blueprint iskeleti, `app.py`'de korumalı kayıt (`try/except` +
  `NIM_PANEL_ENABLED` bayrağı — modül yüklenemezse uygulama etkilenmez),
  `@login_required`, `/api/<path>` catch-all stub'ı (501 + `{ok:false}`).
- `index.html` deposit-only kırpımı `nim_panel/tools/` scriptleriyle yapıldı
  (tekrarlanabilir): template 15.7k → 1.5k satır, CSS/JS ayrı statik dosyalar,
  NII markup + boot bağlama kodu söküldü. Ölü NII fonksiyon gövdelerinin
  kalan temizliği fazlar ilerledikçe sürer (bkz. §8).
- Kütüphaneler CDN yerine **`static/vendor/` altına vendorlandı** (7.4 MB,
  npm'den; jsdelivr kurumsal/ağ politikasında engelli çıktı — plan sapması).
- Kabuk: `_base_prisma.html` extend EDİLMEDİ — tam izolasyon için SPA kendi
  tam-sayfa dokümanı olarak kaldı; PRISMA'ya köprüler: sidebar'da "← Masa"
  linki + `prisma-theme` localStorage tema köprüsü (plan sapması, K1 gereği).
- Masa kartı: `landing.html`'e `NIM_PANEL_ENABLED` korumalı "Panolar" bölümü.
- Doğrulama: headless Chromium — boot 0 hata, 7 sayfa navigasyonu + BSC
  overlay + dark/light tema çalışıyor; stub hataları SPA'nın kendi hata
  banner'ında zarifçe görünüyor.

### Faz A1 — Veri katmanı — ✅ TAMAMLANDI (revize)
- Kullanıcı kararı: **dev.db yok, doğrudan prod.** `data_source.py` tek yol:
  DataClient havuzu (`get_connection_from_pool` + `edw_query_to_pandas`).
  Kaynak `load_dataframe(name, params)` imzası korunur; testler monkeypatch'ler.
- 12 prod SQL `nim_panel/queries/`'e birebir taşındı; `A16438.` prefix'i repo
  konvansiyonuyla (queries/deposits/) tutarlı, parametrize EDİLMEDİ.
- Yeni pip bağımlılığı: `plotly` (yalnız `PlotlyJSONEncoder` + figür dict'leri;
  requirements.txt'e eklendi).

### Faz A2+A3 — Outstanding üçlüsü (Cost + Balance + Tenor) — ✅ TAMAMLANDI (birleştirildi)
- Heatmap/drill endpoint'leri üç sayfanın ortak fabrikası çıktığı için A2 ve
  A3 tek fazda taşındı. `nim_panel/tools/extract_a2.py` kaynak `app.py`'den
  satır-referanslı birebir çıkarır: `engine/common.py` (yardımcılar, bubble/
  heatmap kurucuları), `engine/chart_builder.py` (NIMChartBuilder; NII-özel
  `build_all` kırpıldı), `engine/outstanding.py` (6 motor sınıfı + payload
  kurucular — kaynaktaki çapraz referanslar nedeniyle tek modül),
  `request_params.py`, `routes_cost.py` (9 endpoint), `routes_outstanding.py`
  (7 endpoint).
- Doğrulama: kaynak dev.db'yi monkeypatch'le besleyen harness'ta 10 ağır
  endpoint `ok:true` + headless Chromium'da üç sayfa gerçek chart render
  ediyor (cost monthly: 17 Plotly + 4 Apex figür; 0 pageerror).
  `nim_panel/tests/` 12 birim testi yeşil.

### Faz A4 — Future Deposit Rollings — ✅ TAMAMLANDI
- `engine/weekly.py` (WeeklyRollingsEngine + `_mask_full_nm` KVKK maskesi),
  `routes_weekly.py` (3 endpoint + WEEKLY_CACHE'ler). DD/MM/YYYY bind prod
  yolunda aynen geçer (Oracle TO_DATE). Doğrulama: dev.db harness'ında
  6 AG Grid + 351 satır render, 0 pageerror.

### Faz A5 — New Business — Volume & Pricing — ✅ TAMAMLANDI
- `engine/np_agg.py` + `engine/outstanding_daily.py` kaynak dosyalardan
  birebir (yalnız db_source→data_source); `routes_np.py` 10 endpoint +
  NP detail lazy-master katmanı (`_NP_DETAIL_MASTER_LOCK` + prewarm).
- `bsc_np_rate_series` + `bsc_np_monthly_table` sector_data'ya bağımlı —
  Faz A6 ile gelir (route dosyasında port notu var).
- Doğrulama: 11 endpoint dev.db'yle `ok:true` (51KB müşteri drilldown dahil).

### Faz A6 — Sector Comparison + BSC Presentation — ✅ TAMAMLANDI
- `engine/sector_data.py` kaynak dosyadan birebir (1622 satır — kümülatif
  gider, vintage TCMB(BDDK Mix), mix attribution; yalnız db_source→
  data_source ve paket-içi lazy import değişimi); `routes_sector.py`
  8 sektör + 2 BSC NP endpoint'i.
- Doğrulama: 10/10 endpoint dev.db harness'ında `ok:true`; Sector sayfası
  12 AG Grid + 266 satır + 7 grafik render; BSC overlay açılıyor; 0 pageerror.

### Faz A7 — Cila + üretim hazırlığı — KISMEN (kalan işler ofis gerektirir)
- ✅ Prewarm: `nim_panel/prewarm.py` — `NIM_PANEL_PREWARM=1` ortam
  değişkeniyle daemon thread'de cache ısıtma (varsayılan kapalı, lazy).
- ✅ Vendor kararı: kütüphaneler `static/vendor/`'da (CDN'siz).
- ✅ Ölü NII fonksiyon süpürmesi: `nim_panel/tools/sweep_nii_dead.py` —
  acorn AST çağrı-grafiği analiziyle (giriş noktaları: top-level kod +
  index.html + `window.*` atamaları + string literalleri; fixed-point)
  erişilemeyen 59 fonksiyon / 52 top-level span / ~1.4k satır silindi
  (sim/cross/BSE/dd-/Raw Data/refreshDates/setDataSource + kaynakta da
  ölü olan `_wr*` helper'ları). Plan §8 paylaşılan helper'ları analizde
  canlı doğrulandı. Doğrulama: `node --check` + DEV headless tur (7 sayfa
  navigasyonu + tema toggle, 0 JS hatası) + 12 birim testi yeşil.
- Kalan (ofis makinesi / kullanıcı kararı gerektirir):
  - Oracle smoke: gerçek DataClient ile 40 endpoint'in ilk koşumu ve dtype
    karşılaştırması (özellikle DATE/NUMBER kolonları — `edw_query_to_pandas`
    vs kaynak `cursor.fetchall` farkı).
  - Görsel tur: gerçek veriyle 7 sayfa + drill modalları + BSC (deposit-only
    modda slide seti) + tema geçişleri.
  - İsteğe bağlı: `ROUTE_ACCESS_MAP` departman kuralı, `prisma_nav.json`
    linki, `docs/BACKEND_NIM_PANEL.md`.
  - Ölü NII fonksiyon gövdelerinin JS'ten toplu süpürülmesi (bkz. §8).

### Faz B — NII tarafı (kapsam dışı, ayrı planlanacak)
- Scenario/Cross-Scenario/Results Comparison + BS Evolution + BSC'nin NII
  slide'ları. ALM pickle bağımlılığı nedeniyle ayrı değerlendirme gerekir.

## 7. Test stratejisi

1. **Kaynak-eşdeğerlik (en önemlisi):** kaynak repo testleri
   (`test_weekly_rollings.py`, `test_np_rate_conversion.py`,
   `test_outstanding_daily.py`) `nim_panel/tests/`'e taşınır ve yeşil tutulur.
2. **Endpoint snapshot:** dev.db sabit olduğundan kaynak uygulamanın endpoint
   yanıtları fixture olarak dondurulur; port aynı istekte sayı-sayı aynı
   yanıtı vermek zorunda (kaynağın `tests/snapshots/` disiplini).
3. **Semantik regresyon (platform deseni):** `test_deposits_dashboards_cost.py`
   reçetesi — sentetik DataFrame → DuckDB/SQLite → port edilen formül vs
   bağımsız pandas referansı, `abs(got-want)<tolerans`.
4. **Yapısal:** blueprint kayıt/smoke, auth zorunluluğu, KVKK maskesinin
   detail endpoint'lerinde aktif olduğu, `url_for` tabanlı fetch payload'ı.

## 8. Riskler ve açık sorular

| Konu | Durum |
|------|-------|
| **Modül/sayfa adı** | Öneri: `nim_panel` / "NIM Paneli". Kullanıcı onayı bekliyor (alternatif: `mevduat_panel`, `terminal`). |
| **Plotly CDN erişimi (ofis proxy)** | cdnjs `@`'siz; yine de ofiste doğrulanmalı. Fallback: vendor dosyaları git'te (~5 MB). |
| **dev.db lisans/boyut** | 8.9 MB SQLite git'e girecek (kaynakta da commitli). Sentetik + maskeli; PII yok. Onay gerekli. |
| **`oracledb` fetch farkı** | Kaynak `cursor.execute+fetchall` kullanıyor (pandas 2.0 uyumu); DataClient'ın `edw_query_to_pandas`'ı dtype davranışını değiştirirse DATE/NUMBER kolonlarında sapma olabilir → Faz A1'de dtype karşılaştırma testi. |
| **Çok-worker cache tutarlılığı** | Engine cache'leri worker-lokal; veri güncellemesi "restart şart" (kaynakla aynı sözleşme). Kabul edilebilir mi? |
| **BSC'nin NII slide'ları** | Faz B'ye kadar eksik — BSC deposit-only modda açılır. |
| **Kaynak repo canlı gelişiyor** | Son commit 20 Tem 2026. Port sırasında bs_evolution5'e gelen commit'ler için taşıma sonunda tek diff turu planlanmalı (`nim_panel/tools/` bunu tekrarlanabilir kılar). |
| **Ölü NII fonksiyon gövdeleri** | ✅ ÇÖZÜLDÜ (A7): `tools/sweep_nii_dead.py` AST çağrı-grafiği analiziyle 59 ölü fonksiyonu süpürdü; paylaşılan helper'lar (`renderFig`, `renderWaterfall`, `sweepPlotly/Apex`, `initChartFullscreen`, bubble helpers) canlı doğrulandı. `transform_a0.py` dosyayı yeniden üretirse span'lar bayatlar — araç bu durumda hata verir, analiz turu tekrarlanmalı. |
