# -*- coding: utf-8 -*-
"""
Created on Thu Jan 22 11:34:07 2026

@author: S82464
"""
import os
import threading
import requests
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from flask_login import UserMixin, LoginManager, current_user, login_required, logout_user, login_user
from flask import Flask, render_template, request, jsonify, url_for, redirect, Response
import pandas as pd
from flask_wtf import FlaskForm
from wtforms.validators import DataRequired
import json
from datetime import datetime, timedelta
from os import environ
import logging
import time
import numpy as np
import calendar
from DataClient import DataClient
from deposit import deposit_bp
import re
from presentations import presentations_bp
from prisma_home import prisma_home_bp
from prisma_home.experts import LocalExpertStore, S3ExpertStore
from prisma_home.briefing import BriefingEngine
from presentations.session import SessionRegistry
from presentations.store import S3SnapshotStore, S3DashboardStore
from presentations.scope.store import S3ScopeStore
from presentations.blocks.store import S3BlockStore, LocalBlockStore
from presentations.table_docs.store import (
    S3TableDocStore, LocalTableDocStore, CachedTableDocStore,
)
from presentations.concepts.registry import CachedConceptRegistry, S3ConceptRegistry
from presentations.concepts.bindings import CachedBindingCatalog, S3BindingCatalog
from presentations.variables.semantic_tags import set_active_registry
from presentations.llm import QwenClient
import prisma_nav
from pathlib import Path
import tempfile

requests.packages.urllib3.disable_warnings()

# ── Dev / Prod modu ───────────────────────────────────────────────────────────
# DEV_MODE=True  → Oracle/S3/LLM bağlantıları devre dışı, fake user inject edilir.
# DEV_MODE=False (varsayılan) → production davranışı, hiçbir şey değişmez.
# Geçiş: ortam değişkenini set et/kaldır.  İş bilgisayarında set etme = prod modu.
DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')

logging.info("Script started working...")
start_time = time.time()


