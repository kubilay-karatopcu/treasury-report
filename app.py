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
from deposit_panel import deposit_panel_bp, init_app as deposit_panel_init
from presentations import presentations_bp
from presentations.session import SessionRegistry
from presentations.store import S3SnapshotStore
from presentations.blocks.store import S3BlockStore, LocalBlockStore
from presentations.table_docs.store import (
    S3TableDocStore, LocalTableDocStore, CachedTableDocStore,
)
from presentations.concepts.registry import CachedConceptRegistry
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
    import duckdb as _duckdb

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
                conn = _duckdb.connect(":memory:")
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


class DataOperations:
    
    def __init__(self, dc):
        
        self.dc = dc
        self.final_Df = None

    def process_data(self):

        # MYU
        try:
            myu_df = self.dc.get_data(base_prefix="bsp", dataset="raw/input_data/myu_sql", query="./queries/myu.sql")    
        except:
            myu_df = self.dc.get_data(base_prefix="bsp", dataset="raw/input_data/myu_sql", query="./queries/myu_T_CUST.sql")    
            
        myu_df = myu_df.loc[:, ~myu_df.columns.duplicated()].reset_index(drop=True)

        today = datetime.now()

        def get_custom_part_id(date_obj):
            _, last_day_of_month = calendar.monthrange(date_obj.year, date_obj.month)
            if date_obj.day == last_day_of_month:
                return date_obj.strftime("%m%Y")
            else:
                return date_obj.day * 10
            
        val_dt = today.strftime("%d/%m/%Y")
        part_id = get_custom_part_id(today)
        part_id_t_1 = get_custom_part_id(today - timedelta(days=1))

        try:
            core_comparison_df = self.dc.get_data(
                base_prefix="bsp",
                dataset = "raw/input_data/core_comparison",
                query = "./queries/core_comparison.sql",
                query_params={"part_id": part_id, "part_id_t_1": part_id_t_1, "val_dt":val_dt, "mtrty_dt": val_dt}
            )
        except:
            core_comparison_df = self.dc.get_data(
                base_prefix="bsp",
                dataset = "raw/input_data/core_comparison",
                query = "./queries/core_comparison_T_CUST.sql",
                query_params={"part_id": part_id, "part_id_t_1": part_id_t_1, "val_dt":val_dt, "mtrty_dt": val_dt}
            )

        core_comparison_df = core_comparison_df.loc[:, ~core_comparison_df.columns.duplicated()]

        myu_df = pd.concat([myu_df, core_comparison_df], ignore_index=True)

        myu_df['DATE_TIME'] = pd.to_datetime(
            myu_df['CREATE_DT'].astype(str) + ' ' + myu_df['CREATE_TM'].astype(str).str.zfill(6),
            errors='coerce'
        )
        myu_df['DATA_SRC'] = 'MYU'
        # TREASURY
        try:
            treasury_df = self.dc.get_data(base_prefix="bsp", dataset="raw/input_data/treasury", query="./queries/treasury.sql")   
        except:
            treasury_df = self.dc.get_data(base_prefix="bsp", dataset="raw/input_data/treasury", query="./queries/treasury_T_CUST.sql")   

        treasury_df.rename(columns={
            'RQSTD_INTRST_RT': 'DEMANDED_RATE',
            'RECMMND_INTRST_RT': 'SUGGESTED_PRICE',
            'APPRVD_INTRST_RT': 'OFFERED_RATE',
            'CURRENCY_CD': 'CCY_CODE',
            'MTRTY_STRT': 'VADE_BASLANGIC',
            'MTRTY_END': 'VADE_BITIS',
            'RSRVTN_DT': 'CREATE_DT',
            'PRCNG_CNT': 'TALEP_REVIZE_NO'
        }, inplace=True)

        treasury_df['DATA_SRC'] = 'TREASURY'

        treasury_df['DATE_TIME'] = pd.to_datetime(
            treasury_df['CREATE_TM'].astype(str).str[:14],
            format='%Y%m%d%H%M%S',
            errors='coerce'
        )

        treasury_df = treasury_df.loc[:, ~treasury_df.columns.duplicated()].reset_index(drop=True)

        self.final_df = pd.concat([myu_df, treasury_df], ignore_index=True)
        self.final_df.sort_values(by=['DATE_TIME'], inplace=True)
        self.final_df.reset_index(drop=True, inplace=True)

        self.final_df['IS_MAX_REVIZE'] = False
        self.final_df['TALEP_REVIZE_NO'] = self.final_df['TALEP_REVIZE_NO'].fillna(1)

        if not self.final_df.empty:
            group_cols = ["DATA_SRC", "CUST_ID", "CREATE_DT", "VADE_BASLANGIC"]
            max_revize_indices = self.final_df.groupby(group_cols)["TALEP_REVIZE_NO"].idxmax()
            self.final_df.loc[max_revize_indices, 'IS_MAX_REVIZE'] = True

        # CLEANING
        self.final_df['OFFERED_RATE'] = pd.to_numeric(self.final_df['OFFERED_RATE'], errors='coerce')
        self.final_df = self.final_df[self.final_df['RESERVATION_AMT'] >= 50000].copy()
        self.final_df = self.final_df[self.final_df['OFFERED_RATE'] <= (self.final_df['MARKET_MAX_RT'] * 1.02)].copy()
        self.clear_outliers(source_col='COMPETITOR_BANK_RTS', target_col='PERCENTILE_COMPETITOR_RTS')
        self.clear_outliers(source_col='DEMANDED_RATE', target_col='PERCENTILE_DEMANDED_RTS')
        self.final_df.dropna(subset=['DATE_TIME'], inplace=True)

        # JSON PREP
        self.final_df['CREATE_DT'] = pd.to_datetime(self.final_df['CREATE_DT'])
        self.final_df['DATE_STR_CLEAN'] = self.final_df['CREATE_DT'].dt.strftime('%Y-%m-%d')
        self.final_df['DATE_TIME_STR'] = self.final_df['DATE_TIME'].dt.strftime('%Y-%m-%d %H:%M:%S')

        logging.info("Data operations are done...")
        return self.final_df

    def clear_outliers(self, source_col: str, target_col: str = None, q: float = 0.99):
        if target_col is None:
            target_col = source_col

        self.final_df[source_col] = pd.to_numeric(self.final_df[source_col], errors='coerce')

        p_limit = self.final_df[source_col].quantile(q)

        self.final_df[target_col] = np.where(
            self.final_df[source_col] <= p_limit,
            self.final_df[source_col],
            np.nan
        )

