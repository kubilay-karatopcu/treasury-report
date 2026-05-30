# Treasury Studio — Tablolar Katmanı (Anlatımlı Doküman)

> Bu doküman **tabloların** kodda niye öyle tasarlandığını, neyle neyi
> çözdüğünü ve nasıl birbirine bağlandığını anlatır. Bu bir **entity**
> dokümanıdır: "bir tablo nedir, backend'i nasıl çalışır?" Akışların
> (Keşif / Hazırlık / Sunum) uçtan-uca anlatımı **ayrı stage doküman­larında**
> yapılır; burada tablonun o akışlardaki rolü özetlenir ve oraya pointer
> verilir (§8). Oturup okuyarak konuyu kavraman içindir, madde referansı değil.

---

## Önsöz — Neden bu kadar şey var?

Bir kullanıcı sohbet kutusuna "son 30 günün mevduat dağılımını şube
bazında göster" yazdığını hayal et. Sistem bu cümleyi okuyup bir Oracle
sorgusuna çevirebilmek için çok şey **önceden** bilmek zorunda:

- Hangi tablo "mevduat" demek? `EDW.DEPOSITS_DAILY` mi, `HIST.DEPOSITS_HIST`
  mi, `CDM.ACCOUNT` mi?
- O tabloda "şube" hangi kolon? `BRANCH_CODE` mu, `BRANCH_ID` mi, `BR_CD` mi?
- "Son 30 gün" o tabloda hangi kolon üzerinden filtrelenir?
- `BRANCH_CODE = '01234'` yerine kullanıcıya **"Çankaya şubesi"** yazısı
  nasıl gösterilir? Bunun için başka bir tabloyla JOIN gerekir mi?
- Kullanıcı "segmenti RETAIL olanlar" derse, RETAIL bu tabloda gerçekten
  **`'RETAIL'`** mi yazıyor, yoksa `'BIREYSEL'`/`'CORP'` gibi farklı bir
  kodla mı saklanıyor?

LLM tek başına bunları **bilemez**. Tabloyu hiç görmemiştir. Dolayısıyla
sisteme üç şey sağlanmalı:

1. **Tablonun ne işe yaradığının insan-okunabilir anlatımı.**
   ("Günlük mevduat bakiye snapshot'ı, ETL gün sonunda doldurur.")
