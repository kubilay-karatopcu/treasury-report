# Treasury Studio — Bloklar Katmanı (Anlatımlı Doküman)

> Bu doküman **blok** entity'sini anlatır: bir blok nedir, kodda nasıl
> saklanır/versiyonlanır, SQL'i nasıl çalışır, kütüphanede nasıl paylaşılır.
> Akışların (Keşif / Hazırlık / Sunum) uçtan-uca anlatımı ayrı stage
> doküman­larında yapılır; burada bloğun o akışlardaki rolü özetlenir (§10).
> Kardeş doküman: [`BACKEND_TABLOLAR.md`](BACKEND_TABLOLAR.md).

---

## Önsöz — Neden "blok" diye ayrı bir şey var?

Bir grafik düşün: "şube bazında son 30 günün mevduat bakiyesi, bar chart".
Bu grafiği bir kez yapıp **tek bir dashboard'a** gömebilirsin. Ama:

- Aynı grafiği 10 farklı rapora koymak isteyen 5 kişi var.
- Grafiğin SQL'i değişince (yeni kolon, düzeltilmiş filtre) **hepsinin**
  güncellenmesi gerekiyor.
- "Bu grafiği kim yazdı, hangi versiyonu hangi raporda kullanılıyor, neyi
  değiştirdik?" sorularının cevabı lazım.

Dashboard'a gömülü tek-seferlik bir grafik bunları çözemez. **Blok** tam
da bunun için var: grafiği (SQL + değişkenler + görsel tipi) **tekrar
kullanılabilir, versiyonlu, sahipli bir artefakt** olarak ayrı saklarız.
Dashboard'lar bloğa bir **referans** ile bağlanır; blok bağımsız yaşar.

Yani iki ayrı kavram:

- **Blok** = atomik, versiyonlu yapı taşı (kütüphanede durur, sahibi var).
  `presentations/blocks/`.
- **Manifest / Sunum bloğu** = bir dashboard'ın layout'u + içine alınmış
  blok kopyaları + filtre çubuğu. Manifest blokları bir kütüphane bloğundan
  **import** edilmiş olabilir (`imported_from` damgası), ya da Sunum'da
  doğrudan üretilmiş olabilir.

