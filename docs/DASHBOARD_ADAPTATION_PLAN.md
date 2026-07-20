# Dashboard Adaptation — NIM_calculation → PRISMA Entegrasyon Planı

> Branch: `dashboard_adaptation` · Kaynak: `doguctan/NIM_calculation@bs_evolution5` (c569ae3)
> Durum: PLAN — Faz A0 başlamadı. Bu doküman kaynak repo envanteri + treasury-report
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

### Faz A0 — İskelet + kabuk (veri yok)
- `nim_panel/` blueprint iskeleti, `app.py` kaydı, `@login_required`.
- `index.html` deposit-only kırpımı: CSS/JS ayrı dosyalara çıkarılır, NII
  kalıntıları temizlenir, sayfalar boş-state render olur (fetch'ler 404'e
  düşse de sayfa kurulur).
- CDN yüklemeleri (cdnjs), PRISMA tema köprüsü, `_base_prisma.html` kabuk.
- Masa kartı: `landing.html`'e link.
- Çıktı: `/nim-panel` açılıyor, 7 sayfa navigasyonu ve tema çalışıyor.

### Faz A1 — Veri katmanı
- `data_source.py` dispatcher (DEV=SQLite / PROD=DataClient), `queries/dev|prod`
  taşınır, şema prefix parametrik, `data/dev.db` + `seed_dev_db.py` taşınır.
- Çıktı: `load_dataframe("daily_deposit")` dev'de DataFrame dönüyor; birim test.

### Faz A2 — Outstanding Cost Analysis (ilk dikey dilim)
- `engine/deposit_detail.py` + `engine/common.py` portu; endpoint'ler:
  `deposit_detail_dates/waterfalls`, `daily_deposit_dates/waterfalls`,
  `cost_rate_heatmap`, `hm_product_bar`, `deposit_product_daily`,
  `bubble_series`, `rate_drill`.
- Frontend cost sayfası uçtan uca: waterfall carousel, bubble + slider/play,
  heatmap + drill, Rate Type + Apply Demand Effect.
- Çıktı: Cost sayfası dev.db ile birebir çalışıyor; kaynak repo ile endpoint
  yanıtı snapshot karşılaştırması yeşil (bkz. §7).

### Faz A3 — Balance + Tenor
- `engine/balance.py`, `engine/tenor.py` (+ SwapHedge overlay), endpoint'ler,
  iki sayfa uçtan uca (KPI strip, bridge, Balance↔Customer slider heatmap,
  composition, maturity ladder, term structure, WAT sparkline, TENOR/DTM).

### Faz A4 — Future Deposit Rollings
- `engine/weekly.py`, 3 endpoint, AG Grid pivotları, segment slide'ı,
  drill modal, KVKK maskesi, DD/MM/YYYY bind akışı.

### Faz A5 — New Business — Volume & Pricing
- `engine/np_agg.py` + `engine/outstanding_daily.py` birebir; 11 `/api/np/*`
  endpoint'i; bubble, Rate×Volume heatmap + hover cell-history + drill modal,
  AUM combo, konsantrasyon eğrisi; NP detail lazy cache (`detail_prewarm`).

### Faz A6 — Sector Comparison + BSC Presentation
- `engine/sector_data.py` birebir (en riskli hesap kütlesi — kümülatif gider,
  vintage, mix attribution); 10+ sektör endpoint'i; Sector sayfası 7 kart;
  BSC overlay (yalnız deposit/sektör slide'ları).

### Faz A7 — Cila + üretim hazırlığı
- Prewarm bayrağı + arka plan ısıtma; hata/boş-state UX; erişim kuralı
  (`ROUTE_ACCESS_MAP`); CDN→vendor fallback kararı; ofis makinesinde Oracle
  smoke testi; `prisma_nav.json` linki; dokümantasyon (`docs/BACKEND_NIM_PANEL.md`).

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
| **Kaynak repo canlı gelişiyor** | Son commit 20 Tem 2026. Port sırasında bs_evolution5'e gelen commit'ler için taşıma sonunda tek diff turu planlanmalı. |