if DEV_MODE:
    logging.info("DEV_MODE=True — Oracle / S3 / LLM bağlantıları devre dışı.")

    import fake_db
    import re as _re

    _ORACLE_TO_STRFTIME = [
        ("YYYY", "%Y"), ("YY", "%y"),
        ("MONTH", "%B"), ("MON", "%b"), ("MM", "%m"),
        ("DD", "%d"), ("DY", "%a"), ("DAY", "%A"),
        ("HH24", "%H"), ("HH12", "%I"), ("HH", "%H"),
        ("MI", "%M"), ("SS", "%S"),
    ]

    def _oracle_fmt_to_strftime(fmt: str) -> str:
        out = fmt
        for ora, py in _ORACLE_TO_STRFTIME:
            out = out.replace(ora, py)
        return out

    def _rewrite_oracle_for_duckdb(sql: str) -> str:
        """Minimal Oracle→DuckDB syntax rewrite for DEV_MODE block SQL execution."""
        s = sql
        s = _re.sub(r'\bSYSDATE\b', 'CURRENT_DATE', s, flags=_re.IGNORECASE)
        s = _re.sub(r'\bNVL\s*\(', 'COALESCE(', s, flags=_re.IGNORECASE)

        # ADD_MONTHS(date, n) → (date + INTERVAL '1 month' * (n))
        def _add_months_repl(m):
            return f"({m.group(1).strip()} + INTERVAL '1 month' * ({m.group(2).strip()}))"
        s = _re.sub(
            r"ADD_MONTHS\s*\(\s*([^,]+?)\s*,\s*(-?\d+)\s*\)",
            _add_months_repl,
            s, flags=_re.IGNORECASE,
        )

        # TO_CHAR(date, 'FMT') → strftime(date, 'FMT_CONVERTED')
        def _to_char_repl(m):
            expr = m.group(1).strip()
            fmt = _oracle_fmt_to_strftime(m.group(2))
            return f"strftime({expr}, '{fmt}')"
        s = _re.sub(
            r"TO_CHAR\s*\(\s*([^,]+?)\s*,\s*'([^']+)'\s*\)",
            _to_char_repl,
            s, flags=_re.IGNORECASE,
        )

        s = _re.sub(
            r"TRUNC\s*\(([^,]+),\s*'Q'\s*\)",
            r"DATE_TRUNC('quarter', \1)",
            s, flags=_re.IGNORECASE,
        )
        s = _re.sub(
            r"TRUNC\s*\(([^,]+),\s*'MM'\s*\)",
            r"DATE_TRUNC('month', \1)",
            s, flags=_re.IGNORECASE,
        )

        # Aggregation gate Oracle'a göre WHERE ROWNUM <= N ekliyor — DuckDB
        # ROWNUM bilmiyor, LIMIT'e çevir. (WHERE ROWNUM clause'u tek koşul ise.)
        s = _re.sub(
            r"\bWHERE\s+ROWNUM\s*<=\s*(\d+)\s*$",
            r"LIMIT \1",
            s, flags=_re.IGNORECASE,
        )
        s = _re.sub(
            r"\bAND\s+ROWNUM\s*<=\s*(\d+)\b",
            r"",  # AND'li ek koşul ise sil — LIMIT zaten yukarıdaki kuralla eklenecek (basit)
            s, flags=_re.IGNORECASE,
        )
        # Inline ROWNUM <= N → LIMIT (yukarıdaki yakalamadıysa)
        s = _re.sub(
            r"\bROWNUM\s*<=\s*(\d+)\b",
            "TRUE",  # standalone kalırsa effectsiz yap; LIMIT eklenmediyse o satır gate'ten geldi demektir
            s, flags=_re.IGNORECASE,
        )
        return s

    class _StubDataClient:
        """Fake DataClient — DEV_MODE only.

        Two get_data paths:
        - dataset = bir fake_db tablosu (örn. "EDW.DEPOSITS_DAILY") → tam
          DataFrame döner (legacy basket fetch yolu).
        - dataset = "block::xxx" veya başka (LLM tarafından üretilmiş block
          SQL) → query DuckDB'de çalıştırılır, fake_db tabloları schema'lı
          view olarak register'lanmış durumda.
        """
        def __init__(self):
            self._duck = None
            # S3 replacement: persist uploads (xlsx) ve diğer key-bytes'ları
            # local filesystem'e yaz. `read_bytes` aynı yerden okur.
            self._fs_root = Path(tempfile.gettempdir()) / "prisma-treasury-fs"
            self._fs_root.mkdir(parents=True, exist_ok=True)

        def _key_path(self, key: str):
            # Slash'ları flat dosya adına çevir (alt dizin yaratmıyoruz, basitlik için).
            safe = key.replace("/", "__").replace("\\", "__")
            return self._fs_root / safe

        def _get_duck(self):
            if self._duck is None:
                from presentations.duck import connect_duckdb
                conn = connect_duckdb(":memory:")
                seen_schemas = set()
                for tid in fake_db.known_tables():
                    df = fake_db.get(tid)
                    if "." in tid:
                        schema, tbl = tid.split(".", 1)
                        if schema not in seen_schemas:
                            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                            seen_schemas.add(schema)
                        view_alias = f"_fake_{tid.replace('.','_')}"
                        conn.register(view_alias, df)
                        conn.execute(
                            f'CREATE OR REPLACE VIEW "{schema}"."{tbl}" AS '
                            f'SELECT * FROM {view_alias}'
                        )
                    else:
                        conn.register(tid, df)
                self._duck = conn
            return self._duck

        def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kw):
            # Path 1: legacy basket — dataset is the table_id itself
            if dataset and not str(dataset).startswith("block::"):
                df = fake_db.get(dataset)
                if df is not None:
                    return df
            # Path 2: LLM block SQL — execute via DuckDB
            if query:
                rewritten = _rewrite_oracle_for_duckdb(query)
                # Phase 6.5: Oracle :name binds → DuckDB $name binds.
                # Block engine emits :ident style; DuckDB's parser uses $ident.
                if query_params:
                    rewritten = _re.sub(
                        r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)\b",
                        r"$\1",
                        rewritten,
                    )
                try:
                    if query_params:
                        return self._get_duck().execute(rewritten, query_params).fetchdf()
                    return self._get_duck().execute(rewritten).fetchdf()
                except Exception as exc:
                    # Surface the actual DuckDB error so the LLM retry loop can
                    # see it and fix the SQL. Re-raise as a RuntimeError that
                    # execute_block_sql will catch and turn into a validation
                    # error (which then triggers a retry).
                    logging.warning(
                        "DEV_MODE: fake DuckDB SQL execute failed: %s\n"
                        "  original SQL: %s\n  rewritten SQL: %s",
                        exc, (query or "")[:300], rewritten[:300],
                    )
                    raise RuntimeError(f"DuckDB (fake_db): {exc}") from exc
            return pd.DataFrame()

        def _upload_bytes(self, key, body, content_type=None):
            self._key_path(key).write_bytes(body if isinstance(body, (bytes, bytearray)) else bytes(body))

        def read_bytes(self, key):
            p = self._key_path(key)
            if not p.exists():
                return b""
            return p.read_bytes()

        def read_json(self, key):
            import json as _json
            p = self._key_path(key)
            if not p.exists():
                raise FileNotFoundError(f"DEV_MODE: key not found: {key}")
            return _json.loads(p.read_text(encoding="utf-8"))

        def delete_file(self, key):
            p = self._key_path(key)
            if p.exists():
                p.unlink()

        def list_prefix(self, prefix):
            # S3 list_prefix → tüm key'ler ki bu prefix ile başlar
            safe_prefix = prefix.replace("/", "__").replace("\\", "__")
            return [
                p.name.replace("__", "/")
                for p in self._fs_root.iterdir()
                if p.name.startswith(safe_prefix)
            ]

    dc = _StubDataClient()
    logging.info("DEV_MODE: fake_db hazır — %s", ", ".join(fake_db.known_tables()))