2. **Her kolonun ne anlama geldiği + nasıl kullanılması gerektiği.**
   ("`SEGMENT` kolonu müşteri segmenti — `RETAIL/CORP/SME` alır,
   filtrelenebilir, dimension rolünde.")
3. **Ortak kavramların farklı tablolarda farklı kolonlara nasıl bağlandığı.**
   ("Şube DEPOSITS_DAILY'de `BRANCH_CODE`, DIM_BRANCH'ta `BRANCH_ID`.")

Tablolar katmanı tam olarak bu üç bilgiyi düzenli tutar ve sistemin
gerekli yerlerinde sunar.

---

## 1. Bir tablonun üç katmanı

Treasury Studio kafasında bir "tablo" şudur:

```
┌─────────────────────────────────────────────────────────┐
│  1. FİZİKSEL ORACLE TABLOSU                              │
│     EDW.DEPOSITS_DAILY — 250.000 satır/gün, ETL ile dolar │
└─────────────────────────────────────────────────────────┘
           ▲  "ne işe yarar?" "hangi kolonlar?"
┌─────────────────────────────────────────────────────────┐
│  2. TableDoc — examples/table_docs/EDW/DEPOSITS_DAILY.yaml │
│     • description • partition_column • columns{metadata}   │
└─────────────────────────────────────────────────────────┘
           ▲  "bu kolon hangi 'kavram'a karşılık geliyor?"
┌─────────────────────────────────────────────────────────┐
│  3. Concept Binding —                                      │
│     presentations/catalog/tables/EDW/DEPOSITS_DAILY.yaml   │
│     • concept: branch → column: BRANCH_CODE                │
│     • concept: as_of_time → column: DATE                   │
│     • concept: segment → column: SEGMENT (map)             │
└─────────────────────────────────────────────────────────┘
```

İlk katmanı kontrol etmiyoruz — o ETL ekibinin işi. İkinci ve üçüncü
katmanı **biz** doldururuz; bu iki YAML dosyası, sistem genelinde
tablonun her kullanımının dayanağıdır.

İki YAML'ı **niye ayrı tuttuk**? Çünkü ikisi farklı türde bilgi taşır:

- **TableDoc** kolon-merkezlidir: "Bu tabloda hangi kolonlar var, her
  birinin tipi nedir, ne anlama gelir?" Data engineer'lar yazar.
- **Concept binding** kavramsal bir köprüdür: "Sistem genelinde 'şube' diye
  bir kavramım var, bu tabloda ona karşılık gelen kolon hangisi, içerikleri
  kanonik kodlarımla nasıl eşleşiyor?"

Bir tablonun kolonlarını değiştirmek (yeni kolon, açıklama düzeltme) ile o
tablonun konsepte nasıl bağlandığını değiştirmek **ayrı işlemler**.
Karıştırırsak biri diğerini bozabilir. Bu yüzden iki dosya.

### Somut örnek

`examples/table_docs/EDW/DEPOSITS_DAILY.yaml` (tabloyu **tarif eder**):

```yaml
table: DEPOSITS_DAILY
schema: EDW
description: |
  Günlük mevduat bakiye snapshot'ı. ETL gün sonunda doldurur; T+1 09:00.
partition_column: DATE
estimated_daily_rows: 250000
columns:
  BRANCH_CODE:
    type: VARCHAR2(10)
    description: Şube kodu
    filterable: true
    filter_role: dimension
    suggested_variable: branch_code
    suggested_semantic_tag: branch
  SEGMENT:
    type: VARCHAR2(20)
    description: "Müşteri segmenti (RETAIL, CORP, SME)"
    filterable: true
    filter_role: dimension
    suggested_variable: segment
    suggested_semantic_tag: segment
  DATE:
    type: DATE
    filterable: true
    filter_role: time_axis
    suggested_variable: as_of_date
    suggested_semantic_tag: as_of_time
  BALANCE_TRY:
    type: NUMBER(18,2)
    aggregatable: true
```

`presentations/catalog/tables/EDW/DEPOSITS_DAILY.yaml` (tabloyu
**anlamlandırır**):

```yaml
table: DEPOSITS_DAILY
schema: EDW
concept_bindings:
- concept: segment
  column: SEGMENT
  transform: {kind: map, pairs: {RETAIL: RETAIL, CORPORATE: CORP, SME: SME}}
  confidence: human_verified
- concept: as_of_time
  column: DATE
  transform: {kind: identity}
  confidence: human_verified
- concept: branch
  column: BRANCH_CODE
  transform: {kind: identity}
  confidence: human_verified
```

---

## 2. TableDoc — Kolonların kimlik kartı

Pydantic modeli `presentations/table_docs/schema.py`'de. Her alanın bir
nedeni var.

### Tablo seviyesinde

**`description`** — tablonun ne işe yaradığının insan-dilinde anlatımı.
Sadece UI'da değil, **LLM prompt'una da gider**. "Günlük mevduat bakiye
snapshot'ı" yazmıyorsa LLM kullanıcı "mevduat" dediğinde bu tabloyu seçemez.

**`partition_column`** — Oracle partition key (genelde tarih kolonu). İki
yerde işe yarar: (1) **Routing** — "son 30 gün" filtresi partition kolonuna
iniyorsa tahmini bytes `daily_rows × 30 × bytes_per_row` ile hesaplanır,
küçük çıkar → cached; aksi halde `daily_rows × 365` ile, büyük → lazy. (2)
**LLM hint'i** — `WHERE DATE >= …` tercih edilmesi gerektiğini bilir.

**`estimated_daily_rows`** — routing hesabının girdisi. ETL'in gün başına
yazdığı satır ortalaması; kaba bir büyüklük sırası (10K mı 10M mı?) verir.

### Kolon seviyesinde (`ColumnDoc`)

- **`type`** — `VARCHAR2(20)`, `NUMBER(18,2)`, `DATE`. LLM `WHERE rate > 0.5`
  yazabilmek için tipini bilmeli. Üretim için zorunlu.
- **`description`** — kolonun ne tuttuğu. Prompt'ta görünür; LLM hangi kolonu
  seçeceğine karar verir.
- **`filterable`** — dashboard'da filtre olabilir mi? `BALANCE_TRY` (ölçü)
  hayır, `SEGMENT` (boyut) evet.
- **`filter_role`** — `time_axis` (tarih) / `dimension` (kategori) /
  `measure_threshold` (eşik). UI input widget'ını buna göre seçer.
- **`suggested_variable`** — bind variable adı önerisi (`as_of_date`). Block
  SQL'inde `:as_of_date` olarak görünür.
- **`suggested_semantic_tag`** — bu kolon hangi anlamsal kavrama bağlı
  olmalı (öneri). Gerçek concept binding ayrı YAML'da (3. katman).
- **`lookup`** — kolonun başka tabloda anlamlı adı varsa
  (`BRANCH_CODE` → `DIM_BRANCH.BRANCH_NAME`). İki yerde: LLM display-label
  JOIN'i + Hazırlık join inference'ı (dashed çizgi önerisi).
- **`distinct_values_sample`** (≤50) + **`distinct_values_sampled_at`** —
  filter bar değerleri için nightly cron örneklemesi. 50 üstü typeahead.
- **`aggregatable`** — `SUM(...)` mantıklı mı? Metric vs kategori ayrımı.
- **`visible_in_ui`** — `false` → audit kolonları picker'da gizli; LLM görür.

### Schema validator'ın zorladığı invariant'lar

`load_table_doc_from_dict(raw)` (Pydantic sarmalayıcısı):

- `partition_column` mutlaka `columns` dict'inde olmalı.
- `filter_role` varsa `filterable=True` olmalı.
- `suggested_semantic_tag` varsa `suggested_variable` da olmalı.
- `distinct_values_sample` varsa `distinct_values_sampled_at` zorunlu.
- `semantic_tag` whitelist'te (`SEMANTIC_TAGS_V0`) olmalı; `"other"` escape.

Tüm modeller `extra="forbid"` — typo bir field sessizce kabul edilmez
(`descripiton:` yazsan Pydantic patlar). Schema drift erken yakalanır.

---

## 3. Concept binding — Tablolar arası ortak dil

Üç tablon var, "şube" üçünde farklı kolonda:

```
EDW.DEPOSITS_DAILY  — şube: BRANCH_CODE,  segment: SEGMENT
EDW.LOANS_DAILY     — şube: BR_CD,        segment: SEG
ODS_TREASURY.BONDS  — şube: BRANCH_ID,    segment yok
```

Kullanıcı **"şube: Çankaya, segment: RETAIL"** dedi. Sistem her üç tabloya
farklı uygulamak zorunda. Bu eşleştirmeyi her seferinde elle yazmak imkansız.
**Concept binding** bunu tablo başına bir kez yapar:

```yaml
concept_bindings:
- concept: branch
  column: BRANCH_CODE
  transform: {kind: identity}
- concept: segment
  column: SEGMENT
  transform: {kind: map, pairs: {RETAIL: RETAIL, CORPORATE: CORP, SME: SME}}
```

Concept'ler ortak bir vocabular gibi: tüm sistem `branch`'i bilir; her tablo
"ben de buna karşılık şu kolonumla katılıyorum" der.

### Transform — kanonik değer ↔ DB değeri (5 kind)

- **`identity`** — DB değeri = kanonik kod. `WHERE BRANCH_CODE = '01234'`.
- **`map`** — çevirim var. Kanonik `CORPORATE` ↔ DB `CORP`
  (`pairs: {CORPORATE: CORP}`). Kullanıcı `CORPORATE` seçer, sistem
  `WHERE SEGMENT='CORP'` yazar; tersine UI `CORP`'u `CORPORATE` gösterir.
- **`lookup`** — kanonik değer başka tablodaki kayda referans (JOIN).
- **`bucket_from_range`** — sürekli sayı → bucket. `high_income` →
  `WHERE INCOME >= 50000`.
- **`time_truncation`** — tarihi granularity'ye yuvarla.
  `TRUNC(DATE,'MONTH') = :month`.

### Confidence — yalnız insan-onaylı compiler'a gider

Binding'ler manuel (operator) ya da otomatik (Phase 7.c inference:
regex + dtype + LLM) oluşur. Otomatik öneriler `confidence: llm_proposed` /
`inferred_regex` damgalı kaydedilir ve **sistemde kullanılmaz** — review
queue'da bekler. Operator `/concepts/review`'da onaylarsa `human_verified`
olur. Filter compiler (`presentations/concepts/compiler.py`) **yalnız
human_verified** binding'leri okur. Sebep: yanlış binding → yanlış sayı →
güven kaybı; otomatik öneri fikir için iyi, üretim için insan onayı şart.

