# Treasury Studio — Sunum Editörü Blueprint'i

Bu klasör, mevcut Treasury Report Platform'a eklenecek yeni Sunum Editörü modülü için **şablon + iskelet** içeriyor. Tek başına çalışan bir uygulama değil — Claude Code ile lokalde geliştirilip mevcut Flask projesine Blueprint olarak eklenmek üzere tasarlandı.

## Bu klasörde ne var

- **`CLAUDE.md`** — ana spec. Claude Code önce bunu okuyor. Mimari, dosya yapısı, manifest şeması, API yüzeyi, kodlama standartları, faz planı içeriyor. (İngilizce — Claude Code İngilizce teknik talimatları daha tutarlı işliyor.)
- **`reference/`** — mevcut Treasury app'inden read-only kopyalar (`base.html`, `styles.css`, `DataClient.py`, `deposit_panel/*` vs.). Claude Code pattern'leri buradan öğreniyor. **Düzenleme.**
- **`docs/`** — tasarım kararları ve referans dokümanlar (Türkçe).
- **`presentations/`** — modül iskeleti. Boş stub'lar ve prompt dosyaları. Claude Code faz faz dolduruyor.
- **`examples/`** — fixture'lar + offline dev runner. Hem Python testleri hem VPN'siz frontend dev için.

## Nasıl kullanılacak

### 1. Lokal kurulum (senin makinen)

```bash
# Repo'yu klonla (GitHub'da yaratacağın)
git clone <repo>
cd <repo>

# Claude Code yükle (yoksa)
npm install -g @anthropic-ai/claude-code

# Repo root'unda Claude Code başlat
claude
```

### 2. Claude Code'u başlat

`docs/KICKOFF_PROMPT.md` içindeki kickoff promptunu Claude Code session'ına yapıştır. Şunları söylüyor:
- Spec'in nerede olduğu (`CLAUDE.md`)
- Hangi fazdan başlanacağı
- "Bitti" sayılması için ne gerektiği

Faz faz ilerle. Hepsini bir seferde isteme.

### 3. Offline dev ortamında test et

`examples/` klasöründe Oracle veri katmanını SQLite + örnek veriyle taklit eden minimal bir Flask runner var. VPN'siz, ofis dışında editor UX üzerinde iterasyon yapabiliyorsun.

```bash
cd examples
pip install -r requirements.txt
python run_local.py
# → http://localhost:5000/presentations/p_demo
```

### 4. GitHub'a push'la

```bash
git add presentations/
git commit -m "Phase N: <ne yaptın>"
git push origin main
```

### 5. Ofiste pull et ve entegre et

```bash
git pull
# Modülü Treasury app'ine kopyala
cp -r presentations/ /path/to/treasury_app/flask_app/

# flask_app/__init__.py'a blueprint kaydı ekle:
#   from flask_app.presentations import presentations_bp
#   app.register_blueprint(presentations_bp, url_prefix="/presentations")

# JS bundle'ı build et
cd /path/to/treasury_app/flask_app/presentations
bash build.sh

# Test et
python -m pytest tests/

# Mevcut OpenShift pipeline'ı ile deploy et
```

## Faz takibi

Her fazı tamamladıkça işaretle.

- [ ] **Faz 1** — İskelet: blueprint + statik editor sayfası hardcoded manifest render ediyor
- [ ] **Faz 2** — Manifest + patch engine (Python + JS aynası, testler geçiyor)
- [ ] **Faz 3** — Chat + LLM (Qwen ile tek-blok edit, SSE streaming)
- [ ] **Faz 4** — DuckDB session (Oracle fetch, requery vs re-fetch routing)
- [ ] **Faz 5** — Persistence + share (S3 snapshot, sunum modu)
- [ ] **Faz 6** — Cila (lock UX, sepet UI, error state'leri)

## Kilitli tasarım kararları

Bunlar tasarım fazından çıktı. **Yeniden açma** — kararlaştı.

1. **Block-based, free HTML değil.** LLM yapısal manifest'i editliyor, asla HTML üretmiyor.
2. **Edit'ler için JSON Patch**, RFC 6902 subset (sadece replace/add/remove).
3. **İki katmanlı veri modeli**: Oracle → DuckDB session → block config'leri. LLM DuckDB üzerinde çalışıyor (ucuz, hızlı). Sepet değiştiğinde Oracle'dan re-fetch.
4. **Section mode layout**, Canvas mode değil. Sınırlı container'lar (row, card, section_header). Font/renk override yok.
5. **İki view modu**: edit (kaynak sidebar + chat) ve presentation (TOC sidebar, read-only).
6. **Per-session DuckDB dosyası** `/tmp` altında. Pod restart silyor; manifest durable store'da tutulur.
7. **Filter fetch zamanında**: row filter Oracle SQL'ine inject ediliyor. Column projection fetch zamanında. Multi-table join'ler basket'ta değil, presentation katmanında.
8. **Tek LLM modeli**: Qwen3.5-27B-GGUF mevcut internal endpoint üzerinden. v1'de fallback yok.
9. **v1'de collaborative editing yok.** Per-presentation advisory lock.
10. **Snapshot ve Recipe ayrı kavramlar**. Snapshot = donmuş veri + manifest, immutable, share edilebilir. Recipe = sadece manifest, re-runnable.

## Mevcut uygulamadan gelen kısıtlar

- Qwen GGUF, OpenAI tool-calling formatını desteklemiyor. Düz JSON-in-content + parsing.
- Oracle driver: bazı pod'larda `oracledb` thin mode, bazılarında thick. DuckDB transfer için Arrow bridge kullan, pandas dtype problemi (`NaN` recasting) yaşamamak için.
- ODH reverse proxy `X-Forwarded-Prefix` header'ını güvenilmez şekilde geçiriyor — middleware `SCRIPT_NAME`'i manuel inject ediyor. Bunu bozma.
- `@` içeren CDN URL'leri corporate proxy tarafından bozuluyor. cdnjs.cloudflare.com path'lerini kullan.
- `load_user()` her request'te internal API'ye gidiyor. Per-request iş yükünü minimize et.
- DataFrame → JSON: her zaman `df.to_json(orient="records")` + `flask.Response`, asla `jsonify(df.to_dict(...))`.
