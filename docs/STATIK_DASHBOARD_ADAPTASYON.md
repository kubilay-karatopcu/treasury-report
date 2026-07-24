# Statik Dashboard Adaptasyonu — Legacy sayfalar → PRISMA süreçleri

> Branch: `claude/treasury-dashboard-static-pages-pmjge3`
> Durum: **Faz S0 (backend temeli) başladı.**
> İlişki: `docs/DASHBOARD_ADAPTATION_PLAN.md` (mevduat_panel / NIM portu) ile
> aynı modül içinde yaşar; bu doküman o modüle eklenen **legacy statik
> dashboard'ların** adaptasyonunu kapsar.

---

## 1. Amaç

PRISMA'ya geçmeden önceki üretim uygulamasındaki (`app.py`, kullanıcı tarafından
sağlandı) legacy dashboard'ları **güncel PRISMA backend + design sistemine**
adapte etmek ve uzman altında **süreç** olarak yayınlamak.

Kapsamdaki sayfalar (kullanıcı gruplaması):

| Grup | Legacy route | İçerik |
|------|--------------|--------|
| **Mevduat Verileri** | `/oranlar` | Rezervasyon bazlı teklif/talep faiz oranları (günlük) |
| | `/miktarlar` | Rezervasyon tutarları / portföy (günlük) |
| | `/historic` | Son 30 gün tarihsel görünüm |
| **Sektör Verileri** | `/competitor` | Rakip banka mevduat faiz analizi (+ LLM piyasa özeti) |
| **Uygulamalar** | `/deposit-panel/params`, `/deposit-panel/reservations`, `/deposit-assistant` | Fiyatlama param editörü + asistan (AYRI modüller) |

---

## 2. Kilit kararlar (kullanıcı onaylı — 2026-07-24)

| # | Karar | Gerekçe |
|---|-------|---------|
| **S1** | 4 veri sayfası (oranlar/miktarlar/historic/competitor) **`mevduat_panel` modülüne** eklenir | Hepsi mevduat verisi; ayrı modül refresh/ETL kod tekrarı yaratır. Sektör zaten mevduat_panel'de (Sector Comparison). |
| **S2** | **Uygulamalar ayrı yerde kalır** — `deposit_panel` + `deposit` kendi blueprint'leri; mevduat_panel'e girmez | Fonksiyon farkı (param editörü/asistan), zaten modülleri var; sadece register + süreç bağlama gerekir. |
| **S3** | Süreçlerin tamamı **Mevduat Uzmanı (`dep`)** altına bağlanır | Tek uzman var; mevduat_panel sektörü zaten dep altında — tutarlı. Topic gruplarıyla ayrışır. |
| **S4** | Frontend **PRISMA-native yeniden kurulur** — `_base_prisma.html` shell, PRISMA token/CSS, `chartHelpers.js` ApexCharts konvansiyonları, PRISMA topbar/sidebar/tab kalıpları | "Eski siteyi güncele adapte" hedefi. Legacy JS'in birebir taşınması DEĞİL. |
| **S5** | Veri katmanı **(a)**: legacy'nin paylaşılan `current_df` + batch-refresh modeli mevduat_panel'in engine/data_source kalıbına taşınır | Kullanıcı kararı; legacy `DataOperations.process_data()` tek DataFrame üretir, mevduat_panel'in process-lifetime cache + `/admin/refresh` disiplinine doğal oturur. |

### İzolasyon sözleşmesi amendment (S4 gereği)

`docs/DASHBOARD_ADAPTATION_PLAN.md §4.2`'de mevduat_panel `_base_prisma.html`'i
**extend ETMİYORDU** (tam izolasyon için SPA kendi dokümanıydı). Yeni PRISMA-native
sayfalar için bu **template düzeyinde** gevşetilir:

- Yeni sayfalar `_base_prisma.html`'i **Jinja ChoiceLoader üzerinden extend eder**
  (template bağımlılığı — Python import DEĞİL).
- **Python izolasyonu korunur**: `mevduat_panel` hâlâ `prisma_home`/`presentations`
  modüllerinden hiçbir şey import etmez. Süreç kaydı `prisma_home/processes.py`'de
  string-endpoint (`mevduat_panel.*`) ile çözülür (mevcut sözleşme).
- Bu, Faz P'nin (PRISMA'ya homojenleştirme) doğal uzantısıdır.

---

## 3. Kaynak ETL (legacy `app.py`'den — otorite)

Legacy uygulama boot'ta **iki paylaşılan bellek DataFrame'i** kurar; tüm sayfalar
bunlardan okur (per-request Oracle YOK; tazeleme `/internal/refresh-data`):