def load_competitor_data():
    """Load competitor analysis data from Oracle and pre-process ranges."""
    try:
        comp_df = dc.get_data(
            base_prefix="bsp",
            dataset="raw/input_data/competitor_analysis",
            query="./queries/competitor_analysis.sql"
        )
    except Exception as e:
        logging.exception(f"Failed to load competitor analysis data {e}")
        comp_df = pd.DataFrame(columns=[
            "TARIH", "VADE", "TUTAR", "FAIZ",
            "DOVIZ_CINSI", "KAYNAK", "URUN", "BANKA_ADI"
        ])
 
    if comp_df.empty:
        return comp_df
 
    # Parse VADE range → VADE_MIN, VADE_MAX  (e.g. "32-45 Gün" → 32, 45)
    def parse_range(val):
        if pd.isna(val):
            return 0, 0
        s = str(val)
        nums = re.findall(r'[\d]+(?:[.,]\d+)?', s.replace('.', '').replace(',', '.'))
        nums = [float(n) for n in nums]
        if len(nums) >= 2:
            return int(min(nums)), int(max(nums))
        elif len(nums) == 1:
            return int(nums[0]), int(nums[0])
        return 0, 0
 
    comp_df[['VADE_MIN', 'VADE_MAX']] = comp_df['VADE'].apply(
        lambda x: pd.Series(parse_range(x))
    )
    comp_df[['TUTAR_MIN', 'TUTAR_MAX']] = comp_df['TUTAR'].apply(
        lambda x: pd.Series(parse_range(x))
    )
 
    # Normalize date
    comp_df['TARIH'] = pd.to_datetime(comp_df['TARIH'], errors='coerce')
    comp_df['DATE_STR'] = comp_df['TARIH'].dt.strftime('%Y-%m-%d')
    comp_df['FAIZ'] = pd.to_numeric(comp_df['FAIZ'], errors='coerce')
    comp_df.dropna(subset=['TARIH', 'FAIZ'], inplace=True)
 
    return comp_df

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
    from presentations.store import LocalSnapshotStore

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

    app.config["SNAPSHOT_STORE"] = LocalSnapshotStore(base_dir=_DUCK_BASE_DIR / "snapshots")
    app.config["BLOCK_STORE"]    = LocalBlockStore(base_dir=_DUCK_BASE_DIR / "v2_blocks")
    app.config["TABLE_DOC_STORE"] = CachedTableDocStore(
        LocalTableDocStore(base_dir=Path(__file__).parent / "examples" / "table_docs")
    )
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
    app.config["SNAPSHOT_STORE"] = S3SnapshotStore(dc=dc)
    app.config["BLOCK_STORE"]    = S3BlockStore(dc=dc)
    app.config["TABLE_DOC_STORE"] = CachedTableDocStore(S3TableDocStore(dc=dc))

 
