# Ofis Kullanım Backlog'u — 2026-06-24

2026-06-24 ofiste prod kullanımı sırasında not edilen 22 madde. Her madde kodla doğrulandı (file:line),
kök neden + plan + **kilitli karar** yazıldı. 8 oturuma ayrıldı; her oturum bağımsız planlanıp uygulanacak.

> Kaynak ilgili memory'ler: project_state, hazirlik_dataops_audit, office_followup_roadmap, infra_audit_2026_06,
> charting_library, frontend_bundle_deploy.

## Durum tablosu

| # | Oturum | Maddeler | Öncelik | Tahmini efor |
|---|--------|----------|---------|--------------|
| 1 | Veri mimarisi (sample-cache) | A1–A5 | 🔴 çapa | XL |
| 2 | Async build + abort + geçişler | B1–B4 | 🔴 | L |
| 3 | Concept filtreleri | C1–C3 | 🔴 | L |
| 4 | Dark tema + NaN serileştirme | D1–D3 | 🟢 hızlı kazanım | S |
| 5 | Hazırlık editör UX | E1–E4 | 🟠 | M |
| 6 | Filtre UX (binlik ayraç + tarih toggle) | F1–F2 | 🟢 | S |
| 7 | LLM JSON dayanıklılığı | G1 | 🔴 | M |
| 8 | Audit logging (Oracle tablosu) | H1 | 🟠 | M-L |

Bağımlılık: **1 → 2 ve 3'ü kolaylaştırır.** 4 ve 6 bağımsız, istenildiği an yapılabilir.

---

## Oturum 1 — Veri mimarisi: sample-cache + neyin cache'leneceği

> **DURUM (2026-06-24, branch `feat/oturum-1-sample-cache`):**
> - **1.1 ✅** — `scope/sample.py` (`compose_sample_sql`: raw→SAMPLE(%10)+200k tavan, manuel-SQL→top-N; `sample_fingerprint`), `compose_cached_sql`'e `sample_pct` kwarg, `materialize.py` `__dataset_fidelity` ledger (`record_fidelity`/`dataset_fidelity`, `__dataset_meta`'dan ayrı). 10 test + 54 regresyon geçer. Davranış değişikliği yok.
> - **1.3 ✅ (A1)** — İzole sample.duckdb uygulandı: `session.py` `sample_conn()` (ayrı dosya + ayrı exec-lock; close/delete kapsar). `_preview_sample_into_duck` artık leaf kaynakları (raw→SAMPLE(%10), sql→gate) session sample DuckDB'ye **kalıcı** materialize edip fingerprint ile fidelity ledger'a yazar; aynı/komşu türetme açılışında **Oracle'a tekrar gitmez** (eskiden ephemeral `:memory:` + her açılışta taze 5000-pull). `scope_preview_derivation` ephemeral conn yerine `sess.sample_conn()`. Sızma imkânsız (build/Sunum `session.duckdb`). Testler: caching reuse testi + 2 fixture'a SESSION_REGISTRY; **512 scope testi geçer** (1 pre-existing scope_banner fail hariç).
> - **1.4 ✅ (A2)** — Karar: AKTİF (seçili) tablolar Sunum'da gerekli → cache'lenir; DISABLE (inactive) tablolar ASLA persist edilmez, cached türetmenin kaynağıysa Pass 2 `_pull_source_into_duck` ile GEÇİCİ çekilir (parquet yok, `loaded` yok) — "disable ise cache'lenmesin, sadece son tabloyu üretirken kullanılsın". `fetch.py` `_lineage_only` artık TÜM inactive'i Pass 1'de atlar (`needed_views` istisnası kaldırıldı). Test `test_fetch_inactive_main_pulled_transiently_not_persisted` yeni kurala güncellendi. 492 scope testi geçer.
> - **1.5 ✅ (A3)** — Join-key pushdown: bir join'in BÜYÜK/lazy tarafı, KÜÇÜK (zaten registered) tarafın ayrık join-key'leriyle `WHERE col IN (:_kpush_*)` (parametreli) daraltılarak çekilir — tüm lazy tabloyu DuckDB'ye indirmek yerine yalnız eşleşenler. `compose_cached_sql`+`_pull_source_into_duck` `key_pushdown` kwarg; `_join_key_pushdown` (tek-anahtar, lazy taraf, partner registered, ≤1000 anahtar; aksi tam pull). Pass 2'ye bağlandı. 2 test (pushdown + iki-cached-no-pushdown). 494 scope testi geçer.
> - **1.6 ✅ (A4)** — Doğrulama: `hazirlik` route entry'de Oracle'a GİTMEZ (scope S3/draft, routing katalog-only, veri lazy node-tıklamada) → Sunum→Hazırlık dönüşü eager re-fetch yapmaz. Derivation + python preview'ları kalıcı izole sample.duckdb'den (fingerprint) yeniden okur. Python preview ephemeral `:memory:`→`sess.sample_conn()`'a yükseltildi (derivation ile aynı kalıcı store; restart/worker arası dayanıklı). Kalan küçük boşluk (follow-up): filter preview filter-on-Oracle olduğu için (sample-then-filter semantik kararı) ve raw preview (LIMIT 100, ucuz) hâlâ açılışta Oracle'a gider. test_preview_python fixture'ına SESSION_REGISTRY. 494 scope testi geçer.
> - **Oturum 1 ÇEKİRDEK TAMAM (A1–A5).** Kalan: 1.2 (basket-add eager warm + UI "örneklem" rozeti — JSX/bundle gerekir; UX cilası).

