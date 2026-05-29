# Treasury Studio — Keşif Aşaması (Akış Dokümanı)

> Bu bir **stage (akış) dokümanıdır**: Keşif aşamasının backend'de uçtan-uca
> nasıl çalıştığını (istek → cevap, hangi state nereye yazılıyor) anlatır.
> Entity tarafı için: [`BACKEND_TABLOLAR.md`](BACKEND_TABLOLAR.md). Sıradaki
> aşama: [`BACKEND_HAZIRLIK.md`](BACKEND_HAZIRLIK.md).

---

## Önsöz — Keşif ne yapar?

Keşif, kullanıcının **"hangi tabloları kullanacağım?"** sorusunu cevapladığı
aşamadır. Kullanıcı doğal dilde yazar ("kredi portföyümü segment + şube
bazında incelemek istiyorum"), sistem katalogdan tablolar önerir, kullanıcı
beğendiklerini bir **sepete (basket)** ekler. Sepet dolunca "Hazırlığa Geç"
ile bir sonraki aşamaya devredilir.

Keşif boyunca henüz **gerçek bir sunum yoktur** — her şey bir **taslak (draft)**
üzerinde döner. Taslak, kullanıcının "aktif çalışma alanı"dır; Hazırlığa
geçişte gerçek bir sunuma (presentation) **terfi** ettirilir.

---

## 1. Taslak modeli — her kullanıcının bir aktif draft'ı

Keşif'in state'i bir **draft manifest**'tir. `DraftManager`
(`presentations/drafts/manager.py`) yönetir:

- `get_or_create_current(sicil)` — kullanıcının aktif taslağını döndürür,
  yoksa yaratır (idempotent). Taslak pid'i `draft_<sicil>_<ts>` formatında.
- Draft manifest S3'te: `presentations/{sicil}/{draft_pid}/manifest.json`.
  İçinde: `basket=[{table, …}]`, `kesif_chat_history=[{role, text, ts}]`
  (en fazla **30** mesaj), `meta.title`, `is_draft=True`, `version`.
- Draft tercihleri (`_drafts.json`): `current`, `drafts[]`, `promoted[]`.
- **Çöp toplama:** 7 günden eski + **boş sepetli** taslaklar silinir;
  içinde tablo olan taslaklar süresiz korunur.

> Taslak, `SessionRegistry` üzerinden okunur/yazılır; okuma/yazma best-effort
> (try/except) — katalog ya da manifest yüklenmezse Keşif boş sepetle devam
> eder, çökmeR.

---

## 2. Sayfa yükleme — `GET /atolye/kesif`

`routes_kesif.py::atolye_kesif` (149) → `_build_workbench_payload(sicil,
'tablolar')` (87):

1. `DraftManager` ile aktif taslağı al/yarat.
2. Manifest'ten `basket` + `meta.title` + `kesif_chat_history` (son 30) oku.
3. `kesif_json` payload'unu üret: `draft.pid`, `basket`, `basket_count`,
   `chat.history`, endpoint'ler (`chat_send`, `draft_promote`, `draft_title`).
4. `kesif.html`'i render et (Cosmograph graph + sol tablo listesi + sağ chat).

---

## 3. Chat turu — `POST /atolye/kesif/chat`

Akışın kalbi (`routes_kesif.py:637`):

1. Mesajı doğrula (boş değil, ≤2000 karakter).
2. Taslak pid + **katalog** (paylaşımlı `CatalogLoader`, TTL cache) +
   `basket` + `kesif_chat_history` yükle.
3. User turn'ünü geçmişe ekle.
4. **`propose_tables(...)`** çağır — `chat_history[:-1]` ile (yeni user
   mesajını LLM'e iki kez göstermemek için; bkz. Dikkat).
5. `DiscoveryError` → **HTTP 200** + `assistant_turn.status="error"`
   (chat UI çökmesin diye 4xx değil).
6. Başarıda assistant turn'ünü (proposals + highlights + dropped) geçmişe
   ekle, **30 mesajla cap'le**, manifest'i persist et (version bump).
7. Cevap: `{user_message, assistant_message{text, proposals, highlights,
   dropped}, history}`.

### propose_tables — `discovery/client.py`

İki yol:
- **FakeLLM** (DEV, `llm.py:826`): native `propose_tables`. Mesajı
  tokenize eder, **TR→EN köprüsü** uygular (`mevduat→deposit`, `kredi→loan`,
  `şube→branch`…), her katalog girdisini skorlar: **isim eşleşmesi ×1.5**,
  concept ×1.0, açıklama ×0.5, **aynı departman +0.5**; sepettekileri atlar;
  skora göre sırala, **top-5**.
- **Qwen / gerçek LLM**: `build_system_prompt()` (`discover.txt`) +
  `build_catalog_summary()` (token-bütçeli) + `build_user_message()`. JSON
  döner; parse hatası olursa **1 kez** "sadece JSON dön" ile tekrar denenir.

### Katalog özeti — `discovery/prompt.py`

`build_catalog_summary(entries, dept, token_budget=8000)`: girdileri
`(dept_affinity, binding_count desc)` ile sıralar (kullanıcının departmanı +
çok-concept'li tablolar önce), her birini `- **schema.table** [dept] — desc ·
concepts: …` satırı yapar, ~4 char/token ile bütçeye kadar biriktirir, taşarsa
keser + not düşer. Önce gelenler kesilmeden listede kalır.

### Hallüsinasyon koruması — `_shape_result`

LLM "olmayan" bir tablo önerebilir. `_shape_result(parsed, valid_ids)` her
öneriyi katalogun gerçek `table_id` set'ine karşı kontrol eder: sette varsa
**kabul**, yoksa **drop** (`dropped_proposals: [{schema, name, reason}]`).
Drop'lar kullanıcıya **görünür** (UI dropped paneli) — şeffaflık.

Çıktı: `DiscoveryProposal{schema, name, rationale, match_score,
suggested_companion?}` + `DiscoveryResult{explanation, proposals[],
highlight_graph_node_ids[], dropped_proposals[]}`.

---

## 4. Sepet — `POST /<pid>/basket`

`routes.py:193`: gövdedeki `basket` listesini doğrular, `manifest['basket']`'i
**tümüyle değiştirir** (diff yok), version bump, yaz. Frontend basket state'ini
kendinde tutar; her değişiklikte tam listeyi gönderir. Chat önerileri React
tarafında sepete kopyalanır; bu endpoint otoriter yazımdır.

---

## 5. Hazırlığa terfi — `POST /atolye/kesif/draft/promote`

`routes_kesif.py:535`:

1. `draft_pid`'in draft-prefixli olduğunu doğrula. **Sepet boşsa reddet**
   ("Sepete en az 1 tablo ekleyin").
2. Promote ÖNCESİ sepet `table_id`'lerini yakala (manifest silinecek).
3. `DraftManager.promote(sicil, draft_pid, title=…)` (manager.py:202):
   yeni gerçek pid (`p_<token>`) bas; draft manifest'i kopyala (id=yeni pid,
   version=1, owner_id=sicil, `is_draft` kaldır, title taşı); yeni pid'e yaz;
   tercihleri güncelle; **draft S3 manifest'ini sil** (best-effort).
4. Sepet id'lerini `?seed=ID1,ID2,…` query string'ine kodla.
5. Cevap: `{presentation_id, hazirlik_url: /presentations/{pid}/hazirlik?seed=…}`.

### Seed ile Hazırlığa giriş

`GET /presentations/{pid}/hazirlik?seed=…` → `_seed_basket_from_query`
(`routes_scope.py:713`): her id'yi katalogda bul, bir `BasketItem` yarat
(schema, name, tablo adından türetilmiş alias, projection=tüm kolonlar),
**`table_ref` ile dedup** (alias ile değil), `manifest.draft_scope`'a persist
et. Bu, Keşif ile Hazırlık arasındaki köprüdür.

---

## State özeti

| Ne | Nerede | Not |
|---|---|---|
| Draft manifest | S3 `presentations/{sicil}/{draft_pid}/manifest.json` | basket, kesif_chat_history (cap 30), meta.title, is_draft |
| Draft prefs | S3 `presentations/{sicil}/_drafts.json` | current, drafts[], promoted[] |
| Katalog | CatalogLoader (TTL cache, paylaşımlı) | her chat turu taze okur |
| Sohbet geçmişi | manifest'te (30 cap); LLM yalnız **son 10 turu** görür | token bütçesi |

---

## Dikkat (gotcha'lar)

- **`history[:-1]`** — yeni user mesajı LLM'e iki kez gitmesin diye geçmişten
  çıkarılır (mesaj zaten `build_user_message`'ın "yeni talep" kısmında).
- **DiscoveryError → 200**, 4xx değil — chat UI akışı bozulmasın.
- **Katalog yüklenmezse sessiz** — FakeLLM "eşleşme yok" der; gerçek Qwen
  halüsinasyon yapabilir (UI "katalog yok" göstermeli).
- **Boş sepet promote'u engeller.**
- **Seed idempotent** (`table_ref` ile) — aynı URL tekrar yüklenince
  duplike BasketItem olmaz.
- **Promote sonrası draft silinir** (best-effort); silinmezse 7 gün sonra GC.
- **Manifest version drift** — chat-persist ve basket-update ayrı version
  bump eder; SessionRegistry lock tutmaz (kullanıcı bir draft'ta tek-iş
  yaptığından pratikte sorun değil).
- **Concept skorlaması Keşif'te yok** (FakeLLM yalnız isim/açıklama/dept
  skorlar; concept prominence build_catalog_summary sıralamasında etkili).

---

## Akıştaki yeri

```
[KEŞİF] ── promote + ?seed ──▶ [HAZIRLIK] ──▶ [SUNUM]
   chat → basket → terfi
```

> Sıradaki: [`BACKEND_HAZIRLIK.md`](BACKEND_HAZIRLIK.md) — sepet → scope
> contract → dataset'ler → build.