else:
    dc = DataClient()



XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
 

def _s3_put(key: str, body: bytes) -> None:
    dc._upload_bytes(key, body, content_type=XLSX_CONTENT_TYPE)
 
def _s3_get(key: str) -> bytes:
    return dc.read_bytes(key)
 
def _s3_delete(key: str) -> None:
    dc.delete_file(key)

app = Flask(__name__)
app.context_processor(prisma_nav.inject)

app.config['TIMEOUT'] = 240
app.config['LOGIN_DISABLED'] = DEV_MODE or (environ.get("LOGIN_DISABLED") == "True")
SECRET_KEY = os.urandom(32)
REFRESH_TOKEN = environ.get("REFRESH_TOKEN")
app.config['SECRET_KEY'] = SECRET_KEY
PARAMS = {"appName": "treasuryReportPlatform"}

LLM_API_URL = environ.get(
    "LLM_API_URL",
    "https://smg-llm-api.seip-vip-prd-ocpgen11.qnb.com.tr/v1/chat/completions",
)
LLM_API_KEY = environ.get("LLM_API_KEY", "")



app.config["DATA_CLIENT"] = dc

# Pod-local DuckDB cache (Windows + Linux + macOS uyumlu)
_DUCK_BASE_DIR = Path(tempfile.gettempdir()) / "prisma-treasury-duck"

app.config["SESSION_REGISTRY"] = SessionRegistry(
    dc=dc,
    duck_base_dir=_DUCK_BASE_DIR,
    idle_timeout=1800,
)

if DEV_MODE:
    from presentations.llm import FakeLLM
    from presentations.store import LocalSnapshotStore, LocalDashboardStore
    from presentations.scope.store import LocalScopeStore

    _openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if _openai_key:
        _openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        logging.info("DEV_MODE: OPENAI_API_KEY bulundu → OpenAI (%s) kullanılıyor.", _openai_model)
        app.config["LLM_CLIENT"] = QwenClient(
            endpoint="https://api.openai.com/v1/chat/completions",
            token=_openai_key,
            model=_openai_model,
            verify_ssl=True,
            force_json=True,
        )
    else:
        logging.info("DEV_MODE: OPENAI_API_KEY yok → FakeLLM stub.")
        app.config["LLM_CLIENT"] = FakeLLM()

    app.config["SNAPSHOT_STORE"]  = LocalSnapshotStore(base_dir=_DUCK_BASE_DIR / "snapshots")
    app.config["DASHBOARD_STORE"] = LocalDashboardStore(base_dir=_DUCK_BASE_DIR / "dashboards")
    app.config["BLOCK_STORE"]     = LocalBlockStore(base_dir=_DUCK_BASE_DIR / "v2_blocks")
    app.config["SCOPE_STORE"]     = LocalScopeStore(base_dir=_DUCK_BASE_DIR / "scopes")
    app.config["TABLE_DOC_STORE"] = CachedTableDocStore(
        LocalTableDocStore(base_dir=Path(__file__).parent / "examples" / "table_docs")
    )
    app.config["DEV_MODE"]        = True  # presentations/directory.py için bayrak
    # DEV catalog → fake_db ile aynı tablolar (examples/sample_catalog.json).
    app.config["CATALOG_PATH"] = str(Path(__file__).parent / "examples" / "sample_catalog.json")