Çapa oturum. Şikayetlerin yarısının kök nedeni: tasarım anı tutarlı şekilde sample-DuckDB-backed değil; build full + senkron.

**Hedef mimari (kilitli):**
1. Basket'e tablo gelince **tablo başına %10 oransal sample** DuckDB'ye materialize edilir → tüm preview/transform/python sample üstünde, saniye-altı.
2. Sunum'a geçişte (build) **full data** çekilir (Oturum 2'deki async job ile).
3. Preview'da az satır gösterilir; **sample cache'lenir**.

> **🔒 Karar A:** Sample = **tablo başına %10** (oransal). Uygulama: Oracle `SAMPLE(10)` blok-örneklemesi (full-scan'siz, temsilî).
> Açık nokta (uygulamada netleştir): çok büyük tablolarda %10 hâlâ büyük olabilir → preview hızını korumak için
> üst tavan (ör. min(%10, 200k satır)) düşünülmeli.

### A1 — İlk açılışta preview çok yavaş 🔴
- Kök neden: `routes_scope.py:1940 scope_preview` → DataClient ile Oracle round-trip (`LIMIT 50`), 5 dk process-local cache (`_PREVIEW_CACHE`, `routes_scope.py:69`).
- Beklenti: materialize edilmiş node'un preview'ı **DuckDB'den** gelmeli. Sample-DuckDB mimarisi bunu çözer.

### A2 — Sadece istenen tabloları değil, eşik altı herkesi cache'liyor 🔴
- Kök neden: `routing.py:157 decide_routing` <500MB → "cached", `fetch.py:702` sadece `decision=="cached"` çeker.
  "Hepsi" değil ama transfer niyeti dışındaki tüm küçük tablolar materialize oluyor.
- Plan: "gerçekten gereken"i **lineage'den** türet (bir node'un yalnız ataları). `fetch.py:675` inactive-alias atlama mantığı bunu transfer-niyetine bağlanacak.

### A3 — Büyük lazy tabloyu küçüğe sağdan join'leyince hepsini çekmesin 🔴
- Kök neden: `fetch.py:816 _pull_source_into_duck` lazy kaynağı **cap'siz** çekiyor (`fetch.py:586`, doğruluk gerekçesi).
- Plan: join'de **pushdown** — küçük tarafın join-key'lerini `WHERE key IN (...)` ile lazy tarafa it, sadece eşleşenler insin.

### A4 — Sunum'dan dönünce cache'lenmiş dataset'leri içeriden okusun 🟠
- Kök neden: `materialize.py:639 load_into_duck` zaten parquet'ten okuyor + `refreshed_at` bayatlık kontrolü; teorik doğru.
  Pod restart / session evict → `fetch.py:804` full re-fetch'e düşüyor.
