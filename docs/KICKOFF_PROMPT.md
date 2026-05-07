# Claude Code Başlangıç Promptları

Bu dosyadaki promptları Claude Code session'larına kopyala. İlk başta INITIAL PROMPT'u kullan, sonraki session'larda ilgili faz promptunu.

> **Önemli not**: Promptların kendisi İngilizce yazıldı çünkü Claude Code İngilizce komutları daha tutarlı işliyor — özellikle teknik terimler, dosya yolları, JSON şema isimleri için. Sen Claude Code'la Türkçe konuşabilirsin, o zaten Türkçe anlıyor; ama bu hazır promptları İngilizce bırakmak güvenli. CLAUDE.md de aynı sebeple İngilizce.

---

## BAŞLANGIÇ PROMPTU (yeni session'da yapıştır)

İlk başta bunu yapıştır. Tek seferlik, en başta. Sonraki session'larda daha kısa promptlar yeterli ("Faz 3'e devam, `nodes/generate_patch.py` ile başla").

```
I'm building a new module for an existing Flask application. Read CLAUDE.md at the
repo root before doing anything else — that's the master spec. Then read README.md
for the high-level workflow, skim docs/ for context, and check reference/README.md
to understand the existing app's patterns.

The reference/ folder contains read-only copies of the actual app files:
  - reference/templates/base.html, rates.html, competitor.html
  - reference/static/css/styles.css (design tokens)
  - reference/static/js/*.js (existing frontend patterns)
  - reference/modules/shared/app.py and DataClient.py
  - reference/modules/deposit_panel/*.py (closest analog to what we're building)

Use these to mirror existing patterns. Do NOT modify reference/ — it's read-only.

Important context you should internalize before writing any code:

1. This is NOT a new application. It's a Flask Blueprint that will be dropped into
   an existing Treasury Report Platform. Mirror the patterns described in CLAUDE.md
   under "Repository context". Do not scaffold a new Flask app.

2. We have a separate constraint: I develop locally on this machine, then push to
   GitHub, then pull at the office where the real Oracle EDW + Qwen LLM live.
   Local dev must work without VPN. Use examples/ folder for stubs and fixtures.

3. Build phase by phase as defined at the bottom of CLAUDE.md ("What to build first").
   Each phase ends with something runnable. Do NOT mix phases.

4. We have already validated some assumptions:
   - The frontend design (block-based editor, two view modes, JSON-patch edits)
   - The Qwen LLM can produce valid JSON Patches with system prompt + retry
   - DuckDB is the right working-set engine
   Don't relitigate these — they're in CLAUDE.md as "locked decisions".

5. When in doubt about a design choice, ask me before writing code. When in doubt
   about a Python or JS implementation detail, just pick the cleanest option and
   note it in a comment.

6. The user (me) is Turkish-speaking. You can respond in Turkish if I write in
   Turkish. All user-facing strings in the UI should be in Turkish (button labels,
   placeholders, error messages, etc.). Code comments and identifier names stay
   in English.

Now do the following, in order:

  Step A. Confirm you've read CLAUDE.md by listing the 6 phases and the file layout
          (just the top-level directories under presentations/).
  Step B. Tell me what's missing or unclear in the spec — anything that would block
          you from starting Phase 1. Don't start coding yet.
  Step C. Wait for my answers.

After I respond, we'll start Phase 1 together.
```

---

## FAZ PROMPTLARI (sonraki session'larda)

### Faz 1 — İskelet