else:
    # Production: Qwen GGUF — model field gönderme (endpoint reddediyor).
    app.config["LLM_CLIENT"] = QwenClient(
        endpoint=LLM_API_URL,
        token=LLM_API_KEY,
        verify_ssl=False,
        force_json=False,
    )
    app.config["SNAPSHOT_STORE"]  = S3SnapshotStore(dc=dc)
    app.config["DASHBOARD_STORE"] = S3DashboardStore(dc=dc)
    app.config["BLOCK_STORE"]     = S3BlockStore(dc=dc)
    app.config["SCOPE_STORE"]     = S3ScopeStore(dc=dc)
    app.config["TABLE_DOC_STORE"] = CachedTableDocStore(S3TableDocStore(dc=dc))

 
app.config["S3_GET"]    = _s3_get
app.config["S3_PUT"]    = _s3_put
app.config["S3_DELETE"] = _s3_delete

# Phase B — shared library-block cache + background refetch dispatcher.
# Same backend (DuckDB on local FS) for both DEV and prod; the cache file
# lives next to the per-session DuckDB files under PRESENTATIONS_SESSION_DIR.
from presentations.cache.library_block_cache import LibraryBlockCache as _LBC
from presentations.cache.refresh_dispatcher import RefreshDispatcher as _RD
app.config["LIBRARY_BLOCK_CACHE"] = _LBC(
    db_path=_DUCK_BASE_DIR / "library_block_cache.duckdb",
)
app.config["LIBRARY_REFRESH_DISPATCHER"] = _RD(max_workers=2)

# NOT: Eski LibraryRefreshScheduler (LIBRARY_STORE warm-cache) kaldırıldı —
# tek depo = BLOCK_STORE. Kütüphane bloklarının lazy_ttl önbelleği apply-filters
# içinde per-request serve-stale + LIBRARY_REFRESH_DISPATCHER ile çalışmaya
# devam eder; proaktif tarama yok. (DatasetScheduler dataset cron'unu yapar.)

# Faz A — dataset-level refresh. Materialises cached scope datasets to S3
# parquet on schedule; N Sunum charts reference ONE dataset → one query per
# interval (block-level dedup the old model lacked). Same single-process caveat
# as the library scheduler above (gate by worker_id==0 / sidecar in prod).
from presentations.cache.dataset_scheduler import DatasetScheduler as _DSS
app.config["DATASET_REFRESH_DISPATCHER"] = _RD(max_workers=2, name="dataset-refresh")
app.config["DATASET_REFRESH_SCHEDULER"] = _DSS(
    scope_store=app.config["SCOPE_STORE"],
    data_client=dc,
    dispatcher=app.config["DATASET_REFRESH_DISPATCHER"],
    concept_registry=app.config.get("CONCEPT_REGISTRY"),
    binding_catalog=app.config.get("CONCEPT_BINDING_CATALOG"),
    poll_interval_seconds=60,
)
app.config["DATASET_REFRESH_SCHEDULER"].start()

# Phase 10B — Expert registry.
#   DEV  : git fixtures (examples/phase_10/experts/) via LocalExpertStore.
#   PROD : S3ExpertStore — uzmanlar S3'ten okunur, Atölye'den düzenlenince S3'e
#          yazılır (pod restart'ta kalıcı; bloklar/snapshot'larla aynı parite).
#          PROD BOŞ başlar; uzmanlar /atolye/uzmanlar üzerinden oluşturulur.
if DEV_MODE:
    _EXPERTS_DIR = Path(__file__).parent / "examples" / "phase_10" / "experts"
    app.config["EXPERT_STORE"] = LocalExpertStore(base_dir=_EXPERTS_DIR)
    logging.info("EXPERT_STORE (DEV) loaded from %s", _EXPERTS_DIR)
