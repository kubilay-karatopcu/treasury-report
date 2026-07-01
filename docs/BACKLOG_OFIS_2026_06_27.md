# Ofis Backlog — 3. Tur (2026-06-27)

2026-06-26/27 ofis kullanımında çıkan 9 madde. Kod analizi (5 paralel ajan) + kullanıcı
kararları sonrası 7 oturuma bölündü. İlgili: [[office-backlog-2026-06-24]] (1+2. tur).

> **Akış:** her oturum kendi branch'inde (feat/oturum-mN-…) → commit → main'e merge → push.
> Kullanıcı "merge push" der, sonra sıradaki oturumu söyler. UI değişen her oturum sonunda
> `cd presentations && bash build.sh` + ilgili `?v=` cache-buster bump.

## Durum

| # | Oturum | Maddeler | Tip | Risk | Durum |
|---|---|---|---|---|---|
| M1 | Hazırlık sol panel sadeleştirme | 1 | frontend/bundle | 🟠 | ✅ TAMAM |
| M2 | Audit kapsama (keşif + build) | 2 | backend | 🟢 | ✅ TAMAM |
| M3 | LLM dürüstlüğü: Sunum "sor" + Hazırlık kolon-eşleme | 5, 9 | prompt + context | 🟠 | ✅ TAMAM |
| M4 | Konsept menü perf (validate yalnız detaylı-ara) | 4 | front+back | 🟠 | ✅ TAMAM |
| M5 | Sunum filtre UX: özel-filtre kaldır + her-geçişte seed | 6, 7 | front+back | 🟠 | ✅ TAMAM |
| M6 | Sunum concept-filter motoru: tek DuckDB query, ara-tablo yok, ORA-00942 | 8 | backend | 🔴 | beklemede |
| M7 | Join yeniden tasarım (lazy↔lazy Oracle / cached↔cached) | 3 | full-stack | 🔴 | beklemede |

> Sıra: M1→M5 (kolay + dürüstlük), sonra M6 (concept-motoru) → M7 (join). M6, M7'nin
> veri-katmanını temizler → önce M6.

## 🔒 Kilitli kararlar

- **K1 (M1):** Üstteki global butonlar (Keşife Dön / SQL Tablo / Excel) **olduğu yerde kalır**;
  yalnız **Tablolar listesi** + Tahmini kullanım + Tablo ara kaldırılır. Aktive/deaktive göz
  toggle'ı **canvas node'larına** taşınır.
- **K3 (M7):** (a) Lazy↔lazy Oracle join **sonucu CACHE'LENİR** (yeni cached node; kullanıcı
  kolon+filtreyi baştan seçtiği için sonuç küçük). (b) Mevcut karışık join'ler **silinecek**
  (kullanıcı test ediyor) — migration gerekmez, ama yeni karışık join validation'la reddedilir.
  (c) **Basit sürüm:** yeni kural + tüm-pipeline **sıralı** yeniden çalıştır + node-başı hata
  rozeti; tam streaming edge-edge re-run **sonraki tura**.
- **K4 (M4):** "Konsept uyumlu mu" doğrulaması **yalnız "Detaylı ara"da** çalışır (otomatik
  selection-validation kaldırılır). + memo (suggest-concepts gibi) eklenir.