app.config["S3_GET"]    = _s3_get
app.config["S3_PUT"]    = _s3_put
app.config["S3_DELETE"] = _s3_delete


# ── Phase 7.a — concept registry ────────────────────────────────
# Hand-authored knowledge docs live under presentations/catalog/ (spec §3.1:
# system/dept concepts are git-versioned). concepts/ holds the registry;
# tables/ holds per-table concept bindings (read by the 7.b compiler). Same
# path in DEV and prod for parity. The cached registry hot-reloads on YAML
# mtime change so the data team can edit without a restart.
_CONCEPT_DIR = Path(__file__).parent / "presentations" / "catalog" / "concepts"
concept_registry = CachedConceptRegistry(_CONCEPT_DIR)
app.config["CONCEPT_REGISTRY"] = concept_registry
# Back the semantic-tag allow-list (block validation + UI dropdown) with the
# registry; SEMANTIC_TAGS_V0 stays as the baseline floor (zero regression).
set_active_registry(concept_registry)
logging.info("CONCEPT_REGISTRY loaded: %d concepts from %s",
             len(concept_registry), _CONCEPT_DIR)


data_ops = DataOperations(dc)
data_lock = threading.Lock()

if DEV_MODE:
    current_df = pd.DataFrame()
    competitor_df = pd.DataFrame()
else:
    current_df = data_ops.process_data()
    competitor_df = load_competitor_data()

last_refresh_at = datetime.now()

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

def get_current_df_copy():
    return current_df

def get_competitor_df_copy():
    return competitor_df

def refresh_current_df():
    global current_df, last_refresh_at, competitor_df
    new_df = data_ops.process_data()
    new_df.flags.writeable = False
    new_competitor_df = load_competitor_data()
    with data_lock:
        current_df = new_df
        last_refresh_at = datetime.now()
        competitor_df = new_competitor_df
        
    return len(new_df)
 

deposit_panel_init(dc, get_current_df_copy)
app.register_blueprint(deposit_panel_bp, url_prefix="/deposit-panel")
app.register_blueprint(deposit_bp, url_prefix="/deposit-assistant")
app.register_blueprint(presentations_bp, url_prefix="/presentations")

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
    # Mevduat Verileri
    "rates_page":           "mevduat.gunluk",
    "amounts_page":         "mevduat.gunluk",
    "api_oranlar_data":     "mevduat.gunluk",
    "api_miktarlar_data":   "mevduat.gunluk",
    "historic_page":        "mevduat.tarihsel",
    # Sektör Verileri
    "competitor_page":      "sektor.rakip",
    "competitor_summary":   "sektor.rakip",
    # Uygulamalar
    "deposit.chat":         "uygulamalar.asistan",
    # Deposit Panel (blueprint endpoint'leri "deposit_panel.xxx" formatında)
    "deposit_panel.params":         "uygulamalar.panel",
    "deposit_panel.reservations":   "uygulamalar.panel",
    "deposit_panel.api_get_params":       "uygulamalar.panel",
    "deposit_panel.api_set_params":       "uygulamalar.panel",
    "deposit_panel.api_get_hyperparams":  "uygulamalar.panel",
    "deposit_panel.api_set_hyperparams":  "uygulamalar.panel",
    "deposit_panel.api_get_today_data":   "uygulamalar.panel",
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
                                                
@app.route('/')
def index():
    if app.config.get("LOGIN_DISABLED"):
        return redirect(url_for('home'))
    return redirect(url_for('login'))