else:
    app.config["EXPERT_STORE"] = S3ExpertStore(dc=dc)
    logging.info("EXPERT_STORE (PROD) = S3ExpertStore")

# Phase 10E — Briefing engine. In-process content-hash cache (Phase 12 spec
# §10.4 calls for Redis when multi-pod consistency matters). Falls back to
# the StaticBriefing when LLM_CLIENT is missing or returns garbage so the
# consumer experience degrades gracefully.
app.config["BRIEFING_ENGINE"] = BriefingEngine(
    expert_store=app.config["EXPERT_STORE"],
    snapshot_store=app.config["SNAPSHOT_STORE"],
    llm_client=app.config.get("LLM_CLIENT"),
)
logging.info("BRIEFING_ENGINE wired (cache=in-process, fallback=static MD)")

# Phase 8.a — routing override ceiling. A user may force a system-decided
# `lazy` table to `cached`, but never above this size (DuckDB would thrash).
# See presentations/scope/routing.py::apply_user_override.
from presentations.scope.routing import DEFAULT_HARD_CEILING_BYTES as _ROUTING_CEILING
app.config["PRESENTATIONS_ROUTING_HARD_CEILING_BYTES"] = int(
    os.environ.get("PRESENTATIONS_ROUTING_HARD_CEILING_BYTES", _ROUTING_CEILING)
)


# ── Phase 7.a — concept registry ──────────────────────────────────────────
# Hand-authored knowledge docs live under presentations/catalog/ (spec §3.1:
# system/dept concepts are git-versioned). concepts/ holds the registry;
# tables/ holds per-table concept bindings (read by the 7.b compiler). Same
# path in DEV and prod for parity. The cached registry hot-reloads on YAML
# mtime change so the data team can edit without a restart.
# DEV: git fixtures (dir, mtime hot-reload). PROD: S3 (concept'ler + binding'ler
# Konseptler UI'ından S3'e yazılır; S3 boşsa ilk boot'ta aynı git fixtures'tan
# seed edilir → sonra local dir artık kaynak değil).
_CONCEPT_DIR = Path(__file__).parent / "presentations" / "catalog" / "concepts"
_BINDING_DIR = Path(__file__).parent / "presentations" / "catalog" / "tables"
if DEV_MODE:
    concept_registry = CachedConceptRegistry(_CONCEPT_DIR)
    app.config["CONCEPT_BINDING_CATALOG"] = CachedBindingCatalog(_BINDING_DIR)
else:
    concept_registry = S3ConceptRegistry(dc, fixtures_dir=_CONCEPT_DIR)
    app.config["CONCEPT_BINDING_CATALOG"] = S3BindingCatalog(dc, fixtures_dir=_BINDING_DIR)
app.config["CONCEPT_REGISTRY"] = concept_registry
# Back the semantic-tag allow-list (block validation + UI dropdown) with the
# registry; SEMANTIC_TAGS_V0 stays as the baseline floor (zero regression).
set_active_registry(concept_registry)
logging.info("CONCEPT_REGISTRY: %d concepts | BINDING_CATALOG: %d tables (%s)",
             len(concept_registry), len(app.config["CONCEPT_BINDING_CATALOG"]),
             "DEV/dir" if DEV_MODE else "PROD/S3")



login_manager = LoginManager()
login_manager.init_app(app)

login_manager.login_view = "login"
login_manager.login_message = "Bu sayfayı görmek için lütfen önce giriş yapın."

# LLM_API_URL = environ.get("LLM_API_URL")
# LLM_API_KEY = "environ.get("LLM_API_KEY")"


## 3. Add these functions anywhere before the routes:
 

class User(UserMixin):
    def __init__(self, user_json):
        self.user_json = user_json
        self.name = user_json["name"]
        self.sicil = user_json["sicil"]
        self.ip = user_json["ip"]
        self.department = user_json["department"]
        self.password = user_json["password"]
        # Dashboard maker yetkisi — LDAP tablosundan gelecek. Şimdilik 1.
        # Tabloya kolon eklendiğinde user_json["dashboard_maker"] olarak okunur.
        self.dashboard_maker = int(user_json.get("dashboard_maker", 1)) == 1

    def get_id(self):
        object_id = self.user_json.get('user_id')
        return str(object_id)

    def check_password(self, inputPass):
        if self.password == inputPass:
            return True
        else:
            return False


