"""apply-filters — Hazırlık view'ı okuyan blokta SENTINEL'SİZ concept enjeksiyonu.

Kullanıcı raporu: filtreler otomatik geldi ama seçip güncelleyince grafikler
değişmiyor. Kök neden (view yolu): {{concept_filters}} sentinel'i olmayan
dataset_sql blokları koşulsuz "blind" işaretleniyor, predicate hiç
uygulanmıyordu. Artık column_concepts bir aktif filtreye bağlıysa predicate
WHERE'e AND'lenir (Oracle yolundaki apply_concepts_to_block ile aynı davranış)
ve fiilen koşan SQL data_source.executed_sql'de görünür.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import duckdb
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

import presentations
from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import CachedBindingCatalog


class _FakeUser(UserMixin):
    sicil = "A16438"

    def get_id(self):
        return self.sicil


class _NoOracleDC:
    """Bu yolda Oracle'a HİÇ gidilmemeli — çağrı olursa test düşer."""

    def get_data(self, *a, **kw):  # pragma: no cover - guard
        raise AssertionError("dataset_sql yolu Oracle'a gitmemeli")


class _StubSession:
    def __init__(self, manifest):
        self._m = manifest
        self._conn = duckdb.connect(":memory:")
        self._conn.execute(
            "CREATE VIEW seg_ozet AS SELECT * FROM (VALUES "
            "('RETAIL', 100.0), ('SME', 50.0), ('CORP', 70.0)"
            ") t(SEGMENT, TOTAL)"
        )

    def get_manifest(self):
        return self._m

    def set_manifest(self, m):
        self._m = m

    def get_duck_conn(self):
        return self._conn

    @contextmanager
    def duck_conn(self):
        yield self._conn


class _StubRegistry:
    def __init__(self, session):
        self._s = session

    def get_or_create(self, user, pid):
        return self._s


def _make_app(manifest):
    catalog_dir = Path(presentations.__file__).parent / "catalog"
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        SESSION_REGISTRY=_StubRegistry(_StubSession(manifest)),
        DATA_CLIENT=_NoOracleDC(),
        CONCEPT_REGISTRY=ConceptRegistry.from_dir(catalog_dir / "concepts"),
        CONCEPT_BINDING_CATALOG=CachedBindingCatalog(catalog_dir / "tables",
                                                     check_interval_s=0.0),
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

    if "presentations" not in app.blueprints:
        app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app


def _manifest():
    # Basket alias'lı türetilmiş view + kolonuna segment concept'i bağlı.
    # Blok SQL'i view'ı okur; sentinel YOK, variables YOK.
    return {
        "id": "p1", "version": 1,
        "basket": [{
            "table": "derived://seg_ozet", "alias": "seg_ozet",
            "column_concepts": {"SEGMENT": "segment"},
        }],
        "filters": [{"id": "f_segment", "semantic_tag": "segment",
                     "type": "enum_multi", "label": "Segment",
                     "allowed_values": ["RETAIL", "SME", "CORP", "PRIVATE"]}],
        "blocks": [{
            "id": "b_seg", "type": "bar_chart", "title": "Segment",
            "query": "SELECT SEGMENT, SUM(TOTAL) AS TOTAL FROM seg_ozet GROUP BY SEGMENT ORDER BY SEGMENT",
            "config": {"categories": [], "series": [{"name": "T", "values": []}]},
        }],
    }


def _post(client, filter_state):
    return client.post("/presentations/p1/apply-filters",
                       json={"filter_state": filter_state})


def test_no_sentinel_view_block_gets_filtered():
    client = _make_app(_manifest()).test_client()
    r = _post(client, {"f_segment": ["RETAIL", "SME"]})
    assert r.status_code == 200
    body = r.get_json()
    blk = next(b for b in body["blocks"] if b["id"] == "b_seg")
    assert blk["status"] == "dataset_sql"
    assert blk["concept_injected"] is True
    assert blk["applied_predicates"] == [{"concept": "segment"}]
    assert blk["blind_filters"] == []
    # CORP predicate dışında kaldı → 2 satır.
    assert blk["row_count"] == 2


def test_executed_sql_surfaces_injected_predicate():
    app = _make_app(_manifest())
    client = app.test_client()
    r = _post(client, {"f_segment": ["RETAIL"]})
    assert r.status_code == 200
    session = app.config["SESSION_REGISTRY"].get_or_create("A16438", "p1")
    block = session.get_manifest()["blocks"][0]
    ds = block["data_source"]
    # Şablon SQL değişmedi (çifte enjeksiyon yok)…
    assert "WHERE" not in ds["original_sql"].upper()
    # …ama fiilen koşan SQL predicate'i taşıyor ve kullanıcıya görünür.
    assert "SEGMENT" in ds["executed_sql"]
    assert "WHERE" in ds["executed_sql"].upper()
    assert ds["executed_params"]  # bind değerleri de saklanır
    assert block["config"]["categories"] == ["RETAIL"]


def test_unbound_filter_stays_blind():
    m = _manifest()
    m["basket"][0]["column_concepts"] = {}  # kolon bağlanmamış → blind
    client = _make_app(m).test_client()
    r = _post(client, {"f_segment": ["RETAIL"]})
    assert r.status_code == 200
    blk = next(b for b in r.get_json()["blocks"] if b["id"] == "b_seg")
    assert blk["status"] == "dataset_sql"
    assert blk["concept_injected"] is False
    assert blk["blind_filters"] == ["segment"]
    assert blk["row_count"] == 3  # filtre uygulanmadı, tüm satırlar