### `current_df` ← `DataOperations.process_data()`

1. **MYU** — `myu.sql` (fallback `myu_T_CUST.sql`) → `A16438.STRATEGIC_DEP_PRCNG_CORE_RES` + `EDW.CUST` + `DEP_SMALL_APP_PARAMS`
2. **core_comparison** — `core_comparison.sql` (fallback `_T_CUST`), bind'ler `part_id`/`part_id_t_1`/`val_dt`/`mtrty_dt` (part_id = ay-sonu→`%m%Y`, değilse `gün×10`) → `EDW.TIME_DEPT_RESERVATION` ailesi
3. MYU+core concat → `DATE_TIME` = `CREATE_DT`+`CREATE_TM`, `DATA_SRC='MYU'`
4. **TREASURY** — `treasury.sql` (fallback `_T_CUST`) → `EDW.DEP_PRC_RSRV`; kolon rename (`RQSTD_INTRST_RT→DEMANDED_RATE`, `APPRVD_INTRST_RT→OFFERED_RATE`, `CURRENCY_CD→CCY_CODE`, `MTRTY_STRT→VADE_BASLANGIC`, `RSRVTN_DT→CREATE_DT`, `PRCNG_CNT→TALEP_REVIZE_NO`…), `DATA_SRC='TREASURY'`
5. concat → sort `DATE_TIME`
6. **IS_MAX_REVIZE** — grup `[DATA_SRC,CUST_ID,CREATE_DT,VADE_BASLANGIC]` içinde max `TALEP_REVIZE_NO` işaretlenir (`TALEP_REVIZE_NO` NaN→1)
7. **Temizlik** — `OFFERED_RATE` numeric; `RESERVATION_AMT ≥ 50000`; `OFFERED_RATE ≤ MARKET_MAX_RT×1.02`; %99 percentile outlier kırpma (`COMPETITOR_BANK_RTS→PERCENTILE_COMPETITOR_RTS`, `DEMANDED_RATE→PERCENTILE_DEMANDED_RTS`); `DATE_TIME` NaT drop
8. **JSON prep** — `DATE_STR_CLEAN` (`%Y-%m-%d`), `DATE_TIME_STR` (`%Y-%m-%d %H:%M:%S`)

### `competitor_df` ← `load_competitor_data()`

- `competitor_analysis.sql` → `A63837.COMPETITOR_ANALYSIS`
- `VADE`/`TUTAR` aralık parse → `VADE_MIN/MAX`, `TUTAR_MIN/MAX` (ör. "32-45 Gün" → 32,45)
- `TARIH` normalize → `DATE_STR`; `FAIZ` numeric; NaN drop

### Sayfa → veri akışı (legacy)

- `/oranlar`, `/miktarlar` → JS `GET /api/data/{oranlar|miktarlar}/<date>` → `current_df` tarih filtresi + kolon alt-kümesi
- `/historic` → son 30 gün `current_df` **inline** template'e gömülü
- `/competitor` → `competitor_df` inline; `POST /competitor/summary` → Qwen piyasa özeti

---

## 4. Hedef mimari (mevduat_panel içinde)

```
mevduat_panel/
├── queries/
│   ├── treasury.sql / treasury_T_CUST.sql          # ✅ taşındı (Faz S0)
│   ├── myu.sql / myu_T_CUST.sql                     # ✅
│   ├── core_comparison.sql / core_comparison_T_CUST.sql  # ✅
│   └── competitor_analysis.sql                      # ✅
├── engine/
│   └── reservation_data.py     # DataOperations + load_competitor_data portu
│                               #   load_reservation_df() / load_competitor_df()
│                               #   process-lifetime cache + reset_caches()
├── routes_reservations.py      # JSON API: oranlar/miktarlar/historic/competitor
│                               #   (+ competitor/summary LLM) — blueprint içi
├── templates/mevduat_panel/prisma/   # PRISMA-native sayfalar (Faz S1)
│   ├── rates.html / amounts.html / historic.html / competitor.html
└── static/js/prisma/           # PRISMA-native vanilla JS + chartHelpers uyumu (Faz S1)
```

Süreç kaydı (`prisma_home/processes.py` — modül dışı, string-endpoint):

| pid | label | endpoint | page |
|-----|-------|----------|------|
| `mevduat.oranlar` | Rezervasyon Oranları | `mevduat_panel.prisma_rates` | — |
| `mevduat.miktarlar` | Rezervasyon Miktarları | `mevduat_panel.prisma_amounts` | — |
| `mevduat.tarihsel` | Tarihsel Görünüm | `mevduat_panel.prisma_historic` | — |
| `mevduat.rakip` | Rakip Faiz Analizi | `mevduat_panel.prisma_competitor` | — |