class LoginForm(FlaskForm):
    sicil = StringField('Sicil', validators=[DataRequired()])
    password = PasswordField('Şifre', validators=[DataRequired()])
    remember_me = BooleanField('Beni Hatırla')
    submit = SubmitField('Giriş Yap')


print("Server has started working.")

 

app.register_blueprint(deposit_bp, url_prefix="/deposit-assistant")
app.register_blueprint(presentations_bp, url_prefix="/presentations")
# Phase 10A: PRISMA shell blueprint owns "/" (consumer landing) and "/atolye/*".
# Spec §3 — pre-existing index route is replaced by prisma_home.landing.
app.register_blueprint(prisma_home_bp, url_prefix="")

_user_cache = {}

if DEV_MODE:
    _dev_user = User({
        "name": "Dev User",
        "sicil": "A00000",
        "ip": "127.0.0.1",
        "department": "FİNANSAL YAPAY ZEKA UYGULAMALARI",
        "password": "",
        "user_id": "dev_user",
    })

    @app.before_request
    def _inject_dev_user():
        login_user(_dev_user)


@login_manager.user_loader
def load_user(user_id):
    if DEV_MODE:
        return _dev_user
    now = time.time()
    if user_id in _user_cache:
        user, ts = _user_cache[user_id]
        if now - ts < 300:
            return user
    print(user_id)
    data = dc.get_data(
            base_prefix="ldap",
            dataset = "login",
            query = "./queries/LDAPByID.sql",
            query_params={"user_id": user_id}
        )

    u = {"name": data["NAME"].values[0],
         "sicil": data["SICIL"].values[0],
         "ip": data["IP"].values[0],
         "department": data["DEPARTMENT"].values[0],
         "password": data["PASSW"].values[0],
         "user_id": data["USER_ID"].values[0]}
    
    if not u: return None
    user = User(u)
    _user_cache[user_id] = (user, now)
    return user

SIDEBAR_RULES  = {
  "AKTİF PASİF YÖNETİMİ İŞTİRAKLER KOORDİNASYON": {
    "uygulamalar.asistan": False
   },
  "BİLANÇO ANALİZİ VE MEVDUAT YÖNETİMİ": {
    "uygulamalar.asistan": False
   },
  "AKTİF PASİF YÖNETİMİ VE FON TRANSFER FİYATLAMASI": {
    "uygulamalar.asistan": False
   },
  "HAZİNE SATIŞ": {
    "uygulamalar.asistan": False
   },
  "BİLANÇO YÖNETİMİ": {
    "uygulamalar.asistan": False
   },
  "FİNANSAL YAPAY ZEKA UYGULAMALARI": {
    "uygulamalar.asistan": True
   },
  "MYU": {
    "uygulamalar.asistan": False
   },
  "IBTECH-INF OPEN SOLUTIONS": {
    "uygulamalar.asistan": False
   }
}

ROUTE_ACCESS_MAP = {
    # Uygulamalar — deposit-assistant (deposit-panel kaldırıldı; legacy
    # treasury route'ları da temizlendi → her şey presentations üzerinden).
    "deposit.chat":         "uygulamalar.asistan",
}
 
 
def _resolve_sidebar_key(rules, key):
    """
    Hiyerarşik fallback ile key çözümleme.
    Önce tam key'e bak, yoksa parent'a, yoksa True döndür.
    
    Örnek: key="uygulamalar.asistan"
      1. rules.get("uygulamalar.asistan") → varsa döndür
      2. rules.get("uygulamalar") → varsa döndür
      3. True
    """
    if key in rules:
        return rules[key]
    # Parent'a fallback
    if "." in key:
        parent = key.rsplit(".", 1)[0]
        if parent in rules:
            return rules[parent]
    return True
 
 
def _get_user_rules():
    """Mevcut kullanıcının departmanına göre kuralları döndür."""
    if app.config.get("LOGIN_DISABLED"):
        return {}
    try:
        if current_user.is_authenticated:
            return SIDEBAR_RULES.get(current_user.department, {})
    except Exception:
        pass
    return {}
 
 
