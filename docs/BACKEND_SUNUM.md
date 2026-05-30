# Treasury Studio — Sunum Aşaması (Akış Dokümanı)

> Stage (akış) dokümanı: Sunum editörünün backend'de uçtan-uca işleyişi.
> Önceki: [`BACKEND_HAZIRLIK.md`](BACKEND_HAZIRLIK.md). Entity tarafı:
> [`BACKEND_BLOKLAR.md`](BACKEND_BLOKLAR.md), [`BACKEND_TABLOLAR.md`](BACKEND_TABLOLAR.md),
> [`BACKEND_UZMANLAR.md`](BACKEND_UZMANLAR.md).

---

## Önsöz — Sunum ne yapar?

Sunum, kullanıcının **dashboard'u kurduğu ve izlediği** aşamadır. Hazırlık'tan
gelen scope altında, kullanıcı bloklar (KPI, grafik, tablo, narrative) ekler,
LLM ile düzenler, filtre çubuğuyla daraltır. İki ana etkileşim var:

1. **Sohbet (Stage-2)** — "şube bazında top-10 bar chart ekle" → LLM bir
   JSON Patch üretir, bloğun SQL'i çalışır, manifest güncellenir.
2. **Filtre değişimi** — kullanıcı (ya da izleyici) filtre çubuğunu/sabit
   tarih widget'ını değiştirir → bloklar yeniden veri çeker.

Temel mimari kural: **raporu izleyen kişi Oracle'ı tetiklememeli.** Bu yüzden
dataset'e bağlı bloklar yalnız **materialize edilmiş parquet'i** okur
(`load_into_duck`); Oracle'a yalnız tasarım-zamanı yolları (sohbet, lazy/legacy
blok çalıştırma, cron) gider.

---

## 1. Sunum açma — `GET /<pid>`

`routes.py:105`: `session.get_manifest()` (S3 + pod-local `SESSION_REGISTRY`
cache), `load_scope_for_manifest()` ile **scope banner** (hangi scope_ref
altındayız), `editor.html`'i `manifest_json` ile render. Sunum yoksa
`_seed_manifest` (demo). **Açılışta veri çalıştırılmaz** — render manifest'in
gömülü config'inden (son apply-filters/patch sonucu) gelir.

`GET /<pid>/manifest`: React mount/refresh için güncel manifest'i döner.

---

## 2. Sohbet — `POST /<pid>/chat` → SSE `GET /<pid>/stream/<token>`

`routes.py:1116`: `/chat` ephemeral bir job (`_CHAT_JOBS[token] = {sicil,
pid, message, selected_block_id}`) yaratır, **token'ı hemen döner**.
`/stream/<token>` job'ı pop'lar, `GraphState` kurar (manifest, user_message,
selected_block_id, **scope_contract**), `run_pipeline()` çağırır, SSE
event'leri (`status` / `patch` / `error` / `done`) yield eder.

### Patch pipeline — `graph.py:57`

```
plan_fetch → [gerekirse refetch] → generate_patch → validate_patch
   → execute_block_sqls (validation hatası yoksa) → apply_patch
```
Validation/SQL hatasında `generate_patch`'e **2 kez** retry (hata geri
beslenir).

