# Tasarım Kararları — Treasury Studio Sunum Editörü

Bu doküman tasarım konuşmasını sıkıştırılmış, karar formatında özetliyor. Referans olarak kullan; güçlü gerekçe olmadan yeniden açma.

## İki sayfa, paylaşılan tek metadata katmanı

Treasury platformu iki ilgili ama ayrı sayfaya sahip olacak:

1. **Schema Explorer** (ayrı, gelecek modül) — EDW'nin graph görünümü, tablolar için doc panel, "Sepete Ekle" hook'u.
2. **Sunum Editörü** (bu modül) — sepet odaklı, blok bazlı, LLM ile düzenlenen one-pager'lar.

İkisi de aynı tablo dokümantasyon store'unu paylaşıyor. Schema Explorer bu modülün v1'i için scope dışı; buradaki sepet UI daha basit bir tree view kullanıyor.

## Block-based, asla free HTML değil

LLM HTML üretmiyor. Sabit blok library'sine göre tipli manifest üzerinde JSON Patch üretiyor:

- `section_header`
- `kpi`
- `bar_chart`
- `line_chart`
- `narrative`

Gelecek blok tipleri (shared axes için linked_charts, comparison card, small multiples) sadece gerçek kullanıcı baskısı oluşunca eklenecek.

Gerekçe: stabil çıktı, debuggable, share-safe, per-block re-render destekliyor, halüsinasyona karşı dayanıklı.

## Layout: Section mode, Canvas değil

Kullanıcı yapabilir:
- Blokları yeniden sıralama
- Blokları satıra grupla (2 veya 3 kolon)
- Blokları card içine sar
- Section divider ekleme

Kullanıcı YAPAMAZ:
- Font, renk, padding, border radius değiştirme
- Per-block styling override
- Free-form pixel positioning

Tek tema. Tabler token'ları. Branding kullanıcılar ve paylaşımlar arasında tutarlı kalıyor.

## İki view modu

- **Edit modu**: kaynak sidebar'ı (data sources accordion), chat, en altta "Sunuma Geç" CTA.
- **Sunum modu**: TOC sidebar (scroll-spy ile), "Düzenlemeye Dön" CTA, bloklar read-only, daha temiz tipografi, hairline border, beyaz arka plan.

Sidebar aynı slot, içerik mod'a göre swap ediyor. Tüm sidebar header'daki hamburger ile her iki modda da kapatılabiliyor.

## İki katmanlı veri akışı

```
Oracle EDW  →  Per-session DuckDB  →  Block config'leri
   (Katman 1)        (Katman 2)              (render edilen)

   re-fetch         requery                 render-only
   (sepet           (aggregation,           (chart tipi,
    değişti)         filter,                 sıralama, label,
                     window değişti)         narrative)
```

Patch'ler hangi path'leri touch ettiğine göre sınıflandırılıyor. Sadece en pahalı katman Oracle tetikliyor. Render-only sub-second; requery saniyeler; re-fetch onlarca saniye.

## Filter fetch zamanında, render zamanında değil

