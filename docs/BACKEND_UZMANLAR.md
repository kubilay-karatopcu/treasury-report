# Treasury Studio — Uzmanlar Katmanı (Anlatımlı Doküman)

> Bu doküman **uzman (expert)** entity'sini anlatır: uzman nedir (ve ne
> DEĞİLdir), kodda nasıl saklanır, sunumlara/snapshot'lara nasıl bağlanır,
> ve çalışma zamanında **gerçekte nerede** kullanılır. Kardeş doküman­lar:
> [`BACKEND_TABLOLAR.md`](BACKEND_TABLOLAR.md), [`BACKEND_BLOKLAR.md`](BACKEND_BLOKLAR.md).

---

## Önsöz — Uzman nedir, ne değildir?

Bir **uzman**, tüketici tarafında bir **LLM personasıdır**: bir alanın
(Likidite, Mevduat, Fonlama, NII, Security, Kredi) sesi ve günlük brifing
tarifidir. Altı tane sabit uzman var (`liq, dep, fnd, nii, sec, krd`).
Bir uzman üç şey yapar:

1. Kendine **bağlı snapshot'ları** brifinglerin altında **kaynakça/atıf**
   olarak yüzeye çıkarır.
2. Bir **ses** taşır (`persona.system_prompt` + `voice_examples`) — brifing
   motoru bu sesle metni yeniden ifade eder.
3. Bir **tarif** sahibidir (`briefing_recipe`) — günlük brifingin nasıl
   kurulacağını anlatır.

> **Uzman NE DEĞİLDİR — kritik:** Bir uzman, **sunum (Sunum) sohbetini**
> yöneten bir ajan/persona **değildir.** Aşağıda detaylandırıldığı gibi
> (§5–§6), `bound_experts` alanı manifest'te bulunur, doğrulanır ve saklanır
> — **ama Sunum chat/`generate_patch` akışında HİÇBİR YERDE OKUNMAZ.** Uzman
> personası **yalnızca Phase 10E brifing motorunda** (snapshot atıflarını
> yeniden ifade ederken) kullanılır. Sunum LLM'i uzmanı bilmez. Bu, "yarı-
> bağlı" (half-wired) bir durumdur; Phase 11+ bunu sunuma taşımayı planlıyorsa
> henüz yapılmamıştır.

Uzmanlar **Phase 10**: 10B backend, 10C statik brifing, 10D öneri, 10E LLM
brifing. Tümü git-versiyonlu YAML; veritabanı yok.

---

## 1. Uzman şeması — `prisma_home/experts.py`

Uzman bir `Expert` dataclass'ı (experts.py:32):

| Alan | Tip | Anlam |
|---|---|---|
| `id` | str | Benzersiz kimlik (yüklenince lowercase) |
| `version` | int | Immutability işareti (spec §5.1) |
| `code` | str | 3-harf enum (`LIQ, DEP, FND, NII, SEC, KRD`) — sıralama için |
| `name` | str | Kullanıcıya görünen ad ("Likidite Uzmanı") |
| `domain_label` | str | UI kategorisi |
| `short_description` | str | Giriş metni |
| `persona` | dict | `{system_prompt, voice_examples: [str]}` — LLM sesi |
| `bound_content` | dict | `{blocks: [], snapshots: [], processes: []}` |
| `briefing_recipe` | dict | `{cache_ttl_seconds, sections: [...]}` — kurulum tarifi |
| `access_scope` | dict | `{read: [...] \| '*', edit: [departman...]}` |
| `ui` | dict | `{accent_color, glyph}` — kart stili |
| `status` | str | Yaşam döngüsü (default `active`) |

### `persona` — uzmanın sesi

`system_prompt` (Türkçe ses talimatı) + `voice_examples` (2–3 örnek). Brifing
motoru bunu LLM'e **system bloğu** olarak enjekte eder; uzman böyle "konuşur".
**`persona.system_prompt` istemciye yollanmaz** — yalnız `/api/experts/<id>`
(erişim kontrolünden sonra) döner; server-side tüketilir.

### `briefing_recipe` — brifing nasıl kurulur

`sections: [{id, title, fill_from, llm_paraphrase}]`. `fill_from` snapshot
havuzunu `kind/role/semantic_tag/limit` ile filtreler; `llm_paraphrase: true`
ise persona ile yeniden ifade edilir. (Phase 10E'de `fill_from.kind` yalnız
`snapshot`; `block`/`metric` Phase 11+ için ayrılmış.)

### `bound_content` — uzmana bağlı içerik

`{blocks, snapshots, processes}`. **Şu an yalnız `snapshots` kullanılır**
(blocks/processes Phase 11/12 için tanımlı ama tüketilmiyor). `snapshots`,
snapshot-tarafı `bound_experts` ile çift-yönlü senkron tutulan bir UI
aynasıdır (bkz. §5).

### `access_scope` — görünürlük