- Plan: A1/A2 düzelince büyük oranda kendiliğinden düzelir; session-persistence sağlamlığı teyit edilecek.

### A5 — Mimari tez: sample her yerde, full sadece Sunum'da 🔴 (çapa)
- Durum: `routes_scope.py:2011` derivation preview 5000 sample yapıyor ama tasarım anı tutarsız (A1).
- Plan: yukarıdaki hedef mimariyi A1–A4 ile birlikte tek katmanda uygula (fetch/routing).

---

## Oturum 2 — Async build + abort + geçiş donmaları

> **DURUM (2026-06-24, branch `feat/oturum-2-async-abort`):**
> - **KÖK NEDEN TEŞHİSİ (Plan ajanı):** Async build altyapısı ZATEN var (`build_scope_async`/`_BUILD_JOBS`/`build-status` polling, frontend `_commitBuild` async'i tercih ediyor). B2/B3'ün asıl kökü: `_run_build_core`, `fetch_cached_tables` boyunca `session._exec_lock`'u (`with session.duck_conn()`) tutuyor → F5 sonrası **orphan daemon worker** kilidi tutmaya devam → yeni build VE Sunum yüklemeleri `_exec_lock.acquire()`'da bloklanıyor ("yeniden geçemedim"/"donuk yükleme"). Worker `daemon=True`, cancel-flag YOK, cancel endpoint YOK, frontend cancel butonu YOK, F5 sunucuya "dur" demiyor. `DataClient.get_data` taze conn açıyor + `finally: conn.close()` YOK (leak) + handle vermiyor → tek dev sorgu ortası iptal `get_data` değişikliği gerektirir.
> - **Sıra:** 2.1 (CancelToken) → 2.2 (fetch'e geçir) → 2.5 (build-cancel endpoint + iptal-temizliği: kilit serbest, scope/manifest YAZMA, re-entry açılır) → 2.6 (frontend iptal + F5/unmount→cancel sendBeacon) → 2.4 (get_data conn-cancel + finally close) → 2.3 (python Popen+erken-kill B4) → 2.7 (editör loading timeout savunma).
> - **2.1 ✅** — `scope/cancel.py` `CancelToken` (cancelled flag + `bind`/`unbind` conn + idempotent thread-safe `cancel()` → conn.cancel()+close() best-effort) + `BuildCancelled`. 5 test. Additive, davranış değişikliği yok (henüz kimse import etmiyor).
> - **2.2 ✅** — `fetch_cached_tables`+`_run_build_core`'a `cancel_token`; Pass-1 pool `with`→manuel + `as_completed` + sınır-check, iptal/hata'da `shutdown(wait=False, cancel_futures=True)` (worker bloklanmaz → `_exec_lock` serbest); Pass-2 her iterasyonda `check()`. `_run_build_core` `BuildCancelled`'da yarım scope/manifest YAZMAZ, re-raise (önceki sürüm yetkili). 2 fetch-cancel testi (pre-cancel pull'suz raise; Pass-1 sırası iptal Pass-2 sınırında yakalanır).
> - **2.5 ✅** — `build_scope_async` job'a `CancelToken`; worker `BuildCancelled`→`phase=cancelled` (yazma yok). `POST /<pid>/scope/build-cancel/<job_id>` → `token.cancel()` + (hâlâ çalışıyorsa) `phase=cancelled`; süresi dolmuş job no-op. 507 scope testi geçer.
> - **NOT:** Loop-level cancel SINIRLARDA fırlar (pre-fetch + Pass-2 + tamamlanan Pass-1 pull arası). Tüm pool aynı anda Oracle'da bloklanırsa in-flight sorguyu unblock **2.4** (conn-cancel) gerektirir. B2'nin "F5→sunucuya dur" tetiği **2.6** (frontend) gerektirir — mekanizma hazır, henüz bağlanmadı.
> - **Kalan:** 2.6 (frontend iptal butonu + F5/unmount→build-cancel sendBeacon — B2/B3 kullanıcı-yüzlü tamamlar), 2.4 (get_data conn-cancel+finally close), 2.3 (python Popen+erken-kill), 2.7 (editör loading timeout).

Tek kök: build, request thread'inde senkron + iptal yok.

> **🔒 Karar B:** Gerçek **abort** — cancel-token fetch döngüsüne enjekte, Oracle cursor kapat/iptal, yarım parquet temizle.

### B1 — Sunum'a geçiş çok uzun 🔴
- Kök neden: `routes_scope.py:1107 _do_build` tüm Oracle çekim + DuckDB türetme + parquet yazımını request içinde senkron yapıyor.

### B2 — F5'te arkada çekmeye devam, iptal sonrası tekrar geçemiyorum 🔴
- Kök neden: `refresh_dispatcher.py`'de iptal yok; build dispatcher'ı zaten kullanmıyor. F5 thread'i bırakır ama arka çekim sürer; yeni "Sunum'a geç" lock/in-flight yüzünden bloklanıyor.

### B3 — Sunum→Hazırlık dönüşü loading'de dondu 🔴
- B1/B2 ile aynı aile; senkron + iptal-edilemez geçiş bir build asılınca iki yönü de kilitliyor.

### B4 — Çok ağır python işlemi uzun sürebilir 🟠
- Aynı async-job altyapısını paylaşır.

**Plan (B1–B4 tek oturum):** build'i background job'a taşı (progress + cancel-token); frontend job-status poll; abort gerçekten thread'i durdursun + temizlik. `refresh_dispatcher.py` temel var, iptal eklenecek.

---

## Oturum 3 — Concept filtreleri

> **DURUM (2026-06-24, branch `feat/oturum-3-concept-filters`):**
> - **3.1 ✅ (C2b)** — `block_cache.cache_key`'e `concept_digest` + `concept_filters_digest` helper; `routes.py apply-filters` aktif concept-filtre durumunun digest'ini anahtara katar → bir dashboard concept filtresi değişince sentinel+değişken taşıyan blok bayat cache yerine yeniden yürür. Boş digest → anahtar pre-Phase-7 ile birebir (regresyon yok). 40 cache + 250 concept/cache testi geçer.
> - **Teşhis (Plan ajanı):** Kullanıcının `FROM block_b_verilen_combo_daily WHERE {{concept_filters}}` örneği türetilmiş view; base-tablo `human_verified` binding'i yok → derleyici ulaşamaz; tek çalışan yol `column_concepts` (Hazırlık kolon→concept binding). Sessizce `1=1`'e düşüyor. Base-tablo binding'lerinin aggregate/calculated türevlerine OTOMATİK taşınması büyük → ertelendi.
> - **3.3 ✅ (C1)** — `routes_scope._seed_concept_filters_at_build`: build'de (Sunum'a geçiş) manifest basket table_ref human_verified binding'leri + column_concepts'ten bağlı TÜM concept'leri dashboard `filters`'a otomatik ekler (yalnız ekler, idempotent, kullanıcı filtresini ezmez; `_filter_proposal_from_concept` lazy-import → all-codes default). `dashboard_filters_to_resolved` filter_state yokken default'a düşer → filter_state seed gerekmez. `_run_build_core`'da set_manifest öncesi çağrılır. 3 test + 497 scope testi geçer.
> - **3.2 ✅ (C2a/c)** — Sentinel taşıyan blok artık SESSİZCE düşmüyor/`1=1` olmuyor: (a) eligibility guard'a `sentinel_present` → değişkensiz/source_tables'sız türetilmiş-view/CTE bloğu render edilir; (b) cache-key öncesi `concept_info`'ya blind raporu → post-loop merge `blind_filters`+`concept_injected:false`'ı sonuca taşır (cache_hit/subset/refetched hepsinde) → UI "filtre uygulanmadı" gösterir; (c) `edit.txt` dürüstlük: LLM bağlayamadığı kolonda "filtre ekledim" demesin, Hazırlık>Konseptler'den bağlanmasını söylesin. Test `test_sentinel_over_unbound_derived_view_is_visible_blind`. 748 (concepts+cache+scope) testi geçer.
> - **3.4 ✅ TAMAM (C3)** — Backend: `_scope_lineage_steps` (yan etkisiz lineage walker) + `GET /<pid>/scope/steps?alias=|block=` (block→find_view_refs ile kaynak alias'ları çözer, sqlparse pretty); 3 test. Frontend (kullanıcı kararı: **Sunum bloğunun altına expandable**): `BlockCard.jsx` `StepsPanel` (aç-kapa, lazy fetch `fetchBlockSteps`), `editor.css`+`editor_dark.css` `.block-steps*` (dark), `editor.html` cache-bust bundle 36→37/editor.css 43→44/dark 19→20. **DEV'de doğrulandı:** 9 toggle render, tıklayınca panel açıldı, kaynak alias çözülüp 1 adım (table + pretty SQL) gösterildi, CSS dark, konsol temiz. bundle.js gitignore → ofiste `bash build.sh` şart.
> - **OTURUM 3 TAMAM (C1+C2+C3).**

> **🔒 Karar C1:** Filtreler **Sunum'a geçişte (build)** otomatik yukarı eklenir — blok eklerken DEĞİL.
> Build anında scope'taki tüm **human_verified bağlı** concept'ler bellidir; **hepsi** dashboard filtresi olarak seed edilir.
>
> **🔒 Karar C3 (ara tablolar):** Read-only yeterli. Son query'nin **step-by-step** nasıl yazıldığı, altta
> **"Show steps" (genişletilebilir)** panelde gösterilecek (CTE/adım dökümü). Materialize yok.

### C1 — Konseptler otomatik filtre olarak eklensin; her query concept filter'ı dikkate alsın 🔴
- Durum: Phase 7 concept'leri için otomatik dashboard-filter üretimi YOK (sadece LLM'e `/filters/-` patch öğretiliyor, `prompts/edit.txt`). Variable-seviyesi oto-bind var (`dashboards/binding.py:31`), concept-seviyesi yok.
- Plan: build sırasında scope'un human_verified concept'lerini topla → her biri için dashboard filtresi seed et (Karar C1).

