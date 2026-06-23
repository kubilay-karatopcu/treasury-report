"""HTTP tests for the temporary scope endpoints (Phase 8.a, DoD #4).

Spins up a tiny Flask app with the blueprint, LOGIN_DISABLED, a fake user, a
filesystem scope store, and the *real* concept registry + table-doc catalog so
``examples/phase_8/sample_scope.yaml`` is validated against production-shaped
metadata.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry
from presentations.scope.schema import load_scope_yaml, scope_to_dict
from presentations.scope.store import LocalScopeStore
from presentations.table_docs.store import CachedTableDocStore, LocalTableDocStore

REPO_ROOT = Path(__file__).resolve().parents[3]
CONCEPTS_DIR = REPO_ROOT / "presentations" / "catalog" / "concepts"
TABLES_DIR = REPO_ROOT / "presentations" / "catalog" / "tables"
SAMPLE_SCOPE = REPO_ROOT / "examples" / "phase_8" / "sample_scope.yaml"


class _FakeUser(UserMixin):
    name = "kubilay"
    sicil = "A16438"
    department = "Treasury"

    def get_id(self):
        return self.sicil


@pytest.fixture
def app(tmp_path: Path):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test",
        TESTING=True,
        LOGIN_DISABLED=True,
        SCOPE_STORE=LocalScopeStore(tmp_path / "scopes"),
        CONCEPT_REGISTRY=ConceptRegistry.from_dir(CONCEPTS_DIR),
        TABLE_DOC_STORE=CachedTableDocStore(LocalTableDocStore(base_dir=TABLES_DIR)),
    )
    lm = LoginManager(app)

    @lm.user_loader
    def _load(_id):
        return _FakeUser()

    @app.before_request
    def _force_login():
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            login_user(_FakeUser())

    app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sample_body():
    # JSON-safe shape (ISO date strings) of the sample scope.
    return scope_to_dict(load_scope_yaml(SAMPLE_SCOPE.read_text(encoding="utf-8")))


def test_post_accepts_and_validates_sample(client, sample_body):
    resp = client.post("/presentations/p_abc123/scope", json=sample_body)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    assert data["version"] == 1
    assert data["presentation_id"] == "p_abc123"


def test_get_latest_after_save(client, sample_body):
    client.post("/presentations/p_abc123/scope", json=sample_body)
    resp = client.get("/presentations/p_abc123/scope")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["scope"]["presentation_id"] == "p_abc123"
    assert body["scope"]["version"] == 1


def test_get_specific_version(client, sample_body):
    client.post("/presentations/p_abc123/scope", json=sample_body)
    resp = client.get("/presentations/p_abc123/scope/1")
    assert resp.status_code == 200
    assert resp.get_json()["scope"]["version"] == 1


def test_post_bumps_version(client, sample_body):
    v1 = client.post("/presentations/p_abc123/scope", json=sample_body).get_json()["version"]
    v2 = client.post("/presentations/p_abc123/scope", json=sample_body).get_json()["version"]
    assert (v1, v2) == (1, 2)


def test_get_missing_returns_404(client):
    assert client.get("/presentations/p_nope/scope").status_code == 404


def test_get_missing_version_returns_404(client, sample_body):
    client.post("/presentations/p_abc123/scope", json=sample_body)
    assert client.get("/presentations/p_abc123/scope/9").status_code == 404


def test_invalid_scope_rejected(client, sample_body):
    bad = json.loads(json.dumps(sample_body))
    # Duplicate the basket alias → rule 1 error.
    bad["scope"]["basket"][1]["alias"] = bad["scope"]["basket"][0]["alias"]
    resp = client.post("/presentations/p_abc123/scope", json=bad)
    assert resp.status_code == 400
    errors = resp.get_json()["errors"]
    assert any("Duplicate basket alias" in e for e in errors)


def test_malformed_body_rejected(client):
    resp = client.post("/presentations/p_abc123/scope", json={"scope": {"version": "not-an-int"}})
    assert resp.status_code == 400


# ════════════════════════════════════════════════════════════════════════
# #10 — entitlement geçidi manuel-SQL sepet öğelerini de kapsar (bypass)
# ════════════════════════════════════════════════════════════════════════

from flask_login import login_user as _login_user

from presentations import routes_scope as _rs
from presentations.scope.schema import load_scope_from_dict as _load_scope

_ROUTING = {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}


# ── Saf yardımcı: FROM/JOIN bölgesinden SCHEMA.TABLE çıkarımı ────────────────

def test_sql_refs_simple_from():
    assert _rs._sql_schema_table_refs("SELECT * FROM EDW.DEPOSITS_DAILY") == {"EDW.DEPOSITS_DAILY"}


def test_sql_refs_cte_name_not_flagged():
    # WITH cte adı + SELECT'teki alias.kolon gated sayılmamalı (false-positive yok).
    sql = "WITH cte AS (SELECT 1 AS k) SELECT cte.k FROM cte"
    assert _rs._sql_schema_table_refs(sql) == set()


def test_sql_refs_subquery_alias_and_columns_not_flagged():
    sql = "SELECT a.BALANCE, b.RATE FROM EDW.A a JOIN EDW.B b ON a.k = b.k"
    assert _rs._sql_schema_table_refs(sql) == {"EDW.A", "EDW.B"}


def test_sql_refs_join_comma_subquery_cte_all_caught():
    # JOIN + virgül-join + CTE gövdesi + WHERE alt-sorgusu: hepsi yakalanmalı.
    sql = (
        "WITH t AS (SELECT * FROM EDW.A a JOIN ODS_RISK.SECRET b ON a.k=b.k) "
        "SELECT * FROM t c, EDW.F d WHERE d.x IN (SELECT id FROM HIDDEN.SUB s)"
    )
    assert _rs._sql_schema_table_refs(sql) == {"EDW.A", "ODS_RISK.SECRET", "EDW.F", "HIDDEN.SUB"}


def test_sql_refs_quoted_identifier():
    assert _rs._sql_schema_table_refs('SELECT * FROM "ODS_RISK"."EXPOSURE"') == {"ODS_RISK.EXPOSURE"}


def test_sql_refs_on_function_comma_not_flagged():
    # ON içindeki fonksiyon-çağrısı virgülü yeni tablo gibi toplanmamalı.
    sql = "SELECT * FROM EDW.A a JOIN EDW.B b ON COALESCE(a.k, b.k) = b.k"
    assert _rs._sql_schema_table_refs(sql) == {"EDW.A", "EDW.B"}


# ── Entitlement geçidi: manuel-SQL bypass ────────────────────────────────────

def _scope_with(items):
    return _load_scope({"scope": {
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-01-01T00:00:00Z", "basket": items,
    }})


def _denied_in_ctx(app, scope):
    with app.test_request_context():
        _login_user(_FakeUser())
        return _rs._unentitled_tables(scope)


def test_manual_sql_gated_schema_denied(app):
    # treasury kullanıcısı manuel-SQL ile ODS_RISK (risk) çekiyor → RED (bypass).
    scope = _scope_with([
        {"alias": "manual1", "sql": "SELECT * FROM ODS_RISK.EXPOSURE", "routing": _ROUTING},
    ])
    assert "ODS_RISK.EXPOSURE" in _denied_in_ctx(app, scope)


def test_manual_sql_entitled_schema_allowed(app):
    # Aynı kullanıcı kendi departmanının EDW şemasını manuel-SQL ile çekebilir.
    scope = _scope_with([
        {"alias": "manual1", "sql": "SELECT * FROM EDW.DEPOSITS_DAILY", "routing": _ROUTING},
    ])
    assert _denied_in_ctx(app, scope) == []


def test_manual_sql_join_onto_gated_caught(app):
    # Entitled bir tablodan gated bir şemaya JOIN da yakalanmalı.
    scope = _scope_with([
        {"alias": "manual1",
         "sql": "SELECT * FROM EDW.DEPOSITS_DAILY d JOIN ODS_RISK.EXPOSURE r ON d.k=r.k",
         "routing": _ROUTING},
    ])
    denied = _denied_in_ctx(app, scope)
    assert "ODS_RISK.EXPOSURE" in denied
    assert "EDW.DEPOSITS_DAILY" not in denied


def test_table_ref_item_still_gated(app):
    # table_ref öğeleri eskisi gibi geçide tabi (davranış korunur).
    scope = _scope_with([
        {"alias": "tbl1", "table_ref": {"schema": "ODS_RISK", "name": "EXPOSURE"},
         "projection": {"include_all": True}, "routing": _ROUTING},
    ])
    assert "ODS_RISK.EXPOSURE" in _denied_in_ctx(app, scope)