`read`: `'*'` (herkes) ya da departman listesi. `edit`: hangi departman bu
uzmanı düzenleyebilir (producer form erişimi). Read default'u `'*'`; read
scope'u eksikse erişim kontrolünde `[]` (kimse göremez) varsayılır.

---

## 2. Storage — `LocalExpertStore` (git YAML)

Her uzman bir `*.yaml` dosyası; `examples/phase_10/experts/<id>.yaml`.
`LocalExpertStore(base_dir)` lazy yükler ve **pod ömrü boyunca cache'ler**
(hot-reload yok; YAML değişince pod restart ya da cache invalidation gerek).
Bozuk/eksik dosyaları sessizce atlar.

`ExpertStore` protokolü (salt-okunur): `list_all()`, `load(expert_id)`,
`list_for_user(user)` (`access_scope.read` ile filtre), `exists(expert_id)`.
İleride `S3ExpertStore` (prod paritesi).

Wiring (`app.py:558`):
```python
_EXPERTS_DIR = Path(__file__).parent / "examples" / "phase_10" / "experts"
app.config["EXPERT_STORE"] = LocalExpertStore(base_dir=_EXPERTS_DIR)
```

> **Dikkat:** `store.load(code.lower())` ile id case-insensitive yüklenir, ama
> YAML dosya adı `id` alanıyla **birebir** olmalı (`liq.yaml`, `LIQ.yaml`
> değil).

---

## 3. Snapshot bağlama — `bound_experts` (snapshot-merkezli)