---

## 4. Catalog katmanı — İki YAML'ı birleştiren tek API

İki YAML tree'yi okuyan tek yer olmalı; yoksa her endpoint kendi walker'ını
yazar. `presentations/catalog/loader.py::CatalogLoader` bir kez okur, her
tablo için zengin bir `TableEntry` üretir:

```python
class TableEntry(BaseModel):
    schema_name: str          # alias "schema"
    name: str
    source: Literal["corporate", "user_upload"]
    description: str          # TableDoc'tan
    department: str           # DEFAULT_SCHEMA_DEPARTMENT_MAP'ten
    concepts_bound: list[str] # suggested_semantic_tag'ler
    concepts_unbound: list[str]
    row_count_estimate: int
    row_count_basis: Literal["daily", "total"]
    columns: list[ColumnSummary]
    lookups: list[LookupSummary]
    related_tables: list[str] # concept-paylaşan tablolar
```

**Department map** — `DEFAULT_SCHEMA_DEPARTMENT_MAP` her şemayı bir departmana
atar (EDW/ODS_TREASURY → treasury, ODS_RISK → risk, …). Keşif LLM önerisinde
kullanıcının departmanına ait tablolar +0.5 bonus alır.

**Cache (TTL ≈ 30s)** — bütün API endpoint'leri tek snapshot'ı paylaşır;
her chat'te 80+ YAML walk etmemek için. `?refresh=true` cache'i atlar.