`dep.yaml` → `bound_content.processes` + `department_views.topics`:
"Rezervasyon Verileri" (oranlar/miktarlar/tarihsel) + "Sektör & Rakip" (rakip).

---

## 5. Faz planı (her faz deploy edilebilir biter)

### Faz S0 — Backend veri temeli — 🚧 BAŞLADI
- ✅ 4 SQL (+`_T_CUST` fallback) `mevduat_panel/queries/`'e taşındı.
- `engine/reservation_data.py`: `load_reservation_df()` + `load_competitor_df()`
  (cache + `reset_caches()`), `data_source.load_dataframe` üzerinden; `_T_CUST`
  fallback try/except korunur.
- `routes_reservations.py`: JSON endpoint'leri (oranlar/miktarlar/historic/
  competitor veri) engine'den okur; `__init__.py`'de kayıt.
- `prewarm.py`: `reservation_data` warm + reset adımları.
- Test: `tests/test_reservation_data.py` — `load_dataframe` monkeypatch'li
  sentetik veriyle ETL semantiği (IS_MAX_REVIZE, filtreler, outlier, competitor
  parse).
- **DEV notu:** `queries/dev/` aynaları + dev.db seed'i YOK (bu tablolar
  sentetik dev.db'de bulunmaz). DEV yolu bu sorgular için ofiste seed gerektirir;
  testler `load_dataframe`'i monkeypatch'ler (mevduat_panel test disiplini).

### Faz S1 — PRISMA-native sayfalar
- 4 sayfa `_base_prisma.html` extend eder; PRISMA topbar/sidebar/tab, token CSS.
- Chart'lar ApexCharts (`chartHelpers.js` konvansiyonları); tarih seçimi tek
  PRISMA kalıbı (litepicker yerine PRISMA date control).
- `competitor` AG Grid yerine PRISMA `data_table`/AG Grid Community teması.
- Her sayfa kendi endpoint'i (`mevduat_panel.prisma_rates` vb.).

### Faz S2 — Süreç + uzman bağlama
- `PROCESS_REGISTRY`'ye 4 entry (documentation + blocks descriptor'ları).
- `dep.yaml`'a process id'leri + `department_views` topic grupları.
- `config_flag = MEVDUAT_PANEL_ENABLED` (mevcut) — modül kapalıysa süreç gizli.

### Faz S3 — Competitor LLM özeti + cila
- `competitor/summary` → config'ten LLM (`PRESENTATIONS_LLM_*` veya
  `MEVDUAT_PANEL_LLM_*`), hardcoded token DEĞİL.
- Refresh: `current_df`/`competitor_df` `admin/refresh` akışına bağlanır.

### Faz S4 — Uygulamalar (ayrı)
- `deposit_panel` blueprint'i app.py'de register (bugün edilmemiş).
- `deposit_panel`/`deposit` süreç olarak `dep` altında "Uygulamalar" topic'inde.
- Bu grup mevduat_panel'e GİRMEZ (S2 kararı).

---

## 6. Test stratejisi

1. **ETL semantik regresyonu** (platform deseni): sentetik DataFrame →
   port edilen `process_data` mantığı vs bağımsız pandas referansı; `load_dataframe`
   monkeypatch'lenir. Kapsam: concat, IS_MAX_REVIZE, iki filtre, %99 outlier,
   competitor aralık parse.
2. **Yapısal:** blueprint endpoint kaydı, auth zorunluluğu, boş-veri zarafeti.
3. **Ofis (gerçek Oracle):** ilk koşumda dtype + satır sayısı legacy ile
   karşılaştırma (özellikle `CREATE_TM` string→DATE_TIME parse).

## 7. Açık sorular / riskler

| Konu | Durum |
|------|-------|
| DEV seed | `current_df` tabloları sentetik dev.db'de yok → DEV'de bu sayfalar ofis seed'i gerektirir. Testler monkeypatch'ler. |
| `core_comparison` bind'leri | Prod SQL `:val_dt` kullanır; `part_id`/`part_id_t_1`/`mtrty_dt` fazla bind güvenli (python-oracledb kullanılmayan named bind'leri yok sayar). Ofiste doğrulanmalı. |
| LLM config | Legacy hardcoded token; S3'te config'e taşınır. |
| Refresh sahipliği | `current_df` process-lifetime; çok-worker'da tazeleme = tüm worker'lar restart/refresh (mevduat_panel ile aynı sözleşme). |