Row filter Oracle SQL'ine inject ediliyor. Column projection fetch zamanında. Multi-table join'ler presentation zamanında (LLM DuckDB view'larını birleştiriyor), sepet zamanında değil.

Sepetteki her item bir base entity: tablo + column projection + row filter. Sepetin kendisinde multi-table derivation yok — bu karmaşıklık presentation katmanında, LLM'in serbestçe komposizyon yapabildiği yerde.

## Filter syntax: curated UI, free NL değil

Dokümantasyon data steward'ların curate ettiği "common filter"ları içeriyor. Kullanıcı filter'ı tıklıyor, yazmıyor. Free-form custom filter "Advanced" SQL textarea — SQL bilen kullanıcılar için. LLM filter string'lerini natural language'dan parse ETMİYOR (var olmayan kolon adları uydurmak çok kolay).

## Edit operasyonları — JSON Patch RFC 6902 subset

Desteklenen op'lar: `replace`, `add`, `remove`. Desteklenmeyenler: `move`, `copy`, `test`.

Üründeki operation taxonomy:
- `add_block` — append veya insert
- `modify_block` — mevcut bloğun title/config'ini düzenle
- `regenerate_block` — data_query ve render_config'i tamamen değiştir
- `remove_block` — sil

Her biri LLM'in çağırabileceği bir tool. System prompt "regenerate"den önce "modify"yi tercih etmesi gerektiğini açıklıyor — kullanıcı açıkça sıfırdan başla demediği sürece.

## Manuel edit korunması: per-block lock

Bir blokta `locked: true` regenerate operasyonlarının onu atlamasını sağlıyor. UI'da kilit ikonu var. Bu Claude artifact'larının iyi çözemediği bir problemi çözüyor — manuel edit'lerin regeneration'da silinmesi.

## Snapshot vs Recipe

İki ayrı save kavramı:
- **Snapshot** = donmuş veri + donmuş manifest, immutable, S3'te, link ile share edilebilir. Default share aksiyonu.
- **Recipe** = sadece manifest, veri yok. Re-runnable. Rapor şablonu gibi. Advanced aksiyon.

UI'da ayrı butonlar. Default share snapshot — alıcı tam olarak gönderenin gördüğünü görüyor.

## LLM stratejisi

- **Model**: Qwen3.5-27B-GGUF mevcut internal OpenAI-compatible endpoint üzerinden.
- **Yaklaşım**: System prompt + user message + JSON parsing, parse fail'inde 1 retry.
- **Tool calling yok**: GGUF wrapper XML tool call'larını OpenAI formatına çeviremiyor. Talimatları system prompt'ta veriyoruz, JSON'ı message content'inden parse ediyoruz.
- **Validate edilmiş baseline**: 12-case test scripti (`qwen_patch_test.py`) yapısal + semantik doğruluğu test ediyor. Pass rate'e göre grammar constraint gerekip gerekmediğine karar veriyoruz.

## Concurrency

Manifest hash ile last-write-wins. Frontend chat request'iyle birlikte mevcut hash'i gönderiyor. Server'da daha yeni versiyon varsa 409 + yeni manifest dönüyor, frontend reconcile ediyor (muhtemelen sadece reload).

Gerçek collaborative editing yok. Aynı kullanıcı iki sekme açarsa → aynı lock.

## Persistence modeli

- **Manifest** — küçük, durable. Oracle table veya S3 JSON. Versiyonlu (her save = yeni versiyon satırı).
- **DuckDB session** — büyük, ephemeral. `/tmp/{user_id}/{pid}/session.duckdb`. Pod restart wiping; recipe re-run rebuild ediyor.
- **Snapshot** — büyük, durable. S3 parquet + manifest JSON.

Üç farklı store, üç farklı retention policy. Karıştırma.

## Frontend stack

- React 18, function components, hooks.
- Zustand global state için.
- Recharts chart'lar için.
- Lucide icon'lar için.
- Inter font (zaten base.html'de var).
- Tabler class'ları + import edilen theme token'larıyla inline style obje'leri.
- esbuild, page başına tek bundle.js.
- Tailwind compiler yok — Tabler'ın utility'leri yetiyor.
- `localStorage`/`sessionStorage` ortamımızda **kullanılabilir** (claude.ai artifact'lerinden farklı).
  Az kullan, çoğunlukla sidebar collapsed state ve son görüntülenen presentation için.

## Backend stack

- Flask Blueprint, mevcut app'e register ediliyor.
- LangGraph edit graph için (deposit_panel pattern'iyle tutarlı).
- DuckDB Python lib (in-process, dosya backed).
- Oracle erişimi mevcut `DataClient` üzerinden.
- Oracle → DuckDB transfer için pyarrow (pandas NaN/dtype problemi yaşamamak için).
- SSE Flask `Response(generator(), mimetype="text/event-stream")` ile,
  `X-Accel-Buffering: no` header'ıyla.

## Anti-pattern'ler — kaçınılması gerekenler

- **Layout komutlarını LLM'e sorma.** Direct manipulation "yan yana" / "card içinde" / "yeniden sırala" işlerini hallediyor. LLM sadece data ve content yapıyor.
- **Sepet değişince tüm sepet verisini pre-fetch etme.** Lazy fetch et, sadece bir blok gerçekten ihtiyaç duyduğunda.
- **`df.to_dict()` + `jsonify` kullanma.** `df.to_json(orient="records")` + `Response` kullan.
- **Threading lock'ı içine pahalı iş koyma.** Lock sadece atomic ref swap'ı.
- **Font/renk override izin verme.** Bir kez açtın mı, kapatamazsın.
- **Snapshot ve recipe'i karıştırma.** UI'da benzer görünseler de farklı lifecycle'ları var.
- **LLM'in ürettiği SQL'e schema validation olmadan güvenme.** Execute etmeden önce her column referansını sepet schema cache'e karşı kontrol et.