| Endpoint | Ne yapar |
|---|---|
| `GET /catalog` | Tüm `TableEntry` + facets (`scope/q/dept/concept`). Atölye/Tablolar. |
| `GET /catalog/<schema>/<table>` | Detay (columns, lookups, related). |
| `GET /catalog/concept/<id>` | Concept hub: o concept'i taşıyan tablolar. |
| `GET /catalog/graph` | Bipartite graph payload (Keşif Cosmograph). |

`examples/sample_catalog.json` Phase 1 mirası; CatalogLoader **okumaz**
(Phase 9'da YAML-per-tablo'ya geçildi), eski testlerde duruyor.

---

## 5. Storage backends — Diskte vs Bulutta

`presentations/table_docs/store.py`:

- **`LocalTableDocStore`** — DEV. `base_dir/<SCHEMA>/<TABLE>.yaml`
  (`examples/table_docs/EDW/DEPOSITS_DAILY.yaml`).
- **`S3TableDocStore`** — Production. Aynı yapı S3'te; içeride bir
  `DataClient` sarmalar (`_upload_bytes/list_prefix/read_bytes`).
- **`CachedTableDocStore`** — diğer ikisini sarar, in-process dict cache.
  TableDoc yazma seyrek (operator), okuma çok (her LLM chat).

YAML round-trip: `to_yaml_shape()` (alias `schema`↔`schema_name` unroll) →
`yaml.safe_dump(allow_unicode=True, sort_keys=False, default_flow_style=False)`
(Türkçe korunur, sıra korunur, block style). Parse: UTF-8 → `safe_load` →
`load_table_doc_from_dict`. Exception'lar: `TableDocStoreError` (base),
`TableDocNotFoundError` (404).

---

## 6. Bootstrap → Production: Bir tablonun hayatı

Yeni bir Oracle tablosu (`EDW.NEW_DEPOSITS`) 4-5 aşamadan geçer:

**Aşama 1 — Bootstrap (`jobs/generate_table_docs.py` cron):** Oracle
metadata'sından (`ALL_TABLES`, `ALL_TAB_COLUMNS`, `ALL_*_COMMENTS`,
`ALL_PART_KEY_COLUMNS`) minimal bir TableDoc iskeleti üretir (table, schema,
description, partition_column, estimated_daily_rows, columns{type,
description}). `filterable / filter_role / suggested_semantic_tag / lookup`
**doldurulmaz** — Oracle bilmez, operator karar verir. `overwrite=False` →
varolan, manuel düzenlenmiş YAML'ları clobber etmez.

**Aşama 2 — Operator zenginleştirme:** `/presentations/atolye/tablolar/<S>/<T>`
form editörü (`tablo_edit.html`). Her kolon: filtrelenebilir mi, hangi rol,
hangi concept tag, lookup var mı. `POST .../api/save` → Pydantic validate → YAML.

**Aşama 3 — Distinct sampling (`jobs/sample_distinct_values.py` nightly):**
her `filterable + dimension` kolon için `SELECT DISTINCT … FETCH FIRST 50` →
`distinct_values_sample`. Pahalı sorgu, gün içinde tekrarlanmaz.