Bu doküman **kütüphane bloğunu** (Phase 6.5 `Block` entity'si) anlatır;
manifest bloğunun Sunum'daki davranışı Sunum stage dokümanında.

---

## 1. Blok şeması — `presentations/blocks/schema.py`

Bir blok diskte **tek bir `block:` anahtarı** altında saklanır
(`BlockDocument` sarmalar): `{"block": { …Block alanları… }}`. Serialize:
`model_dump(mode="json", exclude_none=True)` (None opsiyoneller atlanır).
Tüm modeller **`ConfigDict(extra="forbid")`** — bilinmeyen bir field sessizce
kabul edilmez, schema drift erken yakalanır.

### `Block` kök alanları

| Alan | Tip / Kısıt | Anlam |
|---|---|---|
| `id` | `BlockId`: 3–60, `[a-z0-9_]+` | Takım-içi benzersiz kimlik (snake_case) |
| `version` | int 1–10000 | **Disk'te değişmez** (per-version immutable) |
| `title` | str 1–200 | Kütüphanede görünen ad |
| `description` | str? | Kısa açıklama |
| `team` | `TeamId`: 2–60, `[a-z0-9_]+` | Sahibi takım (normalize slug) |
| `owner` | str 1–80 | **Her zaman `current_user.sicil`** (kaydederken zorlanır) |
| `created_at` / `updated_at` | datetime (ISO) | İlki ilk kayıtta, ikincisi her kayıtta UTC now |
| `tags` | list[str] | Serbest etiket (kütüphane arama/filtre) |
| `deprecated` | bool (default False) | Soft-delete bayrağı |
| `changelog` | str? | Versiyon notu (opsiyonel, serbest metin) |
| `documentation` | `BlockDocumentation`? | purpose / business_context / decision_support / known_limitations |
| `query` | str (min 1) | `:bind` değişkenli SQL |
| `variables` | list[`Variable`] | Bind değişkenleri (bkz. §2) |
| `visualization` | `Visualization` (zorunlu) | Görsel tipi + config (bkz. §3) |
| `refresh_policy` | `RefreshPolicy`? | Önbellek/tazeleme politikası (bkz. §6) |

`model_validator` ek olarak zorlar: `tags` trimlenmiş boş-olmayan string'ler;
**blok içindeki variable adları benzersiz** (kopya-yapıştır çakışması → hata).

---

## 2. Variable & semantic_tag — bloğun parametreleri

Bir bloğun SQL'i `WHERE SEGMENT IN (:segs)` gibi **bind değişkenleri**
içerir. Her değişken bir `Variable` (schema.py:87):

- **`name`** — `[a-z0-9_]+`, 3–40. SQL'de `:name` olarak görünür.
- **`semantic_tag`** — **ZORUNLU.** `SEMANTIC_TAGS_V0` allow-list'inden:
  `as_of_time, trade_time, value_time, settle_time, currency, maturity,
  tenor_bucket, counterparty, branch, region, product_group, segment,
  rating_bucket, user_id, deal_id, instrument_type, other`. `other` kaçış
  kapısıdır ve UI'da işaretlenir. Bu tag, dashboard filtresine
  **semantic_tag eşleşmesiyle** otomatik bağlanmanın anahtarıdır (Phase 7
  concept sistemiyle ileri-uyum sözleşmesi).
- **`type`** — `date | date_range | enum_single | enum_multi | number_range`.
- **`required`** (default True), **`default`**, **`allowed_values`**
  (yalnız enum tipleri).

Tip-bazlı doğrulama: enum_single/enum_multi `allowed_values` ister
(default subset olmalı); `date.default` göreceli ifade regex'iyle eşleşir
(`today`, `today - 30d`, `start_of_month`, ISO); `date_range.default` =
`{from, to}`; `number_range.default` = `{min, max}`.

> **Dikkat — aralık değişkenleri doğrudan bind edilemez.** `:my_range`
> hata verir. SQL **accessor** kullanmalı: `date_range` → `:name_from` /
> `:name_to`; `number_range` → `:name_min` / `:name_max`. Validator,
> parent aralık değişkeni tanımlıysa bu accessor'ları otomatik kabul eder.

---

## 3. Visualization — görsel tipi

`Visualization` (schema.py:215): `type` ∈ `kpi | kpi_grid | line | bar |
bar_chart | line_chart | table | pie`, `config` = serbest `dict[str, Any]`.
Tip-bazlı config doğrulaması **renderer'a bırakılır** — schema'yı chart
kütüphanesine bağlamamak için kasıtlı olarak gevşek. Veri, çalıştırma
sonrası `config`'e yazılır (bkz. §8).

---

## 4. Immutability, versiyonlama, soft-delete

**Sözleşme:** her `(team, block_id, version)` üçlüsü **disk'te değişmez.**

- S3 key: `prisma-treasury/v2-blocks/{team}/{block_id}/v{NNNN:04d}.yaml`
- Local: `<base_dir>/{team}/{block_id}/v{NNNN:04d}.yaml`
- `save()` varolan bir versiyona yazmaya çalışırsa **`BlockAlreadyExistsError`**
  (S3'te `if-none-match` koşullu PUT, local'de `open(...,'xb')`).

**Düzenleme = yeni versiyon.** `save_new_version()` en yüksek mevcut
versiyonu okur, +1'ler, çakışmada **5 kez compare-and-swap retry** yapar,
`updated_at`'i UTC now'a çeker. Payload'daki `version` alanı **yok sayılır**
— kullanıcı versiyon belirleyemez.

**Dashboard referansları** belirli versiyona bağlanır (`{team, id, version}`).
Bir bloğu düzenleyip N+1'e çıkarmak eski dashboard'ları **bozmaz** — onlar
hâlâ v_N'e işaret eder (immutable contract).

**Soft-delete:** `soft_delete()` son versiyona `deprecated=True` yazar. Bu,
immutability kuralının **tek istisnası** (sadece bayrak değişir, içerik eski
versiyonlarda korunur). Hard-delete yok (v0 backlog). `list_blocks(
include_deprecated=False)` deprecated'leri eler ve her blok için **yalnız en
son versiyonu** döndürür.

---

## 5. Storage — `presentations/blocks/store.py`

`BlockStore` protokolü: `save`, `save_new_version`, `load(team,id,version)`,
`load_latest(team,id)`, `list_versions`, `list_blocks(team?, tag?, viz_type?,
search?, include_deprecated=False)`, `soft_delete`.

- **`LocalBlockStore`** — DEV, dosya sistemi.
- **`S3BlockStore`** — Production, `DataClient` üzerinden koşullu PUT'lar.

> **Dikkat — iki ayrı kütüphane deposu var.** `/blocks/*` route'ları
> **`BLOCK_STORE`**'u (Phase 6.5, `semantic_tag` zorunlu) kullanır;
> eski `/library/*` route'ları ayrı **`LIBRARY_STORE`**'u (pre-6.5,
> `semantic_tag` yok) kullanır. İki ayrı persistence katmanı; karıştırma.

---

## 6. RefreshPolicy — önbellek/tazeleme politikası (Phase B)

`RefreshPolicy` (schema.py:297): `kind` ∈ `on_open | lazy_ttl | scheduled |
manual` (default `on_open`), `fresh_for_seconds` (10–86400, default 600),
`serve_stale` (default True), `max_age_seconds` (default 86400),
`interval_seconds` veya `schedule` (`scheduled` için, biri-XOR-diğeri).
`RefreshSchedule`: `times` (HH:MM, normalize+sıralı), `days` (MON–SUN),
`timezone` (default `Europe/Istanbul`).

- **`on_open`** (default) → paylaşımlı önbellek yok, eski per-session yol.
- **`lazy_ttl`** → paylaşımlı kütüphane önbelleği + serve-stale (bkz. §9).
- **`scheduled`** → arka plan scheduler önbelleği ısıtır (interval ya da
  takvim). Neither verilmezse günlük 09:00'a düşer.

---

## 7. HTTP endpoint'leri + yetki — `presentations/routes_blocks.py`

| Method | Path | Yetki | Ne yapar |
|---|---|---|---|
| POST | `/blocks/api/validate` | yok | Şema + SQL whitelist kontrolü. `{ok, phase: schema\|sql, errors, warnings}` |
| POST | `/blocks/api/save` | **write gate** | Yeni blok yarat. Versiyon varsa **409** → save_new_version kullan |
| POST | `/blocks/api/save_new_version` | **write gate** | Versiyonu +1'le (atomik CAS) |
| GET | `/blocks/api/list` | yok | Kütüphaneyi listele: `?team` (fuzzy), `?tag`, `?viz_type`, `?q` |
| GET | `/blocks/api/<team>/<id>/<ver>` | yok | Belirli versiyon detayı (`{block:{…}}`) |
| GET | `/blocks/api/<team>/<id>/versions` | yok | Versiyon numaraları |
| POST | `/blocks/<team>/<id>/<ver>/run` | yok | Kayıtlı bloğu çalıştır (`variable_overrides`) → `{rows, columns, meta}` |
| POST | `/blocks/api/preview` | yok | **Kaydedilmemiş** blok payload'ını çalıştır (editör mini-canvas) |
| GET | `/blocks/preview/<team>/<id>[/<ver>]` | yok | Salt-okunur iframe önizleme (HTML) |
| POST | `/<pid>/blocks/insert-from-library` | — | Kütüphane bloğunu dashboard'a klonla |

### Yetki kapısı (`_block_write_denied`)

**Sadece yazma korunur.** Kullanıcının departmanı `_normalize_team_token`
ile slug'lanır ve **bloğun `team` slug'ıyla birebir eşleşmeli** (substring/
wildcard yok); eşleşmezse **403** ("Bu ekip altına blok kaydetme/güncelleme
yetkin yok."). Okuma endpoint'lerinde (list/get/preview/run) **yetki yok** —
yayınlanmış her blok gezilebilir.

> **Dikkat — owner spoof edilemez.** `_normalise_block_payload` `owner`'ı
> daima `current_user.sicil`'e zorlar; payload'daki owner yok sayılır
> (offline runner hariç). Ayrıca takım slug normalizasyonu (Türkçe İ →
> i+combining-dot) **UI'nın `slugify()`'ıyla birebir** aynı olmalı, yoksa
> yetki yanlışlıkla reddedilir.

### Kütüphaneden dashboard'a ekleme

`POST /<pid>/blocks/insert-from-library` `{team, id, version}` alır, bloğu
yükler (version yoksa `load_latest`), Phase 6.5 viz tipini Phase 6'ya eşler
(`bar`→`bar_chart`), **yeni bir id** (`b_<token>`) ile klonlar,
`imported_from{team, id, version}` damgalar, son section'a ekler (yoksa
"Yeni Bölüm" yaratır), `manifest.version`'ı artırır.

> **Dikkat — import versiyon-kilitli DEĞİL.** Klonlanan blok manifest'e
> gömülür; kaynak kütüphane bloğu sonradan güncellenirse dashboard eski
> SQL/config'i görür (yeniden import etmedikçe). Ayrıca `semantic_tag`'ler
> import sırasında **otomatik filtreye bağlanmaz** — bağlama, eklemeden
> sonra editörde yapılır (bkz. tablolar doc §8 binding kuralları).

---

## 8. SQL çalıştırma hattı — değişken → bind → veri → config

Bir blok çalıştığında (`/run`, `/preview`, ya da apply-filters döngüsü
içinde) şu hat işler:

**1. `resolve_variables`** (`variables/resolver.py`) — her değişkeni somut
değere çözer: öncelik `overrides → binding_resolver (dashboard) → default`.
`parse_date_expr` göreceli ifadeyi (`today - 30d`) `datetime.date`'e çevirir.
enum_multi default = `list(allowed_values)`; enum_single = ilk değer. Çıktı
tipleri: date→`date`, date_range→`{from,to}`, enum_multi→`list`,
number_range→`{min,max}`.

**2. `expand_binds`** (`sql/binder.py`) — `query`'yi parametrize eder,
**değerleri asla concat etmez.** enum_multi `:var` → pozisyonel
`:var_0, :var_1, …` (+ params). **Boş enum_multi** → `EmptySelectionError`
→ çağıran katman SQL'i hiç çalıştırmadan boş sonuca kısa-devre yapar.

**3. `validate_and_wrap`** (`aggregation_gate.py`) — güvenlik + dialect.
`upload__…` referansı varsa **DuckDB**, yoksa **Oracle** dialect'i (LIMIT
sözdizimi farkı). Aggregation (GROUP BY/DISTINCT/agg fn) → olduğu gibi koş;
ham SELECT → satır cap'iyle (default 5000) sar.