- **K8 (M6):** Hazırlık-alias'lı bloklar **Oracle'a hiç gitmez** — her zaman cache'li DuckDB
  view'ları üzerinden **tek query** (uzunsa `WITH`) + `concept_filters` **her final query'de**.
  `variables` olsa bile kapı-2 (DuckDB) zorlanır. Saf-Oracle (Hazırlık'sız katalog tablosu)
  bloklar Oracle'da kalır.

---

## Oturum M1 — Hazırlık sol panel sadeleştirme (madde 1) — ✅ TAMAM

**Yapıldı:** Sol "Tablolar" listesi + `BudgetPanel` + `Tablo ara` kaldırıldı (ölü `BudgetPanel`/
`VizSlot` fonksiyonları + `.hz-budget*`/`.hz-basket-row__viz-btn*` CSS + `tableSearch`/
`tablesFiltered` silindi). Üstteki Keşife Dön + SQL Tablo/Excel kaldı. Göz(aktive/deaktive) +
docs + sil(pasifken) + sql-edit aksiyonları `TableNode` başlığına taşındı (`NODE_HANDLERS`
singleton'a kaydedildi; `nodrag`+`stopPropagation` → drawer açılmaz/sürüklenmez). Chat
`min-height 320→440` + sources `flex:0 0 auto; max-height:45vh` → chat boşalan alanı doldurur.
`hazirlik.bundle.js v44→45`, `hazirlik.css v26→27`. Canlı doğrulandı (1440×900: sources 109px
kırpılmıyor, chat 631px; göz-toggle drawer açmadan çalışıyor; 0 console error).

**Kök neden:** Sol panel `Tablolar` listesi + `BudgetPanel` (Tahmini kullanım) + `Tablo ara`
artık önemini yitirdi; chat sıkışık.

**Plan** ([index.jsx](presentations/static/js/hazirlik/index.jsx)):
- **Kaldır:** sol `hz-basket-group` (tablo + türev satırları, ~1717-1838), `BudgetPanel`
  fonksiyonu (1505-1532) + render'ı, `Tablo ara` (`tableSearch`/`tablesFiltered` state+input+filter).
- **Kalır:** "Veri Kaynakları" başlığı + Keşife Dön + SQL Tablo/Excel ekle butonları (üstte).
- **Taşı:** `VizSlot` göz (aktive/deaktive) + per-tablo docs(Info)/SQL-edit ikonları →
  `TableNode` başlığına (canvas node, ~309). Node verisine `hidden`/`onToggle`/`onOpenDocs` geç.
- **Chat:** sol section'ı yukarı uzat (CSS — hazirlik.css chat flex'i zaten esnek).
- **Not:** Node rozetleri (cached/lazy + boyut) KALIR (M7 lazy/cached için gerekli); `refineSizes`
  node-rozetini beslediği için kalır, yalnız budget toplamı/arama gider.

**Deploy:** hazirlik.bundle.js + hazirlik.css ?v bump.

---

## Oturum M2 — Audit kapsama (madde 2) — ✅ TAMAM

**Yapıldı:** Keşif chat hook'u ([routes_kesif.py](presentations/routes_kesif.py) `kesif_chat_send`)
— 3 dönüş yolunda da (başarı/DiscoveryError/LLM-yok) `audit.log_event("kesif_chat", stage="kesif",
prompt, llm_response, sql_text=öneriler, meta)`. Hazırlık **build** event hook'u
([routes_scope.py](presentations/routes_scope.py) `_run_build_core`): başarıda `audit.log_event(
"scope_build", stage="hazirlik", table_ref="N cached / M lazy", meta={version, cached, lazy})`,
hatada `error_text`. Test: `test_chat_logs_audit_event` (catalog). Saf backend (restart).

**Kök neden:** `audit.log_event` Sunum chat (routes.py:928) + Hazırlık scope-chat
(routes_scope.py:3273) **var**; **Keşif** chat (`kesif_chat_send`) + Hazırlık **build**
olayları audit'siz.

**Plan:**
- Keşif chat hook'u: `kesif_chat_send` LLM dönüşü sonrası `audit.log_event("kesif_chat",
  stage="kesif", prompt=…, llm_response=…, sql_text=öneriler)`.
- Build event hook'u: scope build tamamlanınca `audit.log_event("scope_build", stage="hazirlik",
  table_ref=üretilen alias'lar, meta=…)`.
- Test: test_audit'e keşif/build event smoke.

**Deploy:** saf backend (restart).

---

## Oturum M3 — LLM dürüstlüğü (madde 5 + 9) — ✅ TAMAM

**Yapıldı:** **Madde 5** — [edit.txt](presentations/prompts/edit.txt)'e "EMİN DEĞİLSEN SOR" kuralı:
talep tabloyu/kolonu katalogdan kesin çözemezse `"patches": []` + `explanation`'a soru →
mevcut noop yolu (graph.py:136 → ChatBox `onStatus phase:'noop'`) soruyu gösterir, SQL üretmez.
**Madde 9** — `_columns_for`'a `description` eklendi; `compose_scope_user_message`'a
`selected_columns_meta` param + ODAK bloğunda "kolon dokümanı (ad+concept+açıklama)" render
(QwenClient+FakeLLM `suggest_scope_refinements` + scope_chat plumb'landı); [scope_refine.txt](presentations/prompts/scope_refine.txt)'e
"KOLON ADI SADAKATİ" kuralı: kullanıcının yazdığı kolonu ad/açıklama/semantik/yazımdan gerçek
kolona EŞLE, birebir kopyalama. Test: `test_compose_renders_column_docs_for_mapping`. Saf
backend+prompt (restart; bundle YOK — noop UI zaten var).

**Madde 5 — Sunum belirsizse SOR:**
- Kök neden: `edit.txt` "uydurma" diyor ama "emin değilsen sor" yok; hata-retry döngüsü tahmin ediyor.
- Plan: [prompts/edit.txt](presentations/prompts/edit.txt)'e kural — *talep tabloyu/kolonu katalogdan
  kesin çözemiyorsa SQL yazma, kullanıcıya soran kısa açıklama döndür*. Graph `noop`+explanation
  yolunu kullan (ChatBox `onStatus phase:'noop'` zaten gösteriyor). generate_patch'te "clarification"
  çıktısını noop'a maple.

**Madde 9 — Hazırlık kolon adı eşleme:**
- Kök neden: scope-chat'e seçili node'un **sadece kolon ADLARI** gidiyor; tip/açıklama/semantik/örnek yok;
  düzeltme talimatı yok.
- Plan: [routes_scope.py](presentations/routes_scope.py) `scope_chat` → user mesajına seçili node'un
  **tam kolon dokümanı** (ad+tip+açıklama+semantic+örnek değer) enjekte (`compose_scope_user_message`,
  llm.py). [prompts/scope_refine.txt](presentations/prompts/scope_refine.txt)'e kural — *kullanıcının
  yazdığı kolon adı listedekiyle birebir değilse anlam/açıklama/yazımdan en yakın GERÇEK kolona EŞLE;
  query/python'u her zaman doküman adlarıyla yaz*.

**Deploy:** backend + prompt (restart); generate_patch noop için bundle gerekebilir.

---

## Oturum M4 — Konsept menü perf (madde 4) — ✅ TAMAM

**Yapıldı (K4):** Concept SEÇİMİNDE otomatik "uyumlu mu" doğrulaması TAMAMEN kaldırıldı
(yavaştı: her seçimde ephemeral DuckDB örnekleme + 500 DISTINCT, memo'suz → UI donuyordu).
Frontend: `ConceptsTab` validation makinesi (valid state/runValidate/lastValidated/effect/
status rozetleri), `validateConcept` handler, `VALIDATE_CONCEPT_URL`, prop zinciri silindi.
Backend: `/scope/validate-concept` route + `test_validate_concept.py` silindi (paylaşılan
`_preview_sample_into_duck` vb. helper'lar kaldı). Ölü CSS (`.hz-concept-warn/-ok/-msg`) temizlendi.
Uygunluk artık YALNIZ "Detaylı ara" panelindeki sıralı öneri (suggest — N2/A3 memo'lu) üzerinden.
Canlı doğrulandı: validate-concept 404, Konseptler tab 0 rozet, 0 console error. bundle v45→46,
hazirlik.css v27→28.

**Şu an:** `/validate-concept` her seçimde ephemeral DuckDB + 500 DISTINCT, **memo YOK** → 2-4 sn.
`/suggest-concepts` zaten N2/A3 memo'lu, yalnız "Detaylı ara"da.

**Plan (K4):**
- Otomatik selection-validation'ı kaldır (ConceptsTab effect, ~2288) → konsept seçilince "?" durumu.
- Doğrulamayı yalnız "Detaylı ara" / ConceptBrowser'da "Doğrula" ile çalıştır.
- `validate-concept`'i `sess.sample_conn()` + `_preview_cache` memo ile cache'le (suggest-concepts gibi).

**Deploy:** front+back.

---

## Oturum M5 — Sunum filtre UX (madde 6 + 7) — ✅ TAMAM

**Yapıldı:** **Madde 6** — [FilterBar.jsx](presentations/static/js/editor/components/FilterBar.jsx):
"Özel filtre ekle…" butonu + `ManualFilterForm` (103 satır) + AddFilterModal 'manual' dalı +
`view` state kaldırıldı; modal artık yalnız önerilen filtreler (SuggestionList). Canlı doğrulandı:
modal "Filtre Ekle", özel-filtre butonu YOK. **Madde 7** — concept filtre seed'i chat_stream'den
(A4, ilk-promptta yüklenmiyordu → bug) BUILD'e taşındı (`_run_build_core` her Hazırlık→Sunum
geçişinde `_seed_concept_filters_at_build`; idempotent cross-check). UI: [App.jsx](presentations/static/js/editor/App.jsx)
FilterBar `sections.length > 0` ile gate'lendi → chat ORTADAYKEN (empty-start) gizli, chat SOL
panele geçince (blok var) render. `bundle v38→39`. 32 build/seed testi geçer.

**Madde 6 — Özel filtre butonu kaldır:**
- [FilterBar.jsx](presentations/static/js/editor/components/FilterBar.jsx): "Özel filtre ekle…"
  butonu (561) + `ManualFilterForm` (612-711) + AddFilterModal 'manual' dalı sil. Backend endpoint YOK.

**Madde 7 — Filtreler her geçişte yüklensin:**
- Kök neden: `_seed_concept_filters_at_build` yalnız `chat_stream` ilk-prompt'ta (`_filters_seeded`
  flag), build/editör-açılışında değil.
- Plan: seeding'i **build tamamlanınca / editör manifest dönerken** de çalıştır (tablo-konseptleri ↔
  dashboard `filters` cross-check, yeni varsa ekle, idempotent). FilterBar koşulu filtre varsa zaten
  render ediyor; ilk-prompt farkı yalnız UI (chat ortada).

**Deploy:** front+back.

---

## Oturum M6 — Sunum concept-filter motoru (madde 8) 🔴

**Kök neden:** Hazırlık-türev-view'una referanslı AMA `variables`'lı blok → apply-filters kapı-2
(`not block.get("variables")`) atlanıyor → Oracle kapısı → view Oracle'da yok → **ORA-00942**
(`b_kumulatif_offered`). Ayrıca concept_filters her query'de değil; elle eklenen concept_filters çalışmıyor.

**Plan (K8) — apply-filters + chat_stream'i renove et** ([routes.py](presentations/routes.py) ~1620-2030):
- Hazırlık-alias referanslı bloklar (`find_view_refs` > 0) **her zaman DuckDB kapı-2** — `variables`
  şartını kaldır; `inject_dataset_concepts` ile `concept_filters` her final query'de.
- LLM blok-üretim promptu: kaynak Hazırlık view'larından **tek query** (uzunsa `WITH`), **ara tablo
  ÜRETME**, `{{concept_filters}}` sentinel'i WHERE'e her zaman koy.
- Oracle kapısı (3/4) yalnız **saf-Oracle** (Hazırlık-alias'sız) bloklar için.
- "Ara adımlar göster" zaten yok → doğrula/temizle.
- Test: ORA-00942 repro (variables'lı türev-view bloğu) → DuckDB'ye gitsin; concept_filters injection.

**Deploy:** backend + LLM prompt (+ bundle gerekebilir).

---

## Oturum M7 — Join yeniden tasarım (madde 3) 🔴

**Kök neden:** Tüm join'ler DuckDB; lazy kaynak join'e girince **tüm tablo uncapped DuckDB'ye
çekiliyor** (RAM'e sığmazsa patlar). Lazy↔cached engeli yok. Build ilk hatada tümden patlar.

**Plan (K3 — basit sürüm):**
- **Kural:** `validators.py` yeni `rule_join_routing_compatibility` — lazy yalnız lazy ile,
  cached yalnız cached ile joinlenir (karışık → validation hatası). Frontend join modalında ön-kontrol.
- **Lazy join → Oracle:** `fetch.py` yeni `compile_lazy_join_sql(item, scope, catalog)` — iki lazy
  kaynağı Oracle'da joinler (concept/partition pushdown + join koşulu birlikte), sonucu **cache'ler**
  (yeni cached node). Cached↔cached mevcut DuckDB yolu.
- **Kolon seçimi:** kullanıcı join'lerken getirilecek kolonları baştan belirler (cache planı);
  diğer cached tablolardan kolon eklenebilir.
- **Re-run (basit):** başa lazy-join eklenince downstream node'lar yeniden çalışır (mevcut
  `close_affected_over_derivations` + refetch_only); **sıralı** çalışsın; node-başı hata olursa
  o node'da **error rozeti** + dur (scope.status.node_statuses), kalanı beklet.
- **Node UI:** yeni `is-pending` / `is-error` state'leri (mevcut is-inactive'den farklı görünüm).
- **Streaming edge-edge re-run + canlı per-node aktivasyon → sonraki tur.**

**Deploy:** full-stack (backend + bundle). En riskli; dikkatli test.

---

## Açık uygulama notları
- DEV ↔ PROD parity; ölü kod bırakma.
- Her UI oturumu: `bash build.sh` + `?v=` bump.
- M6 + M7 veri-katmanı; en kritik doğruluk/performans işleri — sona, dikkatli.
