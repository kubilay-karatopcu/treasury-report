"""
run_local.py — Offline dev runner for the Presentation Editor module.

Starts a minimal Flask app that:
- Registers the presentations Blueprint
- Fakes Flask-Login with LOGIN_DISABLED + a stub user (matching the real User class shape)
- Provides a fake DataClient (CSV-backed) for offline dev without VPN
- Configures Jinja with ChoiceLoader so real templates take precedence over the stub base.html

Run:
    cd examples
    pip install -r requirements.txt
    python run_local.py

Then visit:
    http://localhost:5000/presentations/p_demo
"""

import os
import sys
import json
from pathlib import Path

# Make the parent project importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import Flask, redirect
from flask_login import LoginManager, UserMixin, login_user
from jinja2 import ChoiceLoader, FileSystemLoader


# ============================================================
# FAKE USER — matches the real User class shape from app.py
# ============================================================

class FakeUser(UserMixin):
    """Mirrors the real User class so route code that reads current_user.sicil
    etc. works identically."""
    def __init__(self):
        self.name = "kubilay"
        self.sicil = "A16438"
        self.ip = "127.0.0.1"
        self.department = "Treasury"
        self.password = "fake"
        self.user_id = "A16438"

    def get_id(self):
        return self.sicil


# ============================================================
# FAKE DATA CLIENT — matches the real DataClient.get_data interface
# ============================================================

def make_fake_data_client():
    """Stub DataClient. The real one (DataClient.py at root) connects to Oracle
    EDW via oracledb. This stub reads from CSVs in examples/sample_data/.

    Real interface: dc.get_data(base_prefix=..., dataset=..., query=..., query_params={...})
    Returns a pandas DataFrame.

    Claude Code: when you implement Phase 4, presentations/duck.py should accept
    any object with this same interface — that way this stub satisfies the contract.
    """
    import pandas as pd

    SAMPLE_DATA = Path(__file__).parent / "sample_data"
    SAMPLE_DATA.mkdir(exist_ok=True)

    # Filesystem-backed S3 stub so SessionRegistry.set_manifest /
    # list_user_presentations actually persist in dev. Without these methods
    # the prod-only S3 calls silently no-op, manifests are never written,
    # and the redirect-to-recent + draft auto-save features look broken
    # because there's nothing to read back.
    FAKE_S3_ROOT = Path(__file__).parent / "fake_s3"
    FAKE_S3_ROOT.mkdir(parents=True, exist_ok=True)

    class FakeDataClient:
        def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
            """Pretend to run SQL but read from CSVs. Match by table name in query."""
            query_str = (query or "").upper()
            for csv in SAMPLE_DATA.glob("*.csv"):
                table_name = csv.stem.upper()
                if table_name in query_str or table_name in (dataset or "").upper():
                    df = pd.read_csv(csv)
                    print(f"  [FakeDataClient] {csv.name} -> {len(df)} rows")
                    return df
            print(f"  [FakeDataClient] no match for query: {(query or '')[:80]}...")
            return pd.DataFrame()

        # ── S3-like ops (filesystem-backed; mirror the prod DataClient
        #     surface that SessionRegistry / scope store call into). ──

        def _path(self, key: str) -> Path:
            p = FAKE_S3_ROOT / key.lstrip("/")
            p.parent.mkdir(parents=True, exist_ok=True)
            return p

        def _upload_bytes(self, key: str, body: bytes, content_type: str = None):
            self._path(key).write_bytes(body)

        def read_json(self, key: str):
            import json as _json
            p = self._path(key)
            if not p.exists():
                raise FileNotFoundError(key)
            return _json.loads(p.read_text(encoding="utf-8"))

        def list_prefix(self, prefix: str) -> list[str]:
            base = FAKE_S3_ROOT / prefix.lstrip("/")
            if not base.exists():
                return []
            out = []
            for f in base.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(FAKE_S3_ROOT).as_posix()
                    out.append(rel)
            return out

        def delete_file(self, key: str):
            p = self._path(key)
            if p.exists():
                p.unlink()

    return FakeDataClient()


# ============================================================
# APP FACTORY
# ============================================================