**4. Yönlendirme** (`duck.py::execute_block_sql`) — `upload__…` → DuckDB
(`ensure_upload_views` S3'ten sheet'leri yükler); aksi halde Oracle
(`dc.get_data`). Sonuç `block_<id>` view'ı olarak register edilir.

**5. `strip_concept_sentinel`** — concept yolu uygulanmıyorsa (manuel run,
preview) `{{concept_filters}}` sentinel'i `1 = 1`'e çevrilir (idempotent,
sentinel yoksa no-op).

**6. `apply_data_to_config`** (`nodes/execute_block_sqls.py`) — DataFrame'i
viz tipine göre `config`'e yazar (yerinde): KPI → ilk numerik → `value`;
bar/heatmap → col0 `categories`, col1+ `series`; line/area → col0 `x_axis`,
col1+ `series`; pie → col0 `labels`, col1 `values`; data_table → `columns`+
`rows`. **Boş DataFrame** → tip-bazlı temizleme (categories=[], value=0…) —
SQL "0 satır" derken eski grafiğin kalması (UI uyumsuzluğu) önlenir.
**Stil alanları (renk, başlık) silinmez** — kullanıcı özelleştirmesi korunur.

> SQL whitelist (`sql/validator.py::validate_sql`): sadece top-level
> `SELECT`/`WITH`, tek statement, DDL/DML/prosedürel yasak, `:bind`'ler
> tanımlı değişkenlerle eşleşmeli (kullanılmayan = uyarı, hata değil).
> DuckDB bağlantısı `enable_external_access=false` ile sertleştirilmiş.