@app.context_processor
def inject_sidebar_helpers():
    """
    Template'lere iki şey enjekte eder:
      1. sv(key) → Jinja'da {% if sv('uygulamalar.asistan') %} şeklinde kullanılır
      2. sidebar_rules → debug veya advanced kullanım için (opsiyonel)
    """
    rules = _get_user_rules()
 
    def sv(key):
        return _resolve_sidebar_key(rules, key)
 
    return {"sv": sv, "sidebar_rules": rules}
 
 
@app.before_request
def enforce_route_access():
    """
    Sidebar'da gizlenen route'lara URL ile doğrudan erişimi engeller.
    Login gerektirmeyen route'ları (login, static, vs.) atlar.
    """
    # Login disabled ise kontrol yapma
    if app.config.get("LOGIN_DISABLED"):
        return None
 
    endpoint = request.endpoint
    if not endpoint:
        return None
 
    # Route access map'te tanımlı mı?
    sidebar_key = ROUTE_ACCESS_MAP.get(endpoint)
    if sidebar_key is None:
        return None  # tanımlı değilse kısıtlama yok
 
    # Kullanıcı login olmamışsa login_manager halleder, biz karışmayalım
    try:
        if not current_user.is_authenticated:
            return None
    except Exception:
        return None
 
    rules = _get_user_rules()
    if not _resolve_sidebar_key(rules, sidebar_key):
        from flask import abort
        abort(403)
 
    return None

@app.context_processor
def inject_sidebar_visibility():
    """Her template'e sidebar_visible dict'ini enjekte eder."""
    default = {"mevduat": True, "sektor": True, "uygulamalar": True}
 
    if app.config.get("LOGIN_DISABLED"):
        return {"sidebar_visible": default}
 
    try:
        dept = current_user.department if current_user.is_authenticated else None
    except Exception:
        dept = None
 
    if dept and dept in SIDEBAR_RULES :
        visibility = {**default, **SIDEBAR_RULES [dept]}
    else:
        visibility = default
 
    return {"sidebar_visible": visibility}
                                                
# Phase 10A: "/" is now served by prisma_home_bp.landing (registered above).
# The legacy index() redirect is removed; flask_login redirects unauthenticated
# users to the login page automatically via LoginManager.login_view = "login".


@app.route('/login', methods=["GET", "POST"])
def login():
    # DEV preview: ?preview=1 ile login sayfasını DEV_MODE'da bile render et.
    is_preview = request.args.get("preview") == "1"
    # Phase 10A: login sonrası yeni PRISMA landing'e gidiyoruz (eski /home değil).
    if app.config.get("LOGIN_DISABLED") and not is_preview:
        return redirect(url_for('prisma_home.landing'))
    if current_user.is_authenticated and not is_preview:
        return redirect(url_for('prisma_home.landing'))
    form = LoginForm()
    if form.validate_on_submit():

        PARAMS = {"sicil": form.sicil.data}

        data = dc.get_data(
                base_prefix="ldap",
                dataset = "login",
                query = "./queries/LDAPBySicil.sql",
                query_params={"user_sicil": PARAMS["sicil"]}
            )

        u = {"name": data["NAME"].values[0],
             "sicil": data["SICIL"].values[0],
             "ip": data["IP"].values[0],
             "department": data["DEPARTMENT"].values[0],
             "password": data["PASSW"].values[0],
             "user_id": data["USER_ID"].values[0]}


        if not u:
            user = None
        else:
            user = User(u)

        if user is None or not user.check_password(form.password.data):
            return redirect(url_for('login'))
        login_user(user, remember=form.remember_me.data)
        return redirect(url_for('prisma_home.landing'))
    return render_template('login.html', title='Sign In', form=form)


@app.route('/logout')
def logout():
    logout_user()
    # Phase 10A: legacy `index` endpoint was replaced by prisma_home.landing.
    return redirect(url_for('prisma_home.landing'))


@app.route('/home')
@login_required
def home():
    # Phase 10A: legacy `/home` (templates/index.html with letter rail) is
    # superseded by the new PRISMA landing. Redirect preserves the `home`
    # endpoint name for templates/base.html links and `/home` bookmarks while
    # routing users to the new shell.
    return redirect(url_for('prisma_home.landing'))


if __name__ == '__main__':
    app.run(debug=DEV_MODE, host='0.0.0.0', port=8081)