Bir uzmanı içeriğe bağlamanın yolu `bound_experts: list[str]` (uzman id'leri).
**Kritik: bu alan SNAPSHOT seviyesindedir** (`snapshot.meta.bound_experts`),
dashboard/sunum seviyesinde değil. Bir sunum birden çok snapshot gömebilir;
her snapshot birden çok uzmana bağlanabilir.

- **Manifest doğrulama** (`presentations/manifest.py:404`): `bound_experts`
  list[str] olmalı; `EXPERT_STORE` erişilebilirse her id `store.exists()` ile
  varlık-kontrolünden geçer (bilinmeyen id → hata).
- **Migration** (`presentations/migration.py`): `ensure_bound_experts()`
  yoksa `[]` ekler (idempotent) — her manifest yüklemede alan mevcut olur.
- **Snapshot deposu** (`presentations/store.py`): `set_bound_experts(
  snapshot_id, list)` hem `meta.json`'a hem dondurulmuş `manifest.json`'a
  yazar (iki kopya senkron).
- **Çift-yönlü senkron** (`routes_library.py::_sync_expert_to_snapshot_links`):
  producer uzman formunda `bound_content.snapshots`'a snapshot ekler/çıkarırsa,
  ilgili snapshot'ların `bound_experts`'i otomatik güncellenir. **Doğruluk
  kaynağı = `snapshot.bound_experts`**; `expert.bound_content.snapshots` UI
  aynasıdır. (Senkron tek yön: form→snapshot; snapshot'ı API'den doğrudan
  değiştirirsen form reload'a kadar yansımaz.)

`find_snapshots_bound_to(snapshot_store, expert_id)` ters-bağlantı: tüm
snapshot meta'larını tarar, `expert_id ∈ bound_experts` olanları döndürür
(uzman detay sayfasındaki kaynakça şeridi için).

---

## 4. Çalışma zamanı kullanımı — Brifing motoru (tek gerçek tüketici)

`prisma_home/briefing.py::BriefingEngine` (Phase 10E):

1. `_bound_snapshots(expert_id)` → o uzmana bağlı snapshot havuzu.
2. `briefing_recipe.sections` her bölümü havuzu `role/semantic_tag/limit` ile
   filtreler.
3. `llm_paraphrase: true` bölümlerde `_llm_paraphrase(expert, section, items)`
   → **`expert.persona` (system_prompt + voice_examples) LLM system bloğuna
   enjekte edilir.** Uzman burada **ses personası** olarak davranır — veri/blok
   seçimini filtrelemez, sadece anlatımı şekillendirir.
4. LLM erişilemezse `StaticBriefing`'e düşer (`examples/phase_10/briefings/
   <id>_static.md`) — bozulmadan zarif degrade.

Önbellek: `cache_ttl_seconds`; uzman kaydedilince `engine.invalidate(
expert_id)`.

> **Tekrar — Sunum chat'i `bound_experts`'i OKUMAZ.** `presentations/graph.py`
> `GraphState`'i uzman taşımaz; `generate_patch` jenerik `prompts/edit.txt`
> system prompt'unu kullanır; persona enjeksiyonu yoktur. Yani uzman bağlamak
> **sunumun nasıl düzenlendiğini/üretildiğini değiştirmez** — yalnız o sunumun
> snapshot'ları, ilgili uzmanın brifing sayfasında kaynakça olarak görünür.

---

## 5. HTTP endpoint'leri

**Tüketici (consumer) — `prisma_home/routes.py`:**

| Method | Path | Ne yapar |
|---|---|---|
| GET | `/uzmanlar/` | Uzman listesi (HTML; `list_for_user` ile filtreli) |
| GET | `/uzmanlar/<code>` | Uzman detayı + brifing (HTML) |
| GET | `/api/experts/` | Liste (slim: persona.system_prompt **hariç**) |
| GET | `/api/experts/<id>` | Tam detay (persona dahil; erişim kontrollü) |
| POST | `/api/experts/suggest` | Phase 10D: kaydedilen snapshot için uzman önerisi |

**Üretici (producer/Atölye) — `presentations/routes_library.py`:**

| Method | Path | Ne yapar |
|---|---|---|
| GET | `/atolye/uzmanlar` | Tüm uzmanları kütüphane kartları olarak listele |
| GET | `/atolye/uzmanlar/<id>` | Yapılandırılmış form editör (YAML değil) |
| POST | `/atolye/uzmanlar/<id>/api/save` | Uzman YAML'ı kaydet + snapshot bağlarını senkronla |

`/api/experts/suggest` (`prisma_home/suggest.py`): `suggest_experts(manifest,
title, description, store, llm)` → güven skoruyla ≤5 öneri; LLM yoksa keyword
fallback; UI `confidence ≥ 0.7`'yi otomatik işaretler.

`DEPT_TO_FEATURED_EXPERT` (`prisma_home/briefings.py`): kullanıcının
departmanını landing hero uzmanına eşler (fallback `liq`).

---

## 6. UI ekranları

- **Tüketici landing / uzman listesi** (`/uzmanlar/`) — uzman kartları
  (code, name, domain, accent_color, glyph), `list_for_user` filtreli.
- **Uzman detay** (`/uzmanlar/<code>`) — brifing bölümleri (LLM/statik) +
  bağlı snapshot kaynakçası.
- **Atölye uzman listesi** (`atolye/uzmanlar.html`) — kart grid'i.
- **Uzman editörü** (`atolye/uzman_edit.html`) — yapılandırılmış form:
  `persona` (system_prompt + voice_examples textarea), `bound_content`
  (blocks/snapshots/processes — BlockStore autocomplete), `briefing_recipe`
  (sections YAML textarea), `access_scope` (read/edit listeleri), `ui`.
  İki kolon: ana içerik + sağ kenar (bağlı blok/snapshot sayaçları).
- **Save modal (Phase 10D)** — sunumu/snapshot'ı kaydederken `/api/experts/
  suggest` ile uzman önerisi; seçilenler `bound_experts`'e yazılır.

---

## 7. Genişleme & açık uçlar

- **Yeni uzman**: `examples/phase_10/experts/<id>.yaml` ekle (id eşleşmeli),
  pod restart / cache invalidation. Değiştirirken `version`'ı artır (spec §5.1).
- **`bound_content.blocks` / `.processes`**: tanımlı ama **henüz kullanılmıyor**
  — Phase 11/12 dolduracak.
- **Snapshot silme temizliği yok**: bir uzman silinince (`YAML` kaldırılınca)
  o id'yi `bound_experts`'inde taşıyan snapshot'lar **otomatik temizlenmez**
  — snapshot yeniden kaydedilene kadar bayat referans kalır.
- **`find_snapshots_bound_to` O(tüm snapshot'lar)** — snapshot sayısı büyürse
  per-uzman index gerekecek (spec §10C).
- **Sunuma persona enjeksiyonu**: istenirse `generate_patch`'e
  `manifest.bound_experts` → `EXPERT_STORE.load` → `persona.system_prompt`'u
  edit prompt'una eklemek gerekir (şu an yok — §4'teki yarı-bağlılık).

---

## Sonsöz — Uzman = brifing personası (henüz sunum personası değil)

Uzman, bir alanın **sesi ve günlük brifing tarifidir**. Snapshot'lara
`bound_experts` ile (snapshot-merkezli) bağlanır; brifing motoru bu bağı
okuyup ilgili snapshot'ları, uzmanın persona'sıyla yeniden ifade ederek
brifing sayfasında kaynakça olarak sunar.

En önemli zihinsel model düzeltmesi: **uzman bağlamak bir sunumun
düzenleme/üretim davranışını DEĞİŞTİRMEZ.** `bound_experts` manifest'te
saklanır ve doğrulanır, ama Sunum LLM akışı onu tüketmez — bu bağ yalnız
tüketici brifing deneyimini besler. Bu ayrımı bilmek, "uzman ekledim ama
sohbet farklı davranmıyor" şaşkınlığını baştan önler.

> **Sonraki doküman­lar:** Keşif / Hazırlık / Sunum **stage** doküman­ları —
> akışların uçtan-uca anlatımı (bu entity doküman­larının birleştiği yer).