---

## 9. Kütüphane önbelleği + tazeleme — `presentations/cache/`

**Problem:** aynı kütüphane bloğu 10 dashboard'da kullanılıyorsa, 10 Oracle
sorgusu israf. Çözüm: paylaşımlı önbellek.

- **Uygunluk** (`library_block_integration.py`): blok (1) `imported_from.
  library_id` taşır, (2) `refresh_policy.kind == "lazy_ttl"`, (3) **kişiye-özel
  predicate içermez** (`:sicil`, `:owner`, `OWNER_ID=`, `CURRENT_USER` →
  paylaşımlı önbellek kirlenmesin diye reddedilir).
- **Cache key** (`library_block_cache.py`): `sha256("{team}|{block_id}|
  {version}|{vars_hash}")[:24]`. `version`, kaynağın `library_updated_at`
  timestamp'inden türetilir → kaynak düzenlenince key değişir (otomatik
  invalidation). Depo: tek dosyalı DuckDB, LRU eviction (500'ün %110'unda).
- **Tazelik:** `fresh` (age ≤ fresh_for_seconds) / `stale` / `expired`
  (age > max_age_seconds). Fresh → anında. Stale + serve_stale → **eskiyi
  anında dön + arka planda yenile** (`RefreshDispatcher`, ThreadPool 2,
  `in_flight` ile dedup → 10 eşzamanlı stale hit = 1 fetch). Expired → miss.
