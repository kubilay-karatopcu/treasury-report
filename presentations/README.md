# presentations/ — Treasury Studio Modülü

Bu, ana Treasury app'inde `flask_app/presentations/` altına eklenecek Flask Blueprint'i.

## Dosyalar (faz faz dolduruluyor)

```
__init__.py           Blueprint factory; ana app'le circular dep kaçınmak için lazy import
routes.py             HTTP endpoint'leri: list, editor shell, manifest CRUD, chat, SSE stream, basket, snapshot
graph.py              LangGraph state machine (route → plan → fetch → generate → validate → apply)
manifest.py           TypedDict / dataclass şemaları + invariant validator'ları
patch.py              RFC 6902 subset: apply, inverse, classify
session.py            PresentationSession + SessionRegistry (per-user, per-presentation)
duck.py               Oracle → Arrow → DuckDB bridge; lazy view builder'lar
llm.py                Qwen client; system-prompt + retry pattern'i
store.py              S3 snapshot/recipe persistence

prompts/
  block_edit.txt      Tek blok edit için system prompt
  global_edit.txt     Multi-block / meta edit için system prompt

nodes/                LangGraph node'ları; her biri pure function (state → state)
  route_intent.py     Selection scope'a göre route eder
  plan_fetch.py       render-only / requery / re-fetch karar
  fetch_data.py       Oracle'dan DuckDB'ye veri çeker
  generate_patch.py   Qwen'i çağırır, JSON parse eder, parse fail'de 1 retry
  validate_patch.py   Schema + path scope + immutable + chart-length invariant'ları
  apply_patch.py      Patch'leri uygular, manifest version artırır, SSE event yayar

templates/
  presentations/
    list.html         "Sunumlarım" indeksi
    editor.html       Editor shell, static/js/bundle.js'den React mount eder

static/
  js/editor/          React kaynağı (build.sh ile ../bundle.js'e bundle ediliyor)
    index.jsx         Mount noktası
    App.jsx           Top-level component
    components/       Sidebar, ChatBox, BlockCard, EditPanel, Toast, vs.
    blocks/           Her blok tipi için bir dosya
    lib/
      patch.js        patch.py'ın aynası
      api.js          Fetch + EventSource helper'ları
      store.js        Zustand store
    theme.js          Tabler token'ları (renk, spacing, tipografi)
  css/
    editor.css        Minimal ekleme; Tabler'a yaslanıyor

build.sh              esbuild komutu — static/js/bundle.js üretiyor

tests/
  test_patch.py
  test_manifest.py
  test_session.py
  test_llm_smoke.py   @pytest.mark.integration; gerçek Qwen erişimi gerekiyor
```

## JS bundle build

```bash
cd flask_app/presentations
bash build.sh
```

`static/js/bundle.js` çıktısı veriyor. Editor template bunu `<script src="{{ url_for('presentations.static', filename='js/bundle.js') }}"></script>` ile yüklüyor.

## Test çalıştırma

```bash
# Unit test'ler
pytest flask_app/presentations/tests/ -v

# Integration (Qwen erişimi gerekiyor)
pytest flask_app/presentations/tests/ -v -m integration
```

## Ana app'e register

`flask_app/__init__.py` içinde:

```python
from flask_app.presentations import presentations_bp
app.register_blueprint(presentations_bp, url_prefix="/presentations")
```

Blueprint `current_app.config["DATA_CLIENT"]` üzerinden Oracle handle'ını okuyor. Ana app startup'ta bunu set ediyor. Offline stub'ın nasıl fake DataClient inject ettiğini görmek için `examples/run_local.py`'a bak.