**Aşama 4 — Concept review queue:** inference pipeline binding önerir;
`/concepts/review`'da operator onaylar → `human_verified` →
`presentations/catalog/tables/<S>/<T>.yaml`.

**Aşama 5 — Üretime hazır:** Tablo artık Keşif LLM listesinde gözükür;
Hazırlık'ta join + concept önerileri sunar; **cached olarak seçilirse Sunum
için S3 parquet'e materialize edilir** (bkz. §7); Atölye/Tablolar'da listelenir;
distinct sample ile filter bar değerleri görünür.

---

## 7. Dataset katmanı — Tablonun verisi nasıl materialize olur

> **Mimari karar (2026-05):** Tüm SQL/tablo/kolon üretimi **Hazırlık**
> aşamasına taşındı; **Sunum** saf layout + görsel oldu. Sebep: aynı
> query'nin N grafik için N kez koşması ve **raporu izleyen kişinin Oracle'ı
> tetiklemesi** istenmiyordu. Çözüm: tablonun verisi bir kez **materialize**
> edilir, viewer onu okur.

Bir tablo sepete alınınca Hazırlık'ta bir **dataset**'e dönüşür (genişletilmiş
scope contract basket item'ı, `presentations/scope/schema.py::BasketItem`).
Bir dataset üç kaynaktan birinden gelir (tam olarak biri):

- **`table_ref`** — gerçek Oracle tablosu (projeksiyon + pinned filtre ile).
- **`sql`** — kullanıcı/LLM'in yazdığı serbest SELECT/WITH (manuel SQL tablo).
- **`derivation`** — başka alias'lardan türetilmiş: `aggregate` (GROUP BY) ya
  da `calculated` (JOIN + expr).

### Routing — cached mi, lazy mi?

`presentations/scope/routing.py::decide_routing` (pure, deterministik) tahmini
boyuta göre karar verir:

- **`cached`** (küçük/aggregate, < ~500 MB) → **S3 parquet'e materialize
  edilir, cron'lanabilir.**
- **`lazy`** (büyük ham tablo) → **Oracle'da kalır, on-demand çekilir,
  cron'lanamaz.** 10 GB üstü "force cached" reddedilir (RAM yetmez).

Operator override edebilir (`→ lazy` / `→ cached` butonu); kararı
`decided_by:"user"` olur ve sistem geri çevirmez. **Türetilmiş tablolar her
zaman cached'tir** (DuckDB agregatı, Oracle byte-estimate'i yok).

### Materialize — tek yazar: cron

`presentations/cache/dataset_scheduler.py::DatasetScheduler` (tek-pod daemon)
her scope'u tarar; `cached + refresh.kind=="scheduled"` olan dataset'leri
**due** olunca `presentations/scope/materialize.py::materialize_dataset` ile
parquet'ler. Dosya düzeni:

```
prisma-treasury/datasets/<pid>/<alias>/data.parquet
prisma-treasury/datasets/<pid>/<alias>/meta.json   # columns, row_count, refreshed_at
```

Kaynak tipine göre:

- `table_ref` → `compose_cached_sql` (projeksiyon + pushdown filtre) → Oracle.
- `sql` → whitelist (`validate_sql`, yalnız SELECT/WITH) sonrası Oracle.
- `derivation` → kaynak alias'larını **parquet'ten** (varsa, Oracle'sız) ya da
  yoksa in-memory çözer, `compile_aggregate_sql`/`compile_calculated_sql` ile
  **DuckDB'de** hesaplar, sadece küçük **sonucu** parquet'ler. (Lazy bir
  kaynaktan türetme: cron büyük kaynağı bir kez çeker, agrega eder, ufak
  sonucu saklar.)

**Dedup kazancı:** N grafik tek bir alias'a bağlandığında pahalı query
**aralık başına bir kez** koşar — blok-bazlı modelin kaçırdığı şey buydu.

Cron UI: cached + türetilmiş node kartlarında `⟳` butonu → manuel | aralık
(her N dk) | takvim (saat + gün). Seçim `basket[i].refresh`'e yazılır,
"Sunum'a geç" sonrası scheduler devralır.

### Read — viewer asla Oracle'a gitmez

`materialize.py::load_into_duck` her cached dataset'in parquet'ini pandas ile
okuyup DuckDB'ye view olarak register eder (DuckDB'nin dosya erişimi kapalı
kalır). `project_block_from_dataset(conn, binding, filter_state)` bloğun
`dataset_binding`'ine göre `SELECT cols FROM "alias"` çalıştırır ve
interaktif filtreleri **DuckDB WHERE predicate'i** olarak uygular
(between/in/eq, injection-safe, $-bind). Parquet yoksa `None` döner → grafik
"henüz materialize edilmedi" boş hâliyle çizilir. **Hiçbir viewer yolu
Oracle'a gitmez.**