- **Scheduler** (`scheduler.py::LibraryRefreshScheduler`): tek-pod daemon,
  60s poll; `scheduled` politikalı blokları interval/takvim'e göre ısıtır
  (yalnız **default değişkenlerle**).
- **Entegrasyon:** apply-filters döngüsünde kütüphane önbelleği **per-session
  önbellekten ÖNCE** denenir; miss sonrası uygunsa yazılır.

> **Dikkat:** Concept-injection (Phase 7) uygulanan bloklar lazy_ttl'i
> **atlar** (senkron fetch + injection). Önbellek yalnız `lazy_ttl` +
> import edilmiş + kişiye-özel-olmayan bloklar için danışılır; `on_open`
> (default) blokları bu katmanı bütünüyle baypas eder.

---

## 10. Bloğun akışlardaki rolü (özet — detay stage doküman­larında)

- **Keşif** — blok yok; Keşif tablolarla ilgilenir. Blok ileride gelir.
- **Hazırlık / Kütüphane** — Kullanıcı **Library MVP**'de blok arar/önizler
  (`/blocks/api/list`, `/blocks/preview/…`) ve dashboard'a ekler
  (`insert-from-library`). Blok manifest'e `imported_from` damgasıyla klonlanır.
  → `[Hazırlık / Kütüphane dokümanı]`
- **Sunum** — Manifest blokları dashboard'ın içeriğidir. Bir Sunum bloğu ya
  **dataset'e bağlanır** (`dataset_binding` → parquet → DuckDB; yeni, viewer
  Oracle'a gitmez — bkz. tablolar doc §7), ya da **kendi query'sini** taşır
  (concept-injection / kütüphane önbellek yolu; legacy). Düzenleme/çalıştırma
  §8'deki hattı izler. → `[Sunum dokümanı]`

---

## 11. UI ekranları — Bloklar nerede karşına çıkar

- **Atölye/Bloklar (kütüphane listesi)** — kart grid'i (`/blocks/api/list`).
- **Blok editörü** (`block_template_edit.html` / `ManualSqlEditor.jsx`) — SQL
  textarea (CodeMirror), değişken listesi, "Şemayı Tara", "Çalıştır",
  validate/preview. Save → `/blocks/api/save` (ya da save_new_version).
- **Salt-okunur önizleme** (`block_preview.html`) — `/blocks/preview/…`,
  toolbar/panel yok.
- **Sunum editörü** — manifest blokları AG Charts ile render edilir; blok
  seçilince PropertiesPanel + ManualSqlEditor açılır.

---

## 12. Genişleme — Bloklara yeni şeyler eklemek

- **Yeni viz tipi**: `Visualization.type` Literal'ını genişlet + renderer
  (frontend) + `apply_data_to_config` `_DATA_KEYS_BY_TYPE` eşlemesi.
- **Yeni variable tipi**: `Variable.type` Literal + resolver + binder
  (accessor mantığı) + FilterBar widget.
- **Yeni semantic_tag**: `SEMANTIC_TAGS_V0` (kod değişikliği + PR; data-driven
  değil) ya da Phase 7 concept registry.
- **Yeni refresh kind**: `RefreshPolicy.kind` Literal + scheduler dispatch.
- **Yeni blok meta field'ı**: `Block` modeline ekle (`extra=forbid` → schema
  değişikliği şart) + editör formu + eski YAML migration.

---

## Sonsöz — Blok = paylaşılabilir, versiyonlu yapı taşı

Blok olmadan her grafik tek-seferlik kalırdı: paylaşılamaz, versiyonlanamaz,
sahipsiz. `(team, id, version)` immutability'si "hangi rapor hangi sürümü
kullanıyor" sorusunu kesinleştirir; `semantic_tag` zorunluluğu bloğu
dashboard filtrelerine ve Phase 7 concept'lerine bağlanabilir kılar; kütüphane
önbelleği aynı bloğun N dashboard'da bir kez koşmasını sağlar; yetki kapısı
+ owner zorlaması üretim güvenliğini verir.

Bir bloğun başına ne geldiğini anlamak için şu sırayı izle: kim yazdı (owner=
sicil), hangi takım altında (team gate), hangi versiyon (immutable üçlü),
SQL'i nasıl çalışır (§8 hattı), nereye import edildi (`imported_from`), ve
paylaşımlı mı önbelleklenir (`lazy_ttl`).

> **Sonraki doküman:** [`BACKEND_UZMANLAR.md`](BACKEND_UZMANLAR.md) (uzman
> entity'si); ardından Keşif / Hazırlık / Sunum stage doküman­ları.