```
Phase 1 starts now. Read CLAUDE.md's "What to build first → Phase 1" section
again — it has been updated with concrete answers to your blocking questions.

Key clarifications:

1. base.html and reference patterns: see reference/templates/base.html for the
   real layout. Block names available: title, head, filter_panel, content, scripts.
   Your editor.html extends base.html, overrides only `content` and `scripts`.

2. Local dev base.html: examples/templates/base.html is a minimal stub already
   in place. run_local.py uses ChoiceLoader so the real base.html (when it exists
   in production) takes precedence; offline dev falls back to the stub.

3. Node.js/esbuild: NOT NEEDED FOR PHASE 1. Use CDN-loaded React 18 + Recharts +
   lucide-react UMD builds from cdnjs.cloudflare.com. Phase 2 introduces esbuild.
   This means Phase 1's JS is plain JS using React.createElement — no JSX, no
   build step. (Yes, it's verbose; Phase 1 is small enough to tolerate it. We
   gain by being able to iterate without a build step.)

4. Auth: Real app uses Flask-Login with `current_user`. Attributes:
   `current_user.sicil` (employee ID like "A16438"), `current_user.name`,
   `current_user.department`. Use `current_user.sicil` as owner_id.
   run_local.py is already wired with a fake user matching this shape.

5. Manifest persistence: NONE in Phase 1. List page returns 2 hardcoded entries.
   Editor page reads examples/sample_manifest.json directly into the template.
   No POST /presentations/ creation, no PUT, no DB. Save button can exist but
   disabled with tooltip "Phase 5'te geliyor".

Concrete file deliverables:

- presentations/__init__.py — Blueprint factory exporting presentations_bp
- presentations/routes.py — two endpoints:
    GET /presentations/         → list.html (2 hardcoded presentations)
    GET /presentations/<pid>    → editor.html with embedded manifest
- presentations/templates/presentations/list.html — extends base.html
- presentations/templates/presentations/editor.html — extends base.html, embeds
  manifest as <script id="initial-manifest" type="application/json">{...}</script>
  and loads React + Recharts + lucide UMDs from cdnjs, then loads
  /presentations/static/js/editor/app.js
- presentations/static/js/editor/app.js — single file, no JSX, uses
  React.createElement. Reads initial-manifest, renders blocks read-only.
- presentations/static/css/editor.css — minimal, supplements Tabler

Do NOT in Phase 1:
- Implement chat
- Implement editing/lock toggle
- Add esbuild or build step
- Touch DuckDB or Oracle
- Implement save endpoints
- Create the LangGraph nodes/

UI strings in Turkish ("Sunumlarım", "Sunum Formatına Geç", section headers).
Code in English.

Verify your understanding by listing the files you'll create, then start coding.
After each file, run `cd examples && python run_local.py` and tell me what you see.
```

### Faz 2 — Manifest + patch engine

```
Phase 2: implement the patch engine on both sides.

Backend:
- presentations/manifest.py — dataclasses or TypedDicts for the manifest schema,
  plus validators (chart length consistency, immutable fields, type-specific config).
- presentations/patch.py — apply_patches, compute_inverse, classify_paths.
  Subset of RFC 6902: replace, add, remove only. Reject move/copy/test.
- tests/test_patch.py — fixtures from examples/patch_fixtures.json, round-trip
  with inverse.
- tests/test_manifest.py — schema validation cases including the invalid_patches
  fixtures.

Frontend:
- static/js/editor/lib/patch.js — same applyPatches semantics. Add a small JS
  test that runs from a node script comparing JS output vs the Python fixtures
  (both languages read examples/patch_fixtures.json).
- Wire blocks to the manifest state via Zustand. Clicking a block selects it
  (visual highlight), clicking the lock icon flips locked. No backend mutation
  yet — selection and lock are client-only.

Do not implement chat or LLM yet.
```

### Faz 3 — Chat + LLM

```
Phase 3: connect chat to Qwen and stream patches.

Backend:
- presentations/llm.py — Qwen client, system prompt loaders from prompts/, JSON
  parsing with one retry on parse failure (mirror logic from qwen_patch_test.py
  which we already validated).
- presentations/prompts/{block_edit.txt, global_edit.txt} — the system prompts
  we tested.
- presentations/nodes/{generate_patch.py, validate_patch.py, apply_patch.py} —
  LangGraph nodes. State as defined in CLAUDE.md.
- presentations/graph.py — wire the nodes (skip plan_fetch and fetch_data for now;
  use a passthrough that always routes to render-only).
- routes.py: POST /chat returns a stream token, GET /stream/<token> emits SSE.
  Set X-Accel-Buffering: no header.

Frontend:
- ChatBox component with two contexts (block-scoped vs global), connected to
  EventSource. On each "patch" event, apply via lib/patch.js, flash the affected
  blocks, show a toast with the explanation.

UI strings in Turkish ("Genel Komut", "Düzenleniyor:", placeholders like
"örn. YTD'ye çevir, USD'de göster…").

Hardcoded data is fine in this phase — LLM only mutates config values.
The blocks should still render the original chart values; the LLM "pretends"
to fetch new data by inventing plausible numbers (per the system prompt).
```