@app.route('/login', methods=["GET", "POST"])
def login():
    # DEV preview: ?preview=1 ile login sayfasını DEV_MODE'da bile render et.
    is_preview = request.args.get("preview") == "1"
    if app.config.get("LOGIN_DISABLED") and not is_preview:
        return redirect(url_for('.home'))
    if current_user.is_authenticated and not is_preview:
        return redirect(url_for('.home', sicil=current_user.sicil))
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
        return redirect(url_for('.home', sicil=user.sicil))
    return render_template('login.html', title='Sign In', form=form)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/home')
@login_required
def home():
    if app.config.get("LOGIN_DISABLED"):
        username = "Test User"
    else:
        username = current_user.name
    words = username.split()
    result = []

    for word in words:
        lower_word = word.replace('I', 'ı').replace('İ', 'i').lower()

        first_letter = lower_word[0]
        if first_letter == 'i':
            first_letter = 'İ'
        elif first_letter == 'ı':
            first_letter = 'I'
        else:
            first_letter = first_letter.upper()

        result.append(first_letter + lower_word[1:])

    username = " ".join(result)

    return render_template('index.html', username=username)


@app.route('/oranlar', methods=['GET'])
@login_required
def rates_page():
    df = get_current_df_copy()
    available_dates = sorted(df['DATE_STR_CLEAN'].unique().tolist()) if not df.empty else []
    latest_date = available_dates[-1] if available_dates else None

    return render_template(
        'rates.html',
        initial_date=latest_date,
        available_dates=available_dates
    )


@app.route('/miktarlar', methods=['GET'])
@login_required
def amounts_page():
    df = get_current_df_copy()
    available_dates = sorted(df['DATE_STR_CLEAN'].unique().tolist()) if not df.empty else []
    latest_date = available_dates[-1] if available_dates else None

    return render_template(
        'amounts.html',
        initial_date=latest_date,
        available_dates=available_dates
    )

@app.route('/api/data/oranlar/<date_str>', methods=['GET'])
@login_required
def api_oranlar_data(date_str):
    df = get_current_df_copy()
    if df.empty or 'DATE_STR_CLEAN' not in df.columns:
        return jsonify([])
    
    cols_to_send = [
        'DATE_TIME_STR', 'DATE_STR_CLEAN', 'DATA_SRC', 'CUST_TP', 'VADE_BASLANGIC',
        'CCY_CODE', 'CURRENTAMOUNT', 'INCOMING_AMT', 'RESERVATION_AMT',
        'TALEP_REVIZE_NO', 'IS_MAX_REVIZE', 'PERCENTILE_COMPETITOR_RTS',
        'OFFERED_RATE', 'PERCENTILE_DEMANDED_RTS', 'MARKET_MAX_RT', 'EKSTREM_YETKI', 'EKSTREM'
    ]
    valid_cols = [c for c in cols_to_send if c in df.columns]
    
    df_filtered = df[df['DATE_STR_CLEAN'] == date_str][valid_cols]
    
    json_data = df_filtered.to_json(orient='records', date_format='iso')
    return Response(json_data, mimetype='application/json')

@app.route('/api/data/miktarlar/<date_str>', methods=['GET'])
@login_required
def api_miktarlar_data(date_str):
    df = get_current_df_copy()
    if df.empty or 'DATE_STR_CLEAN' not in df.columns:
        return jsonify([])
    
    cols_to_send = [
        'DATE_TIME_STR', 'DATA_SRC', 'CUST_TP', 'VADE_BASLANGIC', 'CCY_CODE',
        'RESERVATION_AMT', 'TALEP_REVIZE_NO', 'IS_MAX_REVIZE', 'CURRENTAMOUNT',
        'INCOMING_AMT', 'PORTFOLIO_AMT', 'DATE_STR_CLEAN'
    ]
    valid_cols = [c for c in cols_to_send if c in df.columns]
    
    df_filtered = df[df['DATE_STR_CLEAN'] == date_str][valid_cols]
    
    json_data = df_filtered.to_json(orient='records', date_format='iso')
    return Response(json_data, mimetype='application/json')