- **`generate_patch`** (`nodes/generate_patch.py`): `catalog.json` (hot-reload)
  + `TABLE_DOC_STORE`'dan table_docs'u prompt'a gömer; LLM `prompts/edit.txt`
  yönergesiyle JSON Patch üretir (SQL blokları + manifest edit'leri).
- **`execute_block_sqls`** (`nodes/execute_block_sqls.py`): her SQL'li blok →
  `upload__` prefix'i DuckDB / aksi Oracle; aggregation gate; sonuç
  `block.data_source`'a; `apply_data_to_config` ile satırlar → chart config'i
  (kategoriler/series/value/labels). (Detay: [`BACKEND_BLOKLAR.md`](BACKEND_BLOKLAR.md) §8.)
- **`apply_patch`** (`nodes/apply_patch.py`): `apply_patches()` manifest'i
  yerinde değiştirir, version + `updated_at` bump, `session.set_manifest()`
  (S3 + pod cache).

> **scope_contract** GraphState'e verilir; `validate_patch` **pinned
> filtrelere** mutasyonu reddeder (scope'tan gelen salt-okunur filtreler
> sohbette düzenlenemez). **Uzmanlar (`bound_experts`) bu akışta OKUNMAZ** —
> bkz. [`BACKEND_UZMANLAR.md`](BACKEND_UZMANLAR.md) §4.

---

## 3. Filtre değişimi — `POST /<pid>/apply-filters` (kalp)

`routes.py:1739`: gövde `filter_state` (dashboard filtre değerleri). Döngü
**her bloğu sırayla** işler ve `{ok, version, blocks:[{id, status}]}` döner;
herhangi bir blok değiştiyse manifest persist edilir. Frontend "Güncelle"
butonu, sabit tarih widget'ı (debounced) ve izleyici filtre tıklamaları hep
bunu çağırır.

### Blok-içi branch sırası — İLK EŞLEŞEN KAZANIR

Her blok için (`routes.py:1869-2155`):

| # | Branch | Koşul | Sonuç | Oracle? |
|---|---|---|---|---|
| 0 | **Variable resolution** | her zaman | `variable_bindings` + `filter_state` → resolved değerler | — |
| 1 | **Empty-selection short-circuit** | enum_multi `[]` | `status="empty"`, SQL hiç koşmaz (FİNAL) | hayır |
| 2 | **Dataset-bound (Faz B)** | `block.dataset_binding.alias` var | `load_into_duck` (parquet→view) + `project_block_from_dataset` (DuckDB filtre predicate) → `status="dataset"` | **hayır** |
| 3 | **Library cache** | `imported_from.library_id` + `lazy_ttl` + kişiye-özel-predicate yok | fresh→hemen / stale→ser+arka-plan-refetch | hayır (hit'te) |
| 4 | **Concept injection** | `source_tables` + `{{concept_filters}}` sentinel | compile + sentinel'i AND-predicate ile değiştir → **Oracle** (cache baypas) | **evet** |
| 5 | **Per-session cache (exact)** | `cache_key(id+version+resolved+sql)` hit | DuckDB view'den → `status="cache_hit"` | hayır |
| 6 | **Subset parent** | resolved ⊂ parent.resolved | parent view'i DuckDB'de daralt → `status="subset"` | hayır |
| 7 | **Oracle fetch (son çare)** | yukarıdakilerin hiçbiri | `expand_binds` + `dc.get_data` → per-session cache'e yaz (+ uygunsa library cache) → `status="refetched"` | **evet** |

**İlk eşleşen kazanır:** Bir blok `dataset_binding` taşıyorsa (#2),
concept/cache/Oracle dalları **hiç çalışmaz** — viewer-read-only, sıfır Oracle.
Concept-blind tablo → predicate enjekte edilmez, "filtre uygulanmadı" badge'i.

> Library cache **per-session cache'ten ÖNCE** denenir (#3 < #5). Concept-
> injection uygulanan blok library cache'i **baypas eder** (#4 senkron fetch).

---

## 4. Filtre çubuğu & sabit tarih widget'ı (frontend → apply-filters)

- **FilterBar** (`editor/components/FilterBar.jsx`): enum/sayı filtreleri üstte;
  "Güncelle" → `applyFilters()`.
- **FixedDateFilter** (`editor/components/FixedDateFilter.jsx`): `date_range`
  filtreleri sağ-altta sabit widget'ta (tek tarih/aralık, ok ile ±1 gün, özel
  takvim popover, ~300ms debounce → otomatik `applyFilters`).
- İkisi de `filter_state`'i Zustand store'da tutar; `applyFilters` →
  `POST /apply-filters` → dönen blok config'leri store'a yazılır → re-render.
- **Bağlama:** filtre, bloğa `semantic_tag` eşleşmesiyle bağlanır (dashboard
  filtresi ↔ blok variable'ı, `dashboards/binding.py`) ya da dataset-bound
  blokta `dataset_binding.filters` (açık `{filter_id, column, op}` eşlemesi).

---

## 5. Snapshot & izleyici

**`POST /<pid>/snapshot`** (365): mevcut manifest'i S3'e dondurur
(`title_override`, `description`, `bound_experts`), paylaşılabilir URL döner
(~64-bit entropi). Snapshot salt-okunur.

**`GET /snapshot/<sid>`** / **`GET /dashboard/<did>`** (439/615): store'dan
yükle, auth (sahip ya da audience), `snapshot.html` render. **İzleyici aynı
apply-filters yolunu kullanır** (sohbet/LLM yok, sadece filtre tıklamaları) —
dataset-bound bloklar **asla Oracle'a gitmez** (`load_into_duck`, editörle
aynı). Cron parquet'i yazmadıysa grafik boş render eder.

---

## State özeti

| Ne | Nerede |
|---|---|
| Manifest | S3 `presentations/{sicil}/{pid}/` + pod-local `SESSION_REGISTRY` cache |
| DuckDB session | per `(sicil, pid)` reentrant conn — view'ler + execution sonuçları |
| Per-session BlockCache | `cache_key → view_name + row_count` (DuckDB) |
| Chat job | ephemeral `_CHAT_JOBS[token]` (stream'de pop) |
| Dataset parquet | S3 `datasets/{pid}/{alias}/` (cron yazar, Sunum `load_into_duck` okur) |
| Library block cache | cross-user (lazy_ttl), `LIBRARY_BLOCK_CACHE` DuckDB dosyası |
| Concept registry/binding | apply-filters'ta snapshot alınır → filter_state → predicate |

---

## Dikkat (gotcha'lar)

- **Branch sırası kritik** — `dataset_binding` varsa concept/cache/Oracle dalları
  atlanır. Sıralamayı yanlış kurmak (örn. concept'i dataset'ten önce koymak)
  viewer'ı Oracle'a sürükler.
- **Empty-selection (#1) library cache'ten önce döner** — enum_multi `[]` →
  `status=empty` o blok için finaldir.
- **Concept injection yalnız `source_tables` + sentinel varken** SQL'i değiştirir;
  sentinel'siz bloklar yalnız `blind/applied` bilgi alanı alır, SQL değişmez.
- **`strip_concept_sentinel`** — blok concept yoluna girmezse `{{concept_filters}}`
  → `1 = 1` (Oracle syntax hatası önlenir).
- **Dataset-bound viewer** parquet'i her apply-filters'ta `load_into_duck` ile
  bir kez okur; parquet yoksa (cron çalışmadı) boş. **Asla Oracle.**
- **Uzmanlar sohbeti etkilemez** — `bound_experts` manifest'te ama
  `generate_patch` onu okumaz (yarı-bağlı).
- **Chat geçmişi** manifest'e eklenir, sunum başına ~200 mesajla cap.
- **Retry**: validate_patch (şema) + execute_block_sqls (SQL/gate) hataları
  `validation_errors`'a sayılır, generate_patch başına 2 retry.

---

## Akıştaki yeri

```
[KEŞİF] ──▶ [HAZIRLIK] ── scope_ref ──▶ [SUNUM]
                                          │  ├─ sohbet → patch → execute → render
                                          │  └─ filtre → apply-filters (7-dal sırası)
                                          └─ snapshot ──▶ izleyici (aynı apply-filters, Oracle yok)
```

> Bu, entity (tablo/blok/uzman) ve akış (keşif/hazırlık/sunum) doküman­larının
> birleştiği son halkadır. Sıradaki konu: **storage / altyapı** — S3'e ne
> yazılıyor, versiyonlama, cache katmanları, RAM/kaynak gereksinimi.
