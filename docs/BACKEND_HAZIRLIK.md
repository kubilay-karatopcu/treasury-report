# Treasury Studio — Hazırlık Aşaması (Akış Dokümanı)

> Stage (akış) dokümanı: Hazırlık'ın backend'de uçtan-uca işleyişi.
> Önceki: [`BACKEND_KESIF.md`](BACKEND_KESIF.md). Sıradaki:
> [`BACKEND_SUNUM.md`](BACKEND_SUNUM.md). Entity tarafı:
> [`BACKEND_TABLOLAR.md`](BACKEND_TABLOLAR.md) (özellikle §7 Dataset katmanı).

---

## Önsöz — Hazırlık ne yapar?

Hazırlık, sepete alınan tablolarla **"ne yapacağız?"** sorusunun yapısal
cevabını üreten aşamadır. Mimari karar gereği (Hazırlık = veri katmanı,
Sunum = saf görsel), **tüm SQL/tablo/kolon/veri işi burada** kararlaştırılır:

- Her tablonun **routing**'i: `cached` (DuckDB'ye/parquet'e materialise) mi,
  `lazy` (Oracle'da kal) mı?
- Tablolar nasıl **join**'lenecek?
- Hangi **filtreler** olacak (pinned / interaktif)?
- **Yeni dataset'ler**: manuel SQL tablo, Excel yükleme, LLM ile türetme
  (aggregate / calculated).

Bu kararların toplamı bir **ScopeContract**'tır. "Sunum'a geç" deyince scope
**versiyonlanır**, cached tablolar verilir, ve manifest'e bir `scope_ref`
yazılır. Sunum bu sözleşme altında çalışır.

> Hazırlık'ın çıktısı bir **sözleşme + materialize edilmiş veri**dir; tek
> tek blokların kendi başına "Oracle'a gideyim mi?" diye sormasını engeller.

---

## 1. ScopeContract — Hazırlık'ın state'i

`presentations/scope/schema.py::ScopeContract`:

- `presentation_id`, `version` (store yazımında değişmez), `created_by`,
  `created_at`, `parent_version` (v1'de null).
- `basket[]` — her item üç kaynaktan biri: **`table_ref`** (Oracle tablosu),
  **`derivation`** (türetilmiş: aggregate/calculated), **`sql`** (serbest
  SELECT, Faz C). Her item: `routing{decision, decided_by, estimated_bytes}`,
  `projection{columns, include_all}`, `refresh{kind, interval_seconds,
  schedule}`.
- `joins[]`, `filters{pinned[], interactive[], raw[]}`, `status{state,
  fetched_at, cached_tables[], lazy_tables[], errors[]}`.

İki yaşam-yeri var:
- **Draft scope** — `session.manifest['draft_scope']` (düz dict, **doğrulanmaz**,
  sayfa reload'ını atlatır). Düzenleme sırasında burada yaşar.
- **Built scope** — `SCOPE_STORE`'da versiyonlu YAML (immutable). Build'de yazılır.

---

## 2. Sayfa yükleme — `GET /hazirlik/<pid>`

`routes_scope.py:713`: `_load_latest_scope_or_draft()` önceliği:
(1) `SCOPE_STORE.load_latest()`, (2) `session.manifest['draft_scope']`,
(3) boş default. `?seed=` varsa `_seed_basket_from_query` (Keşif köprüsü).
**`_refresh_routing()`** canlı katalogla cached/lazy kararlarını yeniden
hesaplar (badge'ler güncel kalsın). React template'e gömülü JSON: scope,
katalog, concepts, `columns_by_alias`, `suggested_edges`, `routing_config`.

---

## 3. Auto-save draft — `POST /scope/save-draft`

`routes_scope.py:1081`: in-progress scope dict'ini `manifest['draft_scope']`'a
yazar (SCOPE_STORE'a **değil**). Şema **doğrulanmaz** (draft geçici olarak
tutarsız olabilir). Version bump yok, S3-scope yazımı yok. Frontend ~500ms
debounce ile çağırır → reload'da veri kaybı olmaz.

---

## 4. Routing — cached mı lazy mı? (`scope/routing.py`)

`decide_routing(item, catalog, pinned_filters)` pure/deterministik:

```
rows = (pinned filtre partition kolonuna iniyorsa) daily_rows × days
       aksi halde estimated_total_rows
bytes = rows × bytes_per_row(projection)
bytes > 10 GB (hard ceiling) → cached reddedilir
bytes < 500 MB (threshold)   → "cached"   aksi halde "lazy"
```

Tahmin **yalnız katalog metadata'sından** (Oracle çağrısı yok) → hızlı +
test edilebilir. Her sayfa yükleme + her mutasyondan sonra yeniden hesaplanır.
**Kullanıcı override'ı** (`decided_by="user"`) korunur; sistem-kararlıları
yeniden hesaplanır. Türetilmiş item'lar daima `cached` (Oracle byte-estimate'i
yok), `_refresh_routing` onları atlar.

---

## 5. Join inference & filtre önerileri

**`_suggested_edges`** iki kaynaktan join önerir (dashed çizgi): (1) TableDoc
`lookup` (FK), (2) **shared concept** (iki tablo aynı concept'i farklı kolona
bağlamış). `source` alanı UI'da "niye öneriliyor" der. Confirm → `scope.joins`.

**Filtre önerileri** (`/concepts/filter-suggestions`): blokların/sepetin
`human_verified` concept binding'lerinden filtre üretir — `time` → date_range,
`enum` + canonical_values → enum_multi, `enum` + boş (400+ şube) → typeahead.
Seçilenler `manifest.filters[]`'a yazılır.

---

## 6. LLM chat + apply-suggestion — yeni dataset'ler

**`POST /scope/chat`** (1357): draft scope + mesaj + geçmiş → LLM
`suggest_scope_refinements` (bound_concepts index + sepet katalog excerpt) →
`suggestions` (`kind ∈ {pin_filter, add_filter, add_projection_column,
confirm_join, create_aggregate, create_calculation}`). Mutasyon YOK — öneriler
frontend'e döner.

**`POST /scope/apply-suggestion`** (1440): `_mutate_scope_with_suggestion`
(deep-copy) mutasyonu uygular: filtre ekle/pin'le, projeksiyon kolonu ekle,
join onayla, **derived item ekle** (`create_aggregate` → `derivation.kind=
aggregate`; `create_calculation` → `calculated`). Mutasyonu doğrula + routing
refresh (projeksiyon değişti → bytes değişti → routing değişebilir). Sonuç
frontend React state'inde tutulur (henüz auto-save değil).

> Manuel SQL tablo (`/scope/preview-sql` ile önizleyip ekleme) ve Excel
> yükleme de bu aşamada birer `sql`/`table_ref` dataset üretir.

---

## 7. Build — Hazırlık → Sunum geçişi (KRİTİK)

**`POST /scope/preview-build`** (815): dry-run. Scope'u doğrula, parent'la
diff'le, etkilenen blokları hesapla. **Fetch yok, save yok, materialize yok** —
"Sunum'a geç" modalında ne değişeceğini gösterir.

**`POST /scope/build`** (874): asıl geçiş:

1. Scope'u **doğrula** + routing refresh.
2. Parent'la diff (varsa `parent_version` set).
3. **Re-entry planı**: parent varsa kısmi refetch (`refetch_only` = değişen
   alias'lar) + `drop_aliases` (kaldırılanlar).
4. **`fetch_cached_tables(dc, conn, scope, …)`** → **DuckDB session view'leri**
   materialize eder (S3 parquet **DEĞİL**). Pass 1: cached `table_ref` →
   `compose_cached_sql` (partition/concept/raw pushdown) → `dc.get_data`
   (Oracle) → DuckDB view. Pass 2: derived → kaynak view'ler üzerinde DuckDB'de
   GROUP BY/JOIN → view. Kısmi refetch'te değişmeyen alias'lar atlanır.
5. Scope'u **`SCOPE_STORE`'a kaydet** (version bump: `_stamp_lineage`
   v_N→v_{N+1}, parent=v_N; immutable).
6. `manifest['scope_ref'] = {presentation_id, scope_version}` yaz.
7. Cevap: `scope_version`, `cached_tables`, `lazy_tables`, editöre redirect.

### ⚠️ İki materialize mekanizması yan yana

Bu, en çok yanlış anlaşılan nokta:

| | Tetikleyen | Yazdığı yer | Kim okur |
|---|---|---|---|
| **`fetch_cached_tables`** | **build** (`/scope/build`) | DuckDB **session view** (RAM, geçici, per `(sicil,pid)`) | **build'i yapan kullanıcı**, aynı session'da hemen |
| **`materialize_dataset`** | **cron** (`DatasetScheduler`, 60s poll) | **S3 parquet** (kalıcı) | Sunum `load_into_duck` (her viewer, pod restart sonrası) |

- **Build, S3 parquet YAZMAZ.** Sadece DuckDB session view'i (RAM) + scope
  YAML'ı yazar. Build'i yapan kullanıcı verisini **anında** görür (aynı
  session view'leri).
- **`materialize_dataset`'i YALNIZ cron çağırır.** `cached + refresh.kind=
  "scheduled"` dataset'leri due olunca parquet'ler.
- Sonuç: **`kind: manual` (cron'suz) bir dataset, başka bir viewer'da / pod
  restart sonrası, ilk cron çalışana kadar BOŞ görünür** — çünkü session
  view'i gitti, parquet'i yok. (Build-zamanı tek-seferlik parquet materialize
  backlog'da; o gelene kadar manuel dataset'ler için scheduled refresh
  bağlamak en sağlamı.)

---

## 8. Cron materialize — `DatasetScheduler` (`cache/dataset_scheduler.py`)

Tek-thread daemon, 60s poll. `SCOPE_STORE.list_presentations()` → her scope'un
her item'ı için: `refresh.kind=="scheduled"` ve `routing.decision=="cached"`
ve `_dataset_due(...)` ise → `RefreshDispatcher.enqueue(materialize_dataset)`
(thread pool, `cache_key=dataset:{pid}:{alias}` ile dedup).

`materialize_dataset` (`scope/materialize.py`): `_compute_dataset_df`
(table_ref→Oracle, sql→whitelist+Oracle, derived→kaynak parquet'ten ya da
in-memory) → `write_dataset` → `datasets/{pid}/{alias}/data.parquet` +
`meta.json` (columns, row_count, refreshed_at, sql_hash). **N grafik tek
alias'a bağlıysa, pahalı query aralık başına bir kez koşar** (dedup kazancı).

---

## State özeti

| Ne | Nerede | Not |
|---|---|---|
| Draft scope | `manifest['draft_scope']` (dict) | doğrulanmaz, geçici, reload'ı atlatır |
| Built scope | S3 `…/{pid}/scope_v{N}.yaml` | immutable, version lineage |
| `scope_ref` | manifest | `{presentation_id, scope_version}` → en son build |
| DuckDB session | per `(sicil, pid)`, RAM | build'in fetch_cached_tables view'leri; pod restart'ta gider |
| Dataset parquet | S3 `datasets/{pid}/{alias}/` | yalnız cron yazar; Sunum okur |

---

## Dikkat (gotcha'lar)

- **Build parquet yazmaz** (yukarıda). Yaygın yanılgı.
- **Draft scope geçici** — build sonrası `scope_ref` otoriter olur; built bir
  sunumu tekrar açınca SCOPE_STORE'dan yüklenir, eski draft'tan değil.
- **Kısmi re-entry session kalıcılığı varsayar** — pod restart olursa
  değişmeyen view'ler kaybolur; sonraki okuma `load_into_duck` ile parquet'ten
  yeniden okur (ya da cron çalışmadıysa boş).
- **Routing build sonrası dondurulur** — scope_v_N'in cached/lazy kararları
  sabit; cron bunları kullanır. Yeni v_{N+1}'de bir item lazy yapılırsa eski
  v_N parquet'i S3'te kalır (GC yok).
- **Lazy tablolar build'de cache'lenmez** — on-demand (blok-zamanı, ~5M satır
  cap) çekilir, asla parquet'lenmez.
- **Concept pushdown sessiz** — pinned bir filtre tabloda binding'i yoksa
  Oracle WHERE'inden düşer (hata değil); blok-zamanı resolver yine uygular.
- **Cron enqueue dedup'lu** — aynı `pid:alias` aynı anda iki kez koşmaz.

---

## Akıştaki yeri

```
[KEŞİF] ──▶ [HAZIRLIK] ── build (scope_ref) ──▶ [SUNUM]
   sepet → routing/join/filtre/dataset → versiyonlu scope
                         └── cron ──▶ S3 parquet
```

> Sıradaki: [`BACKEND_SUNUM.md`](BACKEND_SUNUM.md) — bloklar veriyi nasıl
> alır (dataset parquet vs concept-injection), apply-filters branch sırası.