@app.route('/historic')
@login_required
def historic_page():
    df = get_current_df_copy()
    available_dates = sorted(df['DATE_STR_CLEAN'].unique().tolist()) if not df.empty else []

    cols_to_send = [
        'DATE_TIME_STR', 'DATA_SRC', 'CUST_TP', 'VADE_BASLANGIC', 'CCY_CODE',
        'RESERVATION_AMT', 'TALEP_REVIZE_NO', 'IS_MAX_REVIZE',
        'PERCENTILE_COMPETITOR_RTS', 'OFFERED_RATE', 'PERCENTILE_DEMANDED_RTS',
        'MARKET_MAX_RT', 'CURRENTAMOUNT', 'INCOMING_AMT',
        'EKSTREM_YETKI'
    ]

    cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    raw_data = df[df['DATE_STR_CLEAN'] >= cutoff][cols_to_send].fillna(0).to_dict(orient='records')

    return render_template(
        'historic.html',
        raw_data=json.dumps(raw_data),
        available_dates=available_dates
    )


@app.route('/competitor')
@login_required
def competitor_page():
    
    df = get_competitor_df_copy()
 
    banks = sorted(df['BANKA_ADI'].dropna().unique().tolist()) if not df.empty else []
 
    # Include KAYNAK so JS can build source links
    cols = ['DATE_STR', 'VADE', 'VADE_MIN', 'VADE_MAX',
            'TUTAR', 'TUTAR_MIN', 'TUTAR_MAX',
            'FAIZ', 'DOVIZ_CINSI', 'BANKA_ADI', 'KAYNAK']
    valid_cols = [c for c in cols if c in df.columns]
 
    raw_data = df[valid_cols].fillna(0).to_dict(orient='records')
 
    return render_template(
        'competitor.html',
        raw_data=json.dumps(raw_data, default=str),
        banks=banks
    )
 
 
@app.route('/competitor/summary', methods=['POST'])
@login_required
def competitor_summary():
    """Call Qwen LLM to generate a market summary from structured rate data."""
    payload = request.get_json(silent=True)
    if not payload or 'data' not in payload:
        return jsonify({"summary": "Veri sağlanamadı."}), 400
 
    rate_data = payload['data']
 
    system_prompt = (
        "Sen bir Türk bankacılık sektörü analistisin. "
        "Sana JSON formatında rakip bankaların mevduat faiz oranları verilecek. "
        "Her banka için bugünkü (T) ve dünkü (T-1) maksimum faiz oranları ve değişim gösterilmiştir. "
        "Bu veriye bakarak 3-4 cümlelik kısa, profesyonel bir Türkçe piyasa özeti yaz. "
        "Hangi bankalar oran yükseltmiş, hangileri düşürmüş, genel trend ne yöne gidiyor belirt. "
        "Spesifik oran rakamlarını ve banka isimlerini kullan. "
        "Cevabında sadece Türkçe özet metni olsun, başka bir şey yazma."
    )
 
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(rate_data, ensure_ascii=False)}
    ]
 
    try:
        resp = requests.post(
            LLM_API_URL,
            json={
                "temperature": 0.1,
                "max_tokens": 512,
                "messages": messages
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LLM_API_KEY}"
            },
            verify=False,
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        summary = result["choices"][0]["message"]["content"]
    except Exception as e:
        logging.exception(f"LLM summary call failed {e}")
        summary = "Piyasa özeti şu anda oluşturulamıyor."
 
    return jsonify({"summary": summary})



@app.route('/internal/refresh-data', methods=['POST'])
def refresh_data_route():
    token = request.headers.get("X-Refresh-Token")
    if not REFRESH_TOKEN or token != REFRESH_TOKEN:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    try:
        row_count = refresh_current_df()
        return jsonify({
            "status": "success",
            "row_count": row_count,
            "last_refresh_at": last_refresh_at.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        logging.exception("Data refresh failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/internal/data-status', methods=['GET'])
def data_status():
    token = request.headers.get("X-Refresh-Token")
    if not REFRESH_TOKEN or token != REFRESH_TOKEN:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    with data_lock:
        row_count = 0 if current_df is None else len(current_df)
        refreshed = last_refresh_at.strftime("%Y-%m-%d %H:%M:%S") if last_refresh_at else None

    return jsonify({
        "status": "success",
        "row_count": row_count,
        "last_refresh_at": refreshed
    })



if __name__ == '__main__':
    app.run(debug=DEV_MODE, host='0.0.0.0', port=8081)