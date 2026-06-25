# Ofis Kullanım Backlog'u — 2026-06-25 (2. tur)

2026-06-25 ofis testinde, 2026-06-24'te tamamlanan 8 oturumun ([BACKLOG_OFIS_2026_06_24.md](BACKLOG_OFIS_2026_06_24.md)) ardından çıkan yeni bulgular. Çoğu önceki işin derinleşmesi / kenar durumu / küçük düzeltmesi. Kullanıcı kararları baked-in.

## Durum tablosu

| # | Oturum | Maddeler | Öncelik | Bağlı önceki iş |
|---|--------|----------|---------|------------------|
| N1 | Concept routing | A1, A2, A4 | 🔴 çapa | Oturum 3 (C1/C2) |
| N2 | Concept çevre | A3, C3 | 🟠 | Oturum 3.4, 5 (E4) |
| N3 | Audit log tamamla | B1 | 🟠 | Oturum 8 |
| N4 | Chat/LLM hızlı küme | B3, B4, B5 | 🟢 | Oturum 7 |
| N5 | Hazırlık UX | C1, C2, C4 | 🟠 | Oturum 5 (E3) |
| N6 | Veri/build | D1, D2 | 🔴 | Oturum 1 |

> **DÜŞÜLEN:** B2 (chat history per-context) — kullanıcı vazgeçti.

---

## Oturum N1 — Concept routing (çapa, en kritik) — ✅ TAMAM (branch `feat/oturum-n1-concept-routing`)

> **DURUM:** A1+A2+A4 uygulandı, saf backend (bundle yok → ofis sadece restart). 779 test geçer (tek pre-existing scope_banner fail).
> - **A1+A2 ✅** — `routes.py apply-filters` path 2 genişletildi: SQL **herhangi bir basket alias'ı** referans ediyorsa (`find_view_refs`, TÜM alias'lar; değişken/`:bind` blokları hariç) blok **DuckDB'de** koşar — sentinel/binding olmasa, source_tables Oracle tablosu gösterse bile. Eskiden sentinelsiz/binding'siz türetilmiş-view bloğu path 3'e (Oracle) düşüp `ORA-00942` alıyordu. Sentinel+binding varsa concept enjekte edilir; yoksa SQL olduğu gibi DuckDB'de koşar + aktif filtreler blind raporlanır. Catalog-tablo blokları (basket'te alias yok) hâlâ path 3 (Oracle, doğru). Test: `test_derived_view_block_runs_in_duckdb_not_oracle`.
> - **A4 ✅** — `_seed_concept_filters_at_build` build'den ÇIKARILDI; `chat_stream`'de **ilk chat sonrası** `_filters_seeded` flag'iyle bir kez seed edilir (Sunum açılır açılmaz gelmez; kullanıcı silerse geri gelmez).

> **🔒 Karar A1/A2:** Sunum aşamasında **türetilmiş tablolar Oracle'a GİTMEZ, DuckDB'den okunur**. Concept-filtreli blok türetilmiş/scope view üstündeyse enjekte edilmiş SQL DuckDB'de çalışmalı.
>
> **🔒 Karar A4:** Oto-seed filtreleri **build/Sunum-açılışta değil, İLK PROMPT'tan sonra** eklenir (sadece as_of_date değil, HEPSİ).

### A1 — `ORA-00942: table or view does not exist` (concept-filter → Oracle, türetilmiş view) 🔴🔴
- Repro: konsept filtre atınca `[apply-filters] b_offered_cum [oracle]: ORA-00942`.
- Kök neden (hipotez): [routes.py apply-filters](presentations/routes.py) concept enjeksiyon yolu (path 3 — catalog tablo) enjekte SQL'i `dc.get_data` ile **Oracle'a** gönderiyor. `b_offered_cum` türetilmiş scope view (DuckDB'de var, Oracle'da yok) → ORA-00942. Sunum'da parquet/DuckDB beklerken Oracle'a çıkıyor.
- Plan: concept-injected blok kaynağı türetilmiş/scope alias ise enjeksiyon **DuckDB'de** koşmalı (path 2 / `inject_dataset_concepts` + `hydrate_block_datasets` ile), Oracle'a düşmemeli. Oturum 3'te "blind" yaptığımız durumun ERROR'a dönen hâli — burada düzgün DuckDB routing'i lazım.

### A2 — konsept bağlı filtre çalışmıyor 🔴
- Doğru bağlı bir concept'te bile filtre uygulanmıyor. A1 ile aynı aile: ya Oracle'a düşüp patlıyor ya sessizce uygulanmıyor. **A1 ile tek oturum.** Doğrulama: human_verified binding'li bir kolonda filtre değişince blok gerçekten yeniden çalışıp filtreli veri dönüyor mu (cache-key 3.1 sonrası).

### A4 — oto-filtreler ilk prompttan sonra 🟢
- `routes_scope._seed_concept_filters_at_build` (Oturum 3.3) build'de ekliyor → "Sunum açılır açılmaz sağ alta geliyor". Plan: seed'i build'den çıkar, **ilk chat prompt'undan sonra** çalıştır (chat pipeline / ilk apply-filters anında, idempotent). Tüm filtreler için.

---

## Oturum N2 — Concept çevre cilası — ✅ TAMAM (branch `feat/oturum-n2-concept-cevre`, N1 üstüne)

> **DURUM:** A3+C3, saf backend (bundle yok → ofis restart). 509 scope testi geçer (tek pre-existing scope_banner fail).
> - **A3 ✅** — `scope_suggest_concepts` hâlâ ephemeral `:memory:` kullanıyordu (1.3'te derivation preview düzeltilmişti ama bu endpoint kalmış) → her concept-suggest'te Oracle'dan sample çekiyordu. Fix: `sess.sample_conn()` (kalıcı, fingerprint-reuse) + `(pid,alias,column,scope)` sonuç memo'su. Client cache (Oturum 5) + bu server memo birlikte → tekrar açılış anında.
> - **C3 ✅** — `_extract_cte_steps`: bloğun KENDİ SQL'indeki `WITH <ad> AS (...)` CTE'lerini (string/parantez-farkında) adım olarak çıkarır; `scope_steps` bunları scope-view lineage'ından sonra ekler → "ara adım yok" sandığımız CTE'li bloklar artık ara adımları gösterir. Frontend "Adımlar" paneli adımları generic render ediyor → bundle gerekmez. 2 test.

### A3 — konsept seçince tarama çok yavaş 🟠
- Oturum 5'te client cache + 8s timeout eklendi ama **ilk-dokunuş Oracle sample pull'u** yavaş. Plan: E4 backend memo (suggest sonucu `(pid,alias,column,fingerprint)` ile process-cache) + basket-add eager warm (Oturum 1 1.2). Önce ofiste `bash build.sh` ile client cache devrede mi teyit.

### C3 — "ara adım yok" diyor ama ara tablo kullanmış 🟠
- Oturum 3.4 `_scope_lineage_steps` yalnız **scope-basket türetmelerini** yürüyor; bloğun **kendi SQL'indeki CTE'leri** (`WITH ...`) görmüyor → CTE'li blokta "ara adım yok, doğrudan kaynak tablo" diyor. Plan: lineage'a blok SQL'inin CTE çıkarımını ekle (sqlparse ile WITH bloklarını adım olarak göster).

---

## Oturum N3 — Audit log tamamla (B1) — ✅ TAMAM (branch `feat/oturum-n3-audit-tamamla`, N1+N2 üstüne)

> **DURUM:** Saf backend (audit hook'ları) → ofis restart. 18 audit/concept testi geçer.
> - **B1 ✅** — Hook'lar artık LLM'in **ürettiği kodu** da yazıyor: Sunum chat → stream'lenen patch'ler toplanıp `SQL_TEXT`'e (JSON) + `META_JSON`'a `patch_count`; Hazırlık scope-chat → öneriler `SQL_TEXT`'e. `LLM_RESPONSE` zaten açıklamayı yazıyordu. Böylece "yazdığı kod yok" çözülür.
> - **CLOB okuma notu:** veri tabloda VAR; bazı SQL client'ları CLOB'u kısaltıp boş gösterir → `SELECT DBMS_LOB.SUBSTR(SQL_TEXT,4000,1) FROM PRISMA_AUDIT_LOG` ile bakılır.
> - **KALAN (opsiyonel):** `LLM_REQUEST` (LLM'e giden TAM composed prompt) hâlâ boş — `generate_patches`/`suggest_scope_refinements`'ten composed metni dışarı vermek gerekir (daha derin plumbing). `PROMPT` zaten kullanıcının yazdığını tutuyor.

### B1 — audit log'da LLM response/kod boş ("dümenden yazıyor") 🟠
- Açıklama: `PRISMA_AUDIT_LOG`'ta response açıklama vermiyor, yazdığı kod yok ("CLOB'dan dolayı mı?").
- Kök neden: Oturum 8 hook'ları yalnız `prompt` + `llm_response`(explanation) yazıyor. `LLM_REQUEST` (gönderilen ham prompt) ve `SQL_TEXT` (LLM'in ürettiği patch/kod) **doldurulmuyor** (None). No-op chat'te `llm_response` da boş kalabilir.
- Plan: hook'ları zenginleştir — `llm_request` = composed prompt; `sql_text`/`meta_json` = üretilen patch'ler/SQL; Sunum chat'te patch özetini de yaz. (CLOB okuma: bazı SQL client'ları CLOB'u kısaltır — `DBMS_LOB.SUBSTR` ile bakılır; veri var.) `generate_patches`'a request/response/finish_reason/duration döndüren ince bir kanal.

---

## Oturum N4 — Chat/LLM hızlı küme

### B3 — QwenClient timeout 300 🟢 **tek satır**
- [llm.py](presentations/llm.py) `QwenClient.__init__` `timeout: int = 60` → **300**.

### B4 — Sunum açılışta hata → promptu temizle 🟢
- Sunum chat hata alırsa prompt input'u temizlensin (frontend, editor ChatBox).

### B5 — chat kısmı biraz büyüsün 🟢
- Chat paneli yüksekliği/genişliği artır (CSS).

---

## Oturum N5 — Hazırlık editör UX

> **🔒 Karar C1 (E3 düzeltmesi — 2026-06-24'teki E3'ün tersi/rafine):**
> - **Edge tıklama** → **KAYNAK** node'u açılır + chat context **"source → target"** (edge'deyken kaynağı/üreten query'yi editliyoruz).
> - **Node tıklama** → **Veri** sekmesi + **sadece o tablo** context'i (node'dayken yeni node üretiyoruz).

### C1 — edge/node tıklama context'i 🟠 ⚠️
- Durum: Oturum 5 (E3) filter edge'i çıktı(türev) node'unu açacak + chip'i sade-alias yaptı. Kullanıcı şimdi tersini netleştirdi (yukarı karar).
- Plan: (a) edge-click → `showPreview(sourceAlias)` (kaynağı aç); (b) chip'te **edge bağlamında** "source → target" geri gelsin (node bağlamında sade-alias kalsın) → `selectedSource` yalnız edge-click'te set edilir. E3'teki filter-edge `out` değişikliği ve chip sade-alias geri alınır/koşullanır.

### C2 — cachelenmemiş tabloya python: uyarı güzel ama "apply deny" kalıyor 🟠
- Uyarı çıkıyor ama kod uygulanamıyor (apply butonu kilitli). Plan: cachelenmemiş kaynakta python apply-deny state'inin neden kaldığını bul; uyarı sonrası apply yolu açılmalı (ya da net "önce cache/sample" akışı).

### C4 — soldaki liste kapandıkça hareket etmesin 🟢
- Hazırlık'ta sol liste collapse olunca layout shift oluyor. CSS sabit genişlik/transform.

---

## Oturum N6 — Veri / build (🔴)

### D1 — projection-node-arkası büyük tablo join'de build patlak/yavaş 🔴
- Repro (kullanıcı): ufak üretilmiş tablo var; büyük bir tablodan bazı kolonları seçip kaydediyorum (projection node), sonra ufak tabloya join'liyorum. **Hazırlık'ta sorun yok, doğru gösteriyor; ama build'de o kolonları seçtiğim ana (büyük) tabloyu çekemiyor — bitmiyor, çok yavaş.**
- Kök neden (hipotez): projection node'un arkasındaki büyük kaynak tablo build'de **tam/cap'siz** çekiliyor (lazy source). A3 join-pushdown (Oturum 1.5) yalnız doğrudan join derivation'ında devrede; araya **projection node** girince pushdown ulaşmıyor → büyük tablo full pull → yavaş/asılı.
- Plan: projection (column-select) node'un lazy büyük kaynağı için de sample/pushdown ya da build-time daraltma; join lineage'ında projection'ı geçişli ele al.

### D2 — tahmini kullanım düzgün çalışmıyor 🟠
- Boyut/kullanım tahmini (EXPLAIN-plan / `size_estimate` / routing estimate) hatalı. İncele: `routing.estimate_post_scope_size` + `scope/size_estimate.py` (partition_column/estimated_daily_rows önkoşulları).

---

## Açık uygulama notları
- Her UI oturumu sonunda `cd presentations && bash build.sh` + `?v=` bump (frontend_bundle_deploy memory).
- N1 çapa; D1 ile birlikte en kritik veri-doğruluk/performans işleri.
- DEV ↔ PROD parity; ölü kod bırakma.
