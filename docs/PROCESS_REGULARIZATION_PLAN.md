# Süreç Düzenlileştirme (Process Regularization) — Geliştirme Planı

**Status:** draft v1
**Owner:** Kubilay Karatopçu
**Repo hedefi:** `treasury-report` (bu doküman + `presentations/` + `prisma_home/`)
**İlişkili:** `treasury-etl/docs/ETL_PRISMA_HANDSHAKE_PLAN.md` (veri devri),
`docs/DASHBOARD_ADAPTATION_PLAN.md` (mevduat panosu port kararları),
`docs/PHASE_10_SPEC.md` (blok/marketplace)

---

## §0 — Bağlam ve doğrulanacak varsayımlar

Elle inşa edilmiş "custom" süreçler (bugün: `mevduat_panel` SPA'sı) standart
**Keşif → Hazırlık → Sunum** hattından geçmeden var oldu. Bu plan onları,
*sanki Sunum aşamasında üretilmiş gibi* modele birinci-sınıf sokmayı hedefler:
bloklara ayır, blokları dökümante et, süreç olarak kaydet — pano yeniden
yazılmadan.

| # | Varsayım | Kaynak | Doğrulandı? |
|---|----------|--------|-------------|
| V1 | `mevduat_panel` bilinçli olarak Keşif/Hazırlık/Sunum akışının DIŞINDA; manifest/session/blok ÜRETMEZ, kendi SPA'sını taşır | `DASHBOARD_ADAPTATION_PLAN.md §1`, K1 | ☑ |
| V2 | Panonun interaktif grafikleri (bubble split/merge, hover-linked NP heatmap, maturity ladder, drill modalları) standart blok render'ıyla birebir ifade EDİLEMEZ | K3/K4 | ☑ |
| V3 | Blok altyapısı olgun: `Block` (`kind: single\|composite`), `BlockDocumentation`, versiyonlu block store, kütüphane insert akışı | `presentations/blocks/schema.py`, `store.py` | ☑ |
| V4 | Süreçlerin (processes) KENDİ store'u YOK — yalnız kodda `PROCESS_REGISTRY` (dict) + uzman YAML'da string id | `prisma_home/processes.py:22`, `experts.py` | ☑ |
| V5 | LLM ile blok/pano/tablo dökümantasyonu bugün YOK; ama yapıtaşları hazır (`llm.py`, prompt-dosyada-yaşar, "öner→insan onaylar" deseni `concepts/inference/llm_proposer.py`) | araştırma | ☑ |
| V6 | Panonun VERİ katmanı ileride ETL mart'larına devredilecek (CEC + lake fetch) — bu planın parçası DEĞİL, ETL olgunlaştıkça ayrı iş | `ETL_PRISMA_HANDSHAKE_PLAN §2.1/E3` | ☑ (ertelendi) |
| V7 | Uzman `bound_content` üç ayrı liste taşır: `blocks` / `snapshots` / `processes` | `experts.py` | ☑ |

---

## §1 — Tasarım ilkeleri

### 1.1 ETL, "blockify reddini" ikiye böler

Panoyu bloğa çevirme eskiden üç sebeple reddedildi: **(K2)** kaynak SQL + satır
drill + pay/payda agregasyonu = *veri katmanı*; **(K3/K4)** SPA etkileşimleri =
*sunum katmanı*. treasury-etl **K2'yi devralır** (veri mart'lara taşınınca
"hazırlama" derdi ETL'e geçer). Geriye **sunum** kalır; orada K3/K4 hâlâ
gerçektir. Sonuç: **tam blockify yanlış, hibrit doğru.**

### 1.2 Yeni blok türü: `kind: "custom"` — yeniden yazmadan kaydet

Standart SQL+viz render'ı OLMAYAN, ama dökümantasyon + veri-kaynağı + render
hedefi taşıyan blok. İnteraktif pano bileşenlerini (K3/K4) yeniden yazmadan
"blok" olarak kaydetmeyi ve dökümante etmeyi sağlar. Standart renderer'a
dokunmaz; render hedefi panonun kendi SPA sayfası/section'ıdır.

### 1.3 Kademeli terfi (graduation), kalıcı hack değil

`mevduat_panel` chart'ları P3/P4'te Apex'e/standart renderer'a geçtikçe, o chart
`kind:"custom"` → `kind:"single"` (mart üstünde SQL + viz) olarak **terfi eder**.
`custom` blok bir **geçiş rampasıdır**: bugün kaydet/dökümante et, yarın standarda
çevir. Descriptor ve dökümantasyon terfide korunur (blok id sabit, version artar).

### 1.4 Tek gerçeklik: Süreç

Kullanıcı kararı (2026-07-22): panolar birer süreçtir; uzman altındaki
"Kaynakça/Snapshot" ayrımı kaldırıldı. Bu plan süreci birinci-sınıf artifact
yapar — hem pipeline'dan üretilen hem custom süreç aynı **Process Descriptor**'a
uyar, kütüphanede ve uzman altında aynı gerçeklik görünür.

### 1.5 Purely additive

PRISMA faz felsefesi: hiçbir mevcut artifact kırılmaz. `PROCESS_REGISTRY`'siz
süreç çözümü, descriptor'suz uzman, dökümantasyonsuz blok çalışmaya devam eder.
Migration opt-in.

---

## §2 — Kontratlar

### 2.1 Process Descriptor

Hem pipeline'dan üretilen hem custom süreçlerin uyduğu ortak sözleşme. Bugünkü
`PROCESS_REGISTRY` girdisinin (`prisma_home/processes.py:22`) supersetidir —
mevcut alanlar (`label`, `desc`, `endpoint`, `page`, `config_flag`) korunur.

```json
{
  "id": "mevduat.maliyet",
  "title": "Outstanding Cost Analysis",
  "summary": "Monthly averages & daily evolution · bubble · rate heatmap",
  "owner": "A16438",
  "source_kind": "custom",            // "pipeline" | "custom"
  "expert": "dep",                    // bağlı olduğu uzman (bound_content.processes)
  "render": {                          // custom süreçte SPA hedefi; pipeline'da null
    "endpoint": "mevduat_panel.index",
    "page": "cost-analysis",
    "config_flag": "MEVDUAT_PANEL_ENABLED"
  },
  "blocks": ["blk_camon_wf", "blk_camon_bubble", "blk_camon_ratehm", ...],
  "documentation": {                   // süreç düzeyi; BlockDocumentation ile aynı dil
    "purpose": "...",
    "business_context": "...",
    "decision_support": "...",
    "known_limitations": "..."
  },
  "data_sources": [],                  // V6: ETL devrinde CEC FQN'leriyle dolacak
  "concept_bindings": {},              // opsiyonel
  "snapshot_ref": null,                // "Sunum'da üretilmiş gibi" — share yolu
  "descriptor_version": "proc-1"
}
```

Kurallar:
- **Depolama:** block store deseninin (`presentations/blocks/store.py`) kardeşi
  bir `process store` (versiyonlu YAML; Local + S3). `PROCESS_REGISTRY`
  descriptor store'a **taşınır**; `resolve_processes` (`processes.py:68`)
  store'dan okur, imza korunur (uzman sayfası aynen render eder).
- **Backward-compat:** store boşsa/erişilemezse mevcut `PROCESS_REGISTRY` dict'i
  fallback kalır. Uzman YAML'ında yine yalnız string id durur (`bound_content.
  processes`); id → descriptor eşlemesi store'da.
- `source_kind: "custom"` → `render` zorunlu (SPA hedefi). `"pipeline"` →
  `render: null`, blocks standart manifest'ten gelir.
- İzolasyon korunur: `render.endpoint` string çözülür; `mevduat_panel` import
  edilmez (bugünkü `processes.py:6-9` sözleşmesi).

### 2.2 `Block.kind: "custom"` şema genişletmesi

`presentations/blocks/schema.py:377` — bugün `Literal["single", "composite"]`.
Yeni değer eklenir: `Literal["single", "composite", "custom"]`.

`kind == "custom"` bloğu:
```json
{
  "team": "dep", "id": "blk_camon_bubble", "version": 1,
  "title": "Cost Bubble — Balance × Rate",
  "kind": "custom",
  "custom_render": {                   // standart 'query' + 'visualization' YOK
    "endpoint": "mevduat_panel.index",
    "page": "cost-analysis",
    "anchor": "ca-mon-bub-bal"         // pano içi bileşen kimliği (opsiyonel)
  },
  "data_sources": [],                  // V6: mart FQN'leri (ETL devrinde)
  "documentation": { ... },            // BlockDocumentation (§2.3)
  "tags": ["bubble", "cost", "custom"],
  "deprecated": false
}
```

Şema doğrulama kuralları (Pydantic validator):
- `custom` blok `query` / `variables` / `visualization` **taşımaz** (single'ın
  alanları); yerine `custom_render` **zorunlu**.
- `custom` blok `composite` gibi `children` **taşımaz**.
- SQL whitelist / bind resolver / block cache yollarına `custom` blok GİRMEZ
  (SQL'i yok) — bunlar `custom` kind'ı erken `return` ile atlar. Sadece
  dökümantasyon + kütüphane listelemesi + insert-as-reference yollarına girer.

### 2.3 Dökümantasyon kontratı

Mevcut `BlockDocumentation` (`schema.py:204`: `purpose`, `business_context`,
`decision_support`, `known_limitations`) **aynen** kullanılır — hem custom blok
hem süreç düzeyinde. Yeni alan gerekmez. Zorunluluk:
- `custom` blok kütüphanede "yayınlanmış" sayılmak için `documentation.purpose`
  + en az bir alan daha dolu olmalı (boş custom blok "taslak" rozetiyle görünür).
- Süreç descriptor'ının `documentation`'ı, bloklarının dökümantasyonunun
  özeti/çerçevesidir (elle ya da §2.4 taslağıyla).

### 2.4 LLM Dökümantasyon Taslak Üreticisi (doc proposer)

`concepts/inference/llm_proposer.py`'ın kalıbını taklit eden yeni bir üretici.
**Öner → insan onaylar; asla auto-publish etme.**

- **Girdi:** blok bağlamı — varsa mart CEC'i (`data_sources`), blok başlığı +
  tag'leri, custom blok ise render hedefi (hangi pano/sayfa/section), ilgili
  kaynak SQL (varsa) ve pano dokümanları (`catalog/` tablo dokümanları).
- **Çıktı:** `BlockDocumentation` **taslağı** (4 alan) — tolerant JSON çıkarma
  (`llm_proposer.py:54-81` / `briefing.py:216-238` deseni).
- **Prompt:** `presentations/prompts/doc_proposal.txt` (asla inline).
- **Kapı:** taslak `documentation` alanına DEĞİL, bir `documentation_proposed`
  gölge alanına yazılır; kütüphane editöründe kullanıcı görüp onaylayınca
  `documentation`'a promote olur. (concept binding'lerdeki `human_verified`
  gating deseninin dökümantasyon karşılığı.)
- **Kapsam:** Keşif/Hazırlık/Sunum'dan geçmeyen custom bloklar da bu yolla
  dökümante edilebilir — kullanıcının asıl istediği bu.

---

## §3 — Faz planı

Her faz bağımsız ship'lenir (PRISMA faz disiplini). Süreler tek kişi + Claude
Code temposuna kaba tahmin.

### D0 — Kontrat spec'leri + fixtures  *(~2–3 gün)*

- §2'deki üç kontrat kilitlenir: Process Descriptor, `kind:"custom"` şema,
  doc-proposer sözleşmesi.
- Fixture'lar `examples/process_regularization/` altına: örnek custom process
  descriptor (mevduat.maliyet), 2–3 örnek `custom` blok, dolu + boş
  `BlockDocumentation` örnekleri, örnek doc-proposal LLM çıktısı.
- **Acceptance:** iki tarafın da (schema + store) aynı fixture setine karşı test
  yazabildiği durum.

### D1 — Process store + descriptor migration  *(~1 hafta)*

- `prisma_home/`'a (ya da `presentations/process/`) versiyonlu process store
  (block store deseni). `PROCESS_REGISTRY` → descriptor YAML'larına taşınır.
- `resolve_processes` store'dan okur; `PROCESS_REGISTRY` dict fallback. Uzman
  sayfası + `?page=` deep-link aynen çalışır (regresyon testi).
- Mevduat panosu 7 süreç descriptor'ı kazanır (`source_kind: "custom"`, `render`
  dolu). Henüz blocks boş olabilir (D2'de dolar).
- **Acceptance:** uzman sayfası descriptor store'dan render oluyor; eski davranış
  birebir; dict'i silince fallback devrede.

### D2 — `custom` blok türü + kütüphane + dökümantasyon (elle + LLM taslak)  *(~2 hafta)*

- `schema.py`'a `kind:"custom"` + `custom_render` + validator'lar. SQL/cache
  yolları `custom`'ı atlar.
- Mevduat panosunun sayfa/bileşenleri `custom` bloklara ayrılır (önce sayfa
  başına 1 blok; sonra P3/P4 Apex chart'ları ayrı bloklara bölünebilir).
  Descriptor `blocks` listesi dolar.
- Kütüphane (`routes_blocks.py` / `atolye_bloklar`): `custom` bloklar listelenir,
  aranır, önizlenir; "insert" pipeline sunumuna **referans/embed** olarak girer
  (SQL bloğu gibi çalıştırılmaz).
- Blok editörüne `BlockDocumentation` alanları + **"LLM ile taslak üret"**
  butonu (§2.4 doc-proposer). Taslak `documentation_proposed` → kullanıcı onayı.
- **Acceptance:** mevduat panosunun blokları kütüphanede pipeline bloklarıyla
  yan yana görünüyor; her biri dökümanlı (elle ya da onaylı LLM taslağı);
  bir custom blok yeni bir sunuma referans olarak eklenebiliyor.

### D3 — Veri devri (ETL mart'larına)  *(ERTELENDİ — ETL olgunlaşınca)*

> V6: bu faz treasury-etl E1/E3 (mart üretimi + lake fetch) tamamlanınca
> devreye alınır. Bu planın parçası olarak ŞİMDİ yapılmaz.

- Custom bloğun/sürecin `data_sources`'ı boş → mart FQN'leriyle dolar.
- Panonun Oracle sorguları CEC/lake fetch'e taşınır (ETL planı E3).
- `documentation.lineage → promoted_from` (recipe/mart soyağacı).
- Apex'e geçmiş + mart'a bağlanmış chart'lar `custom` → `single` terfi eder
  (§1.3): kütüphaneden gerçek SQL bloğu olarak insert edilebilir hale gelir.

### D4 — Sunum eşdeğerliği + snapshot/share  *(~1 hafta)*

- Custom process bir snapshot/share yolu kazanır (`snapshot_ref`): "sanki
  Sunum'da üretilmiş gibi" — read-only paylaşılabilir görünüm.
- Kütüphane > Süreçler ile uzman > Süreçler tek descriptor store'dan beslenir
  (araştırmadaki "süreç kelimesi 3 şeye işaret ediyor" karmaşası giderilir:
  tek kavram = Process Descriptor).
- **Acceptance:** bir custom süreç, pipeline süreçleriyle aynı kütüphane
  yüzeyinde; snapshot linkiyle paylaşılabiliyor; uzman altında ve kütüphanede
  aynı gerçeklik.

---

## §3.5 — W serisi: Dökümantasyon Yazımı + Uzman Konuşması (2026-07-22 kararları)

Kullanıcı kararları: **(1)** Snapshot kavramı VE mekanizması tamamen sökülür —
paylaşım linki yerine tek-sayfa HTML indirme; frozen paylaşım ihtiyacı = süreç
olarak paylaş (verisi ETL martlarıyla zaten deterministik olacak). **(2)** D2'nin
"custom bloklar Bloklar kütüphanesinde görünsün" maddesi W1'e alındı. **(3)**
Sıra: W1 → W3 → W2 → W4.

### W1 — Process store + dökümantasyon YAZIMI + Bloklar görünürlüğü *(~1 hafta)*
- Versiyonlu process store (block store deseni: Local + S3, atomic bump).
  Registry seed fallback: store'da kayıt yoksa `PROCESS_REGISTRY` okunur; ilk
  kayıt (edit) store'a v1 yazar. Okuma yolu `current_app.config["PROCESS_STORE"]`.
- `surec_detay` ekranına düzenleme formu: süreç + blok dökümantasyonunun 4 alanı
  server-side form ile yazılır; kayıt = yeni versiyon. Saf Jinja.
- Süreçlerin `kind:"custom"` bileşen blokları **Kütüphane > Bloklar** listesinde
  görünür (listing-merge: BLOCK_STORE'a kopyalanmaz — drift yok; liste + API
  process kayıtlarından ek satır üretir, kart tıklaması süreç detayına gider).
- Acceptance: kullanıcı dökümantasyonu ekrandan yazıp kaydediyor (versiyonlu);
  custom bloklar Bloklar'da "custom" rozetiyle listeleniyor.

### W2 — Snapshot'ın TAMAMEN sökülmesi *(~1–1.5 hafta)*
- "Snapshot Al" → "Süreç olarak yayınla": sunum yayını bir pipeline Process
  Descriptor üretir; kütüphane Süreçler'de `pipeline` rozetiyle görünür.
- Paylaşım linki yerine **tek-sayfa HTML dışa aktarma** (self-contained,
  indirilebilir). `create/view/delete_snapshot` route'ları, Snapshot'lar
  sayfası, `SNAPSHOT_STORE`, uzman `bound_content.snapshots` ve brifing
  motorunun snapshot bağı sökülür (bound_content.processes'e migration).
- Acceptance: kod tabanında kullanıcıya görünen "snapshot" kavramı kalmaz;
  dışa aktarma HTML'i tek dosya olarak açılıyor.

### W3 — LLM doc-proposer *(~1 hafta)*
- `prompts/doc_proposal.txt` + `POST /atolye/surec/<pid>/propose-doc` (süreç ve
  blok bazlı). Bağlam: descriptor + blok bilgisi + tablo dokümanları (ileride
  mart CEC). Çıktı `documentation_proposed` gölge alanına; W1 formu "taslağı
  göster → alan alan kabul et" akışı kazanır. Asla auto-publish yok. DEV stub.

### W4 — Uzmanı konuşturma *(~1.5–2 hafta)*
- **W4a — Uzman Yorumu:** uzman personası bağlı süreçlerin dökümantasyonundan
  kısa yorum üretir (brifing motorunun süreç-tabanlı yeniden doğuşu). Uzman
  sayfasında süreç kartlarının üstünde 2-3 cümlelik yorum.
- **W4b — Veri + "…'ye sor":** süreç başına opsiyonel metrik sağlayıcı kontratı
  `{k, v, delta, tone}` (expert kartı rail şekli); custom süreç için
  mevduat_panel engine cache'lerinden KPI özeti, pipeline için manifest KPI'ları.
  Masa'daki gizli "…'ye sor" alanı geri açılır — `/<pid>/chat` SSE deseninin
  uzman muadili, `QwenClient.complete` üzerinden.

## §4 — Riskler ve açık sorular

| Risk / soru | Etki | Öneri |
|---|---|---|
| `custom` blok bir "kaçış kapısı" olup her şeyi custom yapma cazibesi | Kütüphane standart-dışı bloklarla dolar, terfi olmaz | §1.3 graduation zorunlu kültür; `custom` blok "geçici" rozetiyle görünür; P3/P4 ilerledikçe terfi hedefi takip edilir |
| Custom blok "insert" edilince pipeline sunumunda nasıl render olur? | Manifest render motoru custom bileşeni bilmez | v1'de "referans/embed" (link/iframe-benzeri kart), çalıştırılabilir blok değil; gerçek gömme D4+ backlog |
| Process store × `PROCESS_REGISTRY` çift kaynak | Drift | Migration'da dict tek seferde store'a taşınır, dict yalnız fallback; CI'da "dict boş olmalı (prod)" kontrolü |
| LLM doc taslağının kalitesi (halüsinasyon) | Yanlış iş bağlamı | Taslak asla auto-publish değil; `documentation_proposed` + insan onayı; prompt'a "emin değilsen boş bırak" |
| İzolasyon: process store `prisma_home`'da mı `presentations`'da mı? | Modül bağımlılığı | `custom_render.endpoint` string çözümü korunur; descriptor'ı `presentations/process/` altına koymak block store'a yakınlık sağlar, `prisma_home` yalnız string id tüketir |
| "custom" bloğun versiyonlanması (pano değişince) | Bayat dökümantasyon | Blok immutability (Phase 6.5 kararı) aynen: pano/section değişince yeni version; descriptor `blocks` id+version üçlüsü tutar |
| Snapshot/share bir SPA panosu için ne demek? | Donmuş veri yok (SPA canlı) | D4'te "share = descriptor + o anki filtre state + rozet"; gerçek donmuş veri ETL snapshot'ına (D3/ETL) bağlı |

---

## §5 — Sözlük

- **Custom process** — Keşif/Hazırlık/Sunum hattından geçmeden elle inşa edilmiş
  süreç (bugün: `mevduat_panel` sayfaları).
- **Process Descriptor** — pipeline ve custom süreçlerin uyduğu ortak sözleşme;
  `PROCESS_REGISTRY`'nin superseti, versiyonlu store'da yaşar.
- **`kind:"custom"` blok** — standart SQL+viz'i olmayan, render hedefi bir SPA
  bileşeni olan, dökümantasyon + veri-kaynağı taşıyan blok.
- **Graduation (terfi)** — bir `custom` bloğun, chart'ı Apex'e/standarda geçip
  mart'a bağlanınca `single` bloğa dönüşmesi.
- **Doc proposer** — mart CEC + blok bağlamından `BlockDocumentation` taslağı
  üreten, insan-onaylı LLM üreticisi.
- **Veri devri (D3)** — panonun Oracle sorgularının ETL mart'larına (CEC/lake
  fetch) taşınması; ETL olgunlaşınca yapılır.