### Faz 4 — DuckDB session

```
Phase 4: real data layer.

- presentations/session.py — PresentationSession class + SessionRegistry.
  /tmp/presentations/{user_id}/{presentation_id}/ for files.
- presentations/duck.py — Oracle → pyarrow → DuckDB. The fetch_basket function
  injects row_filter into the SQL. Use the existing DataClient wrapper, NOT raw
  oracledb.
- presentations/nodes/{plan_fetch.py, fetch_data.py} — the routing logic
  (render-only / requery / re-fetch).
- routes.py: POST /basket endpoint that triggers re-fetch.
- Frontend: basket UI in EditSidebar — accordion, checkbox toggles, filter input
  per table. Use examples/sample_catalog.json shape.

For local dev (no Oracle), examples/run_local.py already swaps DataClient for a
SQLite/CSV-backed stub. Don't change that interface.

UI strings in Turkish.
```

### Faz 5 — Persistence + share

```
Phase 5:
- presentations/store.py — S3 snapshot writer + reader. Snapshot = parquet of all
  basket data + frozen manifest JSON + computed render output.
- POST /snapshot endpoint, returns shareable link.
- /presentations/snapshot/<sid> — read-only viewer. Uses the editor template but
  forces viewMode='presentation' and disables editing entirely.
- Distinguish snapshot (immutable, share) from recipe (re-runnable manifest,
  template) in the UI. Two save buttons.

Local dev: replace S3 with filesystem storage under examples/snapshots/.

UI strings in Turkish.
```

### Faz 6 — Cila

```
Phase 6: UX polish and edge cases.
- Lock UX: locked blocks visibly different, chat refuses with helpful message.
- Error states: toast variants, retry buttons, clear empty states.
- Basket completeness: column projection UI, filter syntax help, validation.
- Presentation mode TOC: scroll-spy, smooth scroll, keyboard nav.
- Loading: skeleton states for blocks during re-fetch.
- Concurrent edit: stale manifest hash detection, "someone else edited" banner.

This phase is mostly polish; review and prioritize before starting.

UI strings in Turkish.
```

---

## Claude Code ile çalışırken pratik öneriler

- Her session başında Claude oryantasyonu kaybetmiş gibi geliyorsa `read CLAUDE.md` de.
- Karmaşık değişiklikler için önce plan yazmasını iste, sen onayla, sonra yazsın.
- Her commit tek bir faza ait olsun. Faz ortasında merge yapma.
- `examples/` klasörünü first-class kod gibi düşün, throwaway değil.
- Bir şey spec'le çelişiyorsa önce CLAUDE.md'yi güncelle, sonra kodu değiştir. Spec drift'inden kaçın.
- Anlamlı her değişiklikten sonra test çalıştır: `pytest presentations/tests/ -v`.
- Claude bir karar verirken emin değilse yorum satırı ekleyip sana sormasını iste — "// claude: Zustand mı useReducer mı, sen söyle" gibi.
- Türkçe konuşabilirsin, Claude Türkçe anlıyor. Ama prompt'a `respond in Turkish` eklersen daha tutarlı.

## Hata durumlarında

**"Claude tüm kodu silip yeniden yazmak istiyor"** → Sen onaylamadan yapmasın diye CLAUDE.md'de "ask before destructive changes" notu var. Yine de istiyorsa yeni branch açtırıp ayrı denesin.

**"Claude spec'le çelişen bir şey yazdı"** → "CLAUDE.md'yi tekrar oku ve çelişkili olan kısmı belirle" de. Genelde kendi düzeltir. Yoksa "spec'i mi güncelliyoruz, kodu mu" sorusunu sor.

**"Test'ler patladı"** → Hangi test'in patladığını ve neyi assert ettiğini sor. Test fixture'ları şu an `examples/patch_fixtures.json`'da, hem Python hem JS tarafının aynı dosyayı okuması lazım. Bunu hatırlatmak fayda eder.

**"Build çalışmıyor"** → Office'te eski Node version'ı varsa esbuild patlayabilir. CLAUDE.md'de "esbuild --target=es2020" var ama gerekirse es2018'e düşürebilirsin.