def create_app():
    presentations_dir = ROOT / "presentations"

    app = Flask(__name__)

    # ChoiceLoader: real templates first, stub fallback
    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(str(presentations_dir / "templates")),
        FileSystemLoader(str(Path(__file__).parent / "templates")),  # stub base.html
    ])
    app.static_folder = str(presentations_dir / "static")
    app.static_url_path = "/presentations/static"

    app.config.update(
        SECRET_KEY="dev-only-secret",
        LOGIN_DISABLED=True,  # bypasses @login_required
        PRESENTATIONS_LLM_ENDPOINT=os.environ.get(
            "QWEN_ENDPOINT",
            "https://smg-llm-api.seip-vip-prd-ocpgen11.qnb.com.tr/v1/chat/completions",
        ),
        PRESENTATIONS_LLM_MODEL=os.environ.get("QWEN_MODEL", "qwen3.5-27b"),
        PRESENTATIONS_LLM_TOKEN=os.environ.get("QWEN_TOKEN", "hazine_m8o1et"),
        PRESENTATIONS_S3_BUCKET=None,  # filesystem stub
        PRESENTATIONS_LOCAL_SNAPSHOT_DIR=str(Path(__file__).parent / "snapshots"),
        PRESENTATIONS_SESSION_DIR=str(Path(__file__).parent / "sessions"),
        PRESENTATIONS_SESSION_IDLE_TIMEOUT=1800,
    )

    # Inject fake DataClient — Phase 4 onwards reads this from current_app.config
    app.config["DATA_CLIENT"] = make_fake_data_client()

    # SessionRegistry — Phase 4. Holds per-(user, presentation) DuckDB sessions.
    from presentations.session import SessionRegistry
    app.config["SESSION_REGISTRY"] = SessionRegistry(
        dc=app.config["DATA_CLIENT"],
        duck_base_dir=app.config["PRESENTATIONS_SESSION_DIR"],
        idle_timeout=app.config["PRESENTATIONS_SESSION_IDLE_TIMEOUT"],
    )

    # SnapshotStore — Phase 5. Local filesystem in dev; S3 in prod.
    from presentations.store import LocalSnapshotStore
    app.config["SNAPSHOT_STORE"] = LocalSnapshotStore(
        base_dir=app.config["PRESENTATIONS_LOCAL_SNAPSHOT_DIR"],
    )

    # BlockStore — Phase 6.5.a. Local filesystem in dev; S3 in prod.
    from presentations.blocks.store import LocalBlockStore
    app.config["BLOCK_STORE"] = LocalBlockStore(
        base_dir=Path(__file__).parent / "v2_blocks",
    )

    # TableDocStore — Phase 6.5.b. Reads from examples/table_docs/<SCHEMA>/<TABLE>.yaml.
    from presentations.table_docs.store import LocalTableDocStore, CachedTableDocStore
    app.config["TABLE_DOC_STORE"] = CachedTableDocStore(
        LocalTableDocStore(base_dir=Path(__file__).parent / "table_docs"),
    )

    # ConceptRegistry — Phase 7.a. Reads YAML from presentations/catalog/concepts/.
    # Needed by Keşif (Phase 9.c) for the concept-detail panel served on
    # GET /catalog/concept/<id>. Empty registry falls back to a graceful
    # 404; concept hubs still render on the graph regardless.
    try:
        from presentations.concepts.registry import ConceptRegistry
        app.config["CONCEPT_REGISTRY"] = ConceptRegistry.from_dir(
            Path(__file__).resolve().parent.parent / "presentations" / "catalog" / "concepts",
        )
    except Exception as exc:
        print(f"⚠ CONCEPT_REGISTRY setup skipped: {exc}")
        app.config["CONCEPT_REGISTRY"] = None

    # ScopeStore — Phase 8.a. Needed so the Hazırlık page (which the Phase
    # 9.a Keşif "Hazırlık'a geç" redirects into) can save / load contracts.
    try:
        from presentations.scope.store import LocalScopeStore
        app.config["SCOPE_STORE"] = LocalScopeStore(
            base_dir=Path(__file__).parent / "scopes",
        )
    except Exception as exc:
        print(f"⚠ SCOPE_STORE setup skipped: {exc}")

    # Phase 9.b.1 — Cosmograph license key, fed to the React component at
    # mount time. None during development; populated once the commercial
    # license is procured.
    app.config["COSMOGRAPH_LICENSE_KEY"] = os.environ.get("COSMOGRAPH_LICENSE_KEY")

    # Inject LLM client. Provider precedence:
    #   1. OPENAI_API_KEY in env     → OpenAI (paid, best JSON discipline)
    #   2. GROQ_API_KEY in env       → Groq (Llama 3.3 70B, free, fast)
    #   3. OPENROUTER_API_KEY in env → OpenRouter
    #   4. USE_REAL_LLM=1            → Corporate Qwen (needs VPN)
    #   5. otherwise                 → FakeLLM stub (offline regex)
    from presentations.llm import QwenClient, FakeLLM

    if os.environ.get("OPENAI_API_KEY"):
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        app.config["LLM_CLIENT"] = QwenClient(
            endpoint="https://api.openai.com/v1/chat/completions",
            model=model,
            token=os.environ["OPENAI_API_KEY"],
            verify_ssl=True,
            force_json=True,
        )
        print(f"✓ OpenAI client bound (model={model})")

    elif os.environ.get("GROQ_API_KEY"):
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        app.config["LLM_CLIENT"] = QwenClient(
            endpoint="https://api.groq.com/openai/v1/chat/completions",
            model=model,
            token=os.environ["GROQ_API_KEY"],
            verify_ssl=True,
            force_json=True,
        )
        print(f"✓ Groq client bound (model={model})")

    elif os.environ.get("OPENROUTER_API_KEY"):
        model = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free")
        app.config["LLM_CLIENT"] = QwenClient(
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            model=model,
            token=os.environ["OPENROUTER_API_KEY"],
            verify_ssl=True,
            force_json=True,
        )
        print(f"✓ OpenRouter client bound (model={model})")

    elif os.environ.get("USE_REAL_LLM") == "1":
        app.config["LLM_CLIENT"] = QwenClient(
            endpoint=app.config["PRESENTATIONS_LLM_ENDPOINT"],
            model=app.config["PRESENTATIONS_LLM_MODEL"],
            token=app.config["PRESENTATIONS_LLM_TOKEN"],
            verify_ssl=False,   # corporate self-signed cert
            force_json=False,   # GGUF wrapper may not support it
        )
        print("✓ Corporate Qwen client bound")

    else:
        app.config["LLM_CLIENT"] = FakeLLM()
        print("✓ FakeLLM stub bound (set GROQ_API_KEY to use real LLM)")

    # Set up Flask-Login (with disabled mode it won't redirect to /login,
    # but current_user.* still resolves to a fake authenticated user)
    login_manager = LoginManager(app)
    login_manager.login_view = "login"

    @login_manager.user_loader
    def load_user(user_id):
        return FakeUser()

    # Force a fake user into the request context — works alongside LOGIN_DISABLED
    @app.before_request
    def force_login():
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            login_user(FakeUser())

    # Try to register the real blueprint
    try:
        from presentations import presentations_bp
        app.register_blueprint(presentations_bp, url_prefix="/presentations")
        print("✓ presentations blueprint registered")
    except ImportError as e:
        print(f"⚠ Could not import presentations blueprint yet: {e}")
        print("  This is expected if you haven't built Phase 1 yet.")

        @app.route("/presentations/p_demo")
        def fallback_editor():
            from flask import render_template_string
            with open(Path(__file__).parent / "sample_manifest.json", encoding="utf-8") as f:
                manifest = f.read()
            return render_template_string(
                """
                <!DOCTYPE html><html><head>
                  <title>Presentations Dev (Phase 1 not yet built)</title>
                  <link href="https://rsms.me/inter/inter.css" rel="stylesheet">
                  <style>
                    body { font-family: 'InterVar', sans-serif; padding: 40px; background: #f6f7f9; }
                    pre { background: #fff; padding: 16px; border-radius: 6px; border: 1px solid #e6e7e9;
                          font-family: ui-monospace, monospace; font-size: 12px; overflow: auto; max-height: 60vh; }
                    h1 { color: #1e293b; }
                    p { color: #6c757d; }
                  </style>
                </head><body>
                  <h1>presentations modülü henüz build edilmedi</h1>
                  <p>Claude Code'u kickoff promptuyla başlat ve Phase 1'i çalıştır.</p>
                  <h2>Sample manifest (kullanıma hazır):</h2>
                  <pre>{{ manifest }}</pre>
                </body></html>
                """,
                manifest=manifest,
            )

    @app.route("/")
    def root():
        return redirect("/presentations/p_demo")

    return app


if __name__ == "__main__":
    app = create_app()
    print()
    print("=" * 60)
    print("  Treasury Studio — Local Dev Server")
    print("=" * 60)
    print(f"  http://localhost:5000/presentations/p_demo")
    print()
    print(f"  Sample data: {Path(__file__).parent / 'sample_data'}")
    print(f"  Sessions:    {Path(__file__).parent / 'sessions'}")
    print(f"  Snapshots:   {Path(__file__).parent / 'snapshots'}")
    print()
    app.run(debug=True, host="0.0.0.0", port=5000)