> İmplementasyon notu: `materialize_dataset` şu an yalnız cron'dan çağrılıyor;
> `kind: manual` (cron'suz) bir dataset, ilk materialize çalışana kadar
> Sunum'da boş görünür. Build-zamanı tek-seferlik materialize backlog'da.

---

## 8. Tablonun akışlardaki rolü (özet — detay stage doküman­larında)

Tablonun uçtan-uca yolculuğunda üç aşama var. Buradaki anlatım **yüksek
seviye**; her aşamanın derin mekaniği kendi dokümanında.

### Keşif — "Hangi tabloları kullanayım?" → `[Keşif dokümanı]`

Kullanıcı doğal dilde yazar. CatalogLoader özeti (her `TableEntry` tek satır,
departman + concept bonuslu sıralı) LLM prompt'una gömülür; `propose_tables`
öneri döndürür, `_shape_result` katalogda olmayan halüsinasyon önerileri eler
(`dropped_proposals`, kullanıcıya şeffaf). DEV'de `FakeLLM` TR→EN keyword
matching yapar. Kullanıcı beğendiğini **sepete** ekler (`POST /<pid>/basket`,
tam liste replace). Tablonun buradaki rolü: **bir aday**.

### Hazırlık — Sepetteki tablo → dataset → `[Hazırlık dokümanı]`

Sepetteki her tablo bir **dataset**'e dönüşür (§7). Bu aşamada belirlenir:

- **Routing** (cached/lazy) — `_refresh_routing` her sistem-sahipli item'ı
  tekrar değerlendirir; user override'ı korunur.
- **Join'ler** — `_suggested_edges` iki kaynaktan önerir: TableDoc `lookup`
  (FK) ve **shared concept** (iki tablo aynı concept'i farklı kolona bağlamış).
  Dashed çizgi → kullanıcı confirm → `scope.joins`.
- **Filtreler** — `/concepts/filter-suggestions` `human_verified` binding'lerden
  filtre önerir (time → date_range, enum → multi/typeahead). Manifest'in
  top-level `filters[]`'ına yazılır.
- **Yeni dataset üretimi** — manuel SQL tablo, Excel yükleme, LLM ile
  aggregate/calculated türetme.

"Sunum'a geç" (`POST /<pid>/scope/build`) → scope validate + versiyonlanır
(`scope_v{N+1}`), manifest'e `scope_ref` yazılır, cached dataset'ler cron ile
parquet'lenmeye başlar. Tablonun rolü: **materialize edilebilir veri kaynağı**.

### Sunum — Bloklar veriyi nasıl alır → `[Sunum dokümanı]`

İki yol yan yana yaşar:

1. **Dataset-bound (yeni, primary):** Blok `dataset_binding{alias, columns,
   filters}` taşır. `apply-filters` → `load_into_duck` (parquet → view) →
   `project_block_from_dataset` (DuckDB projeksiyon + filtre). **Oracle yok.**
   Sabit tarih filtresi / filter bar değişimi = cached dataset üzerinde DuckDB
   requery.
2. **Concept-injection (legacy / library blokları):** Blok kendi SQL'ini
   taşır (`WHERE {{concept_filters}}`), `source_tables`'taki her tablonun
   `human_verified` binding'ine göre compiler AND-predicate'leri üretir,
   sentinel'i değiştirir, parametrize SQL Oracle'a gider. Concept-blind tablo
   (binding yok) → predicate eklenmez, "filtre uygulanmadı" badge'i çıkar.

Tablonun rolü: **görselin bağlandığı (materialize edilmiş ya da concept-aware)
veri kaynağı**.

> Üç aşamanın da derin akış mekaniği (prompt inşası, apply-filters loop,
> variable resolution, empty-selection, cache katmanları, sentinel dansı)
> ilgili **stage doküman­larında** anlatılacak. Bu doküman tabloya odaklı
> kalır.

---

## 9. UI ekranları — Tablolar nerede karşına çıkar

**Atölye/Tablolar — Liste** (`/presentations/atolye/tablolar`,
`routes_kesif.py::atolye_tablolar`): CatalogLoader'dan TableEntry'ler,
schema'ya göre gruplu (`(-len(tables), schema)` sıralı — EDW en üstte). Kart:
`{schema}.{name}`, description, department, concept sayısı, row count, source
badge, "Düzenle →".

**Atölye/Tablolar — Düzenle** (`/presentations/atolye/tablolar/<S>/<T>`,
`routes_library.py::tablo_edit`): form editör. Üst: şema/tablo (readonly),
partition (dropdown), günlük satır, açıklama. Alt: her kolon bir kart (ad,
tip, açıklama, filterable/aggregatable/visible checkbox, filter_role, suggested
variable/tag, lookup grubu). `form_json` ile hidrate; save → `_form_to_table_doc_dict`
→ Pydantic → YAML. `_humanize_validation_error` Türkçe hata üretir (raw pydantic
sızmaz).

**Atölye/Keşif graph** (`/presentations/atolye/kesif`): Cosmograph bipartite —
sarı concept hub'lar, mavi tablo node'ları, edge'ler concept binding. Click →
sepete ekle. `/catalog/graph` (60s cache) payload sağlar.

**Atölye/Hazırlık** (`/presentations/atolye/hazirlik/<pid>`): sol = sepet +
routing badge; orta = ER canvas (node + join edge); sağ = filter bar +
suggestions. Etkileşimler: routing override, projeksiyon edit, join confirm,
filtre ekleme, manuel SQL/Excel dataset.

**Concept Review Queue** (`/concepts/review`): pending binding önerileri
(Schema.Table, concept/column/transform, confidence, reasoning, [Onayla]/[Reddet]).
Onay → YAML'a `human_verified`.

---

## 10. Genişleme — Sisteme yeni şeyler eklemek

- **Yeni concept**: `presentations/catalog/concepts/<dept>.yaml`'a ekle
  (`id, name, type, description, canonical_values?`). CatalogLoader re-init /
  pod restart.
- **Yeni transform kind**: `concepts/compiler.py` dispatch + Pydantic Literal'ı
  genişlet + test fixture + spec §10.2 amendment.
- **Yeni filter type**: `_filter_proposal_from_concept` + `compile_filters`
  dispatch + FilterBar.jsx input widget.
- **Yeni binding inference rule**: `concepts/inference/` pipeline'a adım ekle
  (regex → dtype → distinct → LLM); review'a `inferred_<kural>` damgalı çıkar.
- **Yeni kolon meta field'ı**: `ColumnDoc`'a ekle + ilgili invariant +
  form template/serializer + eski YAML default migration.

---

## Sonsöz — Tablolar sistemin omurgası

Tablolar olmadan bu sistem mümkün değil. LLM cümle döker ama gerçek veriye
dokunamaz; concept binding olmadan filtreler her tabloya spesifik kalır;
CatalogLoader olmadan her endpoint kendi YAML walker'ını yazar. Her parçanın
bir nedeni var:

- **TableDoc** → kolon metadatasını tutar ki LLM tabloyu okuyabilsin.
- **Concept binding** → farklı tablolarda aynı kavramı eşleştirir ki filter
  compiler genelleyebilsin.
- **CatalogLoader** → iki YAML tree'sini birleştirir ki backend tek noktadan
  okusun.
- **Dataset katmanı** → cached tabloyu parquet'e materialize eder ki **viewer
  Oracle'ı tetiklemesin** ve aynı query N grafik için bir kez koşsun.
- **Bootstrap / distinct sampler / review queue** → yeni tabloyu otomatik
  dökümante eder, filter değerlerini hazır tutar, binding'leri insan onayından
  geçirir.

Tablonun başına ne geldiğini anlamak için bu sırayı takip et: kim ekledi,
operator nasıl zenginleştirdi, hangi concept'lere bağlandı, hangi endpoint'ten
okunuyor, cached ise nasıl materialize ediliyor, filtre nasıl
(DuckDB predicate ya da concept SQL) uygulanıyor.

> **Sonraki doküman­lar:** `BACKEND_BLOKLAR.md` (blok entity'si) ve
> `BACKEND_UZMANLAR.md` (uzman entity'si); ardından **Keşif / Hazırlık /
> Sunum** stage doküman­ları (akışların uçtan-uca anlatımı).