### C2 — "Ekledim" dedi, kaynakta göründü ama manuel SQL'de yoktu; bloklar filtreye göre güncellenmiyordu 🔴 (gerçek bug)
- Kök neden (iki gerçek + bir UX):
  1. **Cache key'de concept filtre yok** (`routes.py:1882` sadece `resolved` değişkenlere dayalı) → filtre değişince bayat cache hit. "Bloklar güncellenmiyor"un kanıtı.
  2. **Çok-tablolu bloklar enjekte edemiyor** (`integration.py:454`): unqualified kolon → ORA-00918 riski, "blind" işaretleniyor, sessizce uygulanmıyor.
  3. Sentinel `block.query`'de saklanır (kalıcı) ama çalıştırılan SQL'de `1=1`'e döner → UX karışıklığı olabilir; ama verilen örnekte sentinel zaten varken çalışmadı → (1)+(2) ile uyumlu.
- Plan: cache key'e concept-filter-hash ekle + çok-tablolu blokta alias-qualification.

### C3 — Ara tabloları görmek istiyorum (block_b_verilen_combo_daily, daily_count vb.) 🟠
- Kök neden: çok-CTE query'de CTE'ler yalnız query scope'unda, kalıcı değil. Türetilmiş node'lar Basket'te görünür, query-içi CTE'ler görünmez.
- Plan: Karar C3 — "Show steps" read-only panel (CTE SQL'i + örnek çıktı), materialize yok.

---

## Oturum 4 — Dark tema + NaN serileştirme (hızlı kazanım) — ✅ TAMAM (2026-06-24)

> Uygulandı: D3 `duck.py preview_view` → `_jsonable`/`itertuples` (+ regresyon testi `test_duck.py`, 20/20 geçer).
> D1/D2 → `editor_dark.css`'e `.filter-modal*`, `.filter-suggest*`, `.filter-enum-multi__trigger/__row`,
> `.report-title-input` dark override; `editor.html` cache-buster `editor_dark.css?v=18→19`.
> DEV editör (p_demo) computed-style ile doğrulandı (hepsi koyu yüzey + açık metin), konsol temiz.
> Ofis: sadece **restart** (duck.py) — JSX değişmedi, `build.sh` gerekmez; CSS cache-buster zaten bumped.

### D1 — Filtre ekleme UI'ı beyaz + eklenen filtrelerde koyu font 🟢/S
- Kök neden: `editor_dark.css`'te `.filter-modal`, `.filter-suggest-*`, `.filter-enum-multi__panel` dark override YOK (`editor.css:6598+` hardcoded `#ffffff`/`#1e293b`). **Sunum tarafı FilterBar modal'ı.** Hazırlık tarafı (`hz-fp-*`) zaten dark (commit 423fdc8).
- Plan: Sunum FilterBar modal/suggest dark override ekle; Hazırlık tarafını teyit et.

### D2 — Sunum'da başlık ismini değiştirirken beyaz UI 🟢/S
- Kök neden: `.report-title-input` (`editor.css:3788`) `background:#ffffff` hardcoded, `editor_dark.css`'te override yok.

### D3 — Kolon preview'da `NaN is not valid JSON` 🟢/S (net bug)
- Kök neden: `duck.py:186 preview_view` → `df.values.tolist()` ham NaN → `routes.py:719 json.dumps` geçersiz JSON. CLAUDE.md "asla NaN serileştirme" kuralı ihlali.
- Plan: preview değerlerini `duck._jsonable()` (NaN→None) üstünden geçir.

> Not: Bundle gitignored → bu oturum sonunda `bash build.sh` + `editor.html`'de `?v=` cache-buster bump (frontend_bundle_deploy memory).

---

## Oturum 5 — Hazırlık editör UX

### E1 — Üretilen node saçma yere konuyor; kaynağın sağına koysun 🟠
- Kök neden: `index.jsx:3839 posNearSource()` zaten sağa koyuyor (`src.x+360`); kaynak pozisyonu bulunamayınca `nextNodePos()` grid fallback'i devreye giriyor (= bug).
- Plan: `saveAsTable` kaynak node'u güvenilir çözsün; çözemese de sağ-of-source + kardeş istifleme garanti.

### E2 — Hazırlık'ta kaydet butonu yok 🟢/S
- Durum: genel "scope kaydet" yok; sadece bağlamsal ("Filtreyi kaydet" `:2753`, "Tablo olarak kaydet" `:2809`) + auto-save draft (`:3108`) + "Sunum'a geç" (`:4108`).
- **🔒 Karar E2:** Görünür bir **"Kaydet" butonu** eklenecek; **auto-save kalır**, kullanıcı manuel de kaydedebilir (geri-bildirimli).

### E3 — Tablo/edge tıklama bağlamı yanlış ⚠️ (semantik düzeltme)
- **Mevcut (yanlış):** Tabloya tıklayınca LLM chat context'i **"kaynak → tıklanan tablo"** (ilişki/join çerçevesi) oluyor.
- **🔒 Karar E3 (doğru davranış):**
  - **Tabloya tıklama** → context yalnızca **o tablo** (o tablodan yeni node üretme bağlamı).
  - **Edge'e tıklama** → context **o edge'in ürettiği (sağdaki) node** = sağdaki node'u üreten kaynak script/query'yi düzenleme bağlamı.
- İnceleme notu: Explore bulgusu tıklamanın preview drawer açtığını gösterdi; chat-context atama ayrı bir katman — bu oturumda chat-context kurulumunu (kaynak hedefleme) düzelt.
- İlgili dosyalar: `index.jsx:4160 onNodeClick`, `onConnect :3767`, chat-context kurulum mantığı.

### E4 — Kolona uygun konsept var/yok kontrolü yavaş 🟠
- Kök neden: `index.jsx:3045` konsept seçici açılınca `/scope/suggest-concepts`'e per-column fetch, timeout yok, yanıt gelene kadar UI bekliyor. Backend katalog mı LLM mi → netleştir.
- Plan: öneri önbelleği + spinner; mümkünse tablo dokümante edilince önden hesapla.

---

## Oturum 6 — Filtre UX (binlik ayraç + tarih toggle)

### F1 — Aralık konseptinde alt/üst değerlere binlik ayraç (virgül) 🟢/S
- Kök neden: `FilterPanel.jsx:236` `<input type="number">` binlik ayraç gösteremez.
- Plan: `type="text"` + locale formatlı görüntü (`toLocaleString('tr-TR')`) + parse.

### F2 — Tarih konseptinde aralık VEYA tekil gün seçimi 🟢/S
- Durum: bugün sağ kutuyu boş bırak = tekil gün (`buildSpecs :134`), yalnız ipucu metni; açık toggle yok.
- Plan: "Tekil gün / Aralık" radio'su (`FilterPanel.jsx:205–224`).

---

## Oturum 7 — LLM JSON dayanıklılığı

### G1 — Büyük çok-karosel çıktıda JSON parse hatası 🔴
- Kök neden: `llm.py:113 generate_patches` **max_tokens=2048** → büyük dashboard (3 karosel × ~3 slide) bunu aşıp JSON ortadan kesiliyor (`char 7658` ≈ kesilme). `finish_reason` truncation kontrolü yok, JSON onarımı yok, retry yok (sadece discovery path'inde retry var).
- **🔒 Karar G:** **max_tokens yükseltilecek** (model limitine göre 4096+).
- Destekleyici plan: `finish_reason=="length"` truncation tespiti + JSON onarımı (açık `[`/`{` kapat) + bir retry. (Çok büyük dashboard'u karosel başına parçalı üretme backlog'da.)

---

## Oturum 8 — Audit logging (Oracle tablosu)

### H1 — Sağlam audit log: kim, hangi prompt, hangi tablo, hangi query, LLM I/O, session bağlantısı 🟠
- Durum: loglama minimal/dağınık; prompt/yanıt hiç saklanmıyor; korelasyon id yok (`session.py` user_id+presentation_id var ama node state'ine taşınmıyor).
- **🔒 Karar H:** **Oracle tablosuna** yaz. Bu oturumda: CREATE DDL yaz + insert mekanizmasını koda kurgula.

**Önerilen tablo (uygulama oturumunda kesinleştir):**

```sql
CREATE TABLE PRISMA_AUDIT_LOG (
  ID              NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  EVENT_TS        TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  USER_SICIL      VARCHAR2(32),
  SESSION_ID      VARCHAR2(64),
  PRESENTATION_ID VARCHAR2(64),
  REQUEST_ID      VARCHAR2(64),       -- pipeline boyunca korelasyon
  STAGE           VARCHAR2(24),       -- kesif | hazirlik | sunum
  EVENT_TYPE      VARCHAR2(48),       -- prompt | llm_request | llm_response | table_produced | query_written | scope_build | error
  PROMPT          CLOB,
  LLM_REQUEST     CLOB,
  LLM_RESPONSE    CLOB,
  SQL_TEXT        CLOB,
  TABLE_REF       VARCHAR2(256),
  META_JSON       CLOB,
  DURATION_MS     NUMBER,
  FINISH_REASON   VARCHAR2(32),       -- LLM truncation tespiti (G1 ile bağlı)
  ERROR_TEXT      CLOB
);
```

**Insert hook noktaları (file:line):**
- `llm.py:121–134` — LLM request/response (prompt, response, finish_reason, duration).
- `nodes/generate_patch.py:142–211` — üretilen patch/SQL, validation sonucu.
- `routes_scope.py` build olayları — table_produced / query_written / scope_build.
- `routes.py` chat girişi — user prompt + sicil.

**Uygulama notları:**
- Yazma **mevcut bağlantı mekanizmasıyla** (`dc.get_connection`, size_estimate'teki gibi) — asla raw `oracledb` değil.
- Audit yazımı request'i yavaşlatmasın: **async/batched** (kuyruk + arka thread) — best-effort, hata request'i düşürmesin.
- FINISH_REASON alanı G1 (truncation tespiti) ile aynı veriyi yakalar → iki oturum birbirini besler.
- PII: prompt'lar kullanıcı verisi içerir; saklama/erişim politikası netleştirilecek.

---

## Açık uygulama notları (genel)
- Bundle değişiklikleri gitignored → her UI oturumu sonunda `bash build.sh` + ilgili `?v=` cache-buster bump (frontend_bundle_deploy memory).
- DEV ↔ PROD parity: her fix'in prod karşılığını da güncelle (feedback memory).
- Ölü kod bırakma; düzenlemeden önce gerçekten import edildiğini doğrula.
