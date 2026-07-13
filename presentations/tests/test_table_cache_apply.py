"""Oturum tablo-önbelleği — apply-filters Oracle'sız koşar.

basket[].duck_cache işaretli tablolar ilk kullanımda BİR KEZ Oracle'dan
oturum DuckDB'sine çekilir; blok SQL'leri (Oracle lehçesi dahil) lokalde
koşar. Filtre değişimi = sıfır Oracle turu.
"""
from __future__ import annotations

import pandas as pd
import pytest
from flask import Flask
from flask_login import LoginManager, login_user

from presentations import presentations_bp
from presentations.session import SessionRegistry


class _FakeUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    sicil = "A16438"
    name = "test"

    def get_id(self):
        return "A16438"


class _CountingDC:
    """get_data çağrılarını sayar: tablo yükleme vs blok sorgusu ayrımı
    dataset önekiyle yapılır."""

    def __init__(self):
        self.table_loads = 0
        self.block_queries = 0

    def get_data(self, dataset=None, query=None, query_params=None, **kw):
        if str(dataset).startswith("block::tcache/"):
            self.table_loads += 1
            return pd.DataFrame({
                "MONTH": pd.to_datetime(["2026-05-01", "2026-06-01"] * 2),
                "DIM_SEGMENT": ["BIREYSEL", "BIREYSEL", "OZEL", "OZEL"],
                "BALANCE": [100.0, 150.0, 200.0, 260.0],
                "WR_SUM": [40.0, 63.0, 84.0, 117.0],
            })
        self.block_queries += 1
        raise AssertionError(f"Oracle blok sorgusu beklenmiyordu: {dataset}")

    # SessionRegistry / manifest persist stub'ları
    def _upload_bytes(self, *a, **kw):
        pass

    def read_json(self, key):
        raise FileNotFoundError(key)


def _manifest():
    # Oracle lehçeli SQL (NVL + FROM DUAL) — çeviri yolunu da kanıtlar.
    sql = (
        "WITH f AS (SELECT * FROM S.PRISMA_T WHERE DIM_SEGMENT IN (:segment)),\n"
        "t1 AS (SELECT MAX(MONTH) m FROM f WHERE MONTH <= :donem_to)\n"
        "SELECT DIM_SEGMENT, ROUND(NVL(SUM(WR_SUM), 0)/NULLIF(SUM(BALANCE),0)*100, 2)\n"
        "FROM f, t1 WHERE f.MONTH = t1.m GROUP BY DIM_SEGMENT"
    )
    return {
        "id": "ptc", "version": 1,
        "meta": {"title": "T"},
        "basket": [{"table": "S.PRISMA_T", "alias": "prisma_t",
                    "column_concepts": {}, "duck_cache": True}],
        "filters": [
            {"id": "f_segment", "semantic_tag": "segment", "type": "enum_multi",
             "label": "Segment", "allowed_values": ["BIREYSEL", "OZEL"],
             "default": ["BIREYSEL", "OZEL"]},
            {"id": "f_donem", "semantic_tag": "as_of_time", "type": "date_range",
             "label": "Dönem", "default": {"from": "2026-05-01", "to": "2026-06-01"}},
        ],
        "blocks": [{
            "id": "sec", "type": "section_header", "title": "B", "config": {},
            "children": [{
                "id": "b_rate", "type": "bar_chart", "title": "Faiz",
                "query": sql,
                "variables": [
                    {"name": "segment", "semantic_tag": "segment",
                     "type": "enum_multi", "required": True,
                     "default": ["BIREYSEL", "OZEL"],
                     "allowed_values": ["BIREYSEL", "OZEL"]},
                    {"name": "donem", "semantic_tag": "as_of_time",
                     "type": "date_range", "required": True,
                     "default": {"from": "2026-05-01", "to": "2026-06-01"}},
                ],
                "variable_bindings": {
                    "segment": {"from_filter": "f_segment"},
                    "donem": {"from_filter": "f_donem"},
                },
                "config": {"categories": [], "series": []},
            }],
        }],
    }


@pytest.fixture
def app(tmp_path):
    dc = _CountingDC()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        DATA_CLIENT=dc,
        SESSION_REGISTRY=SessionRegistry(dc=dc, duck_base_dir=tmp_path / "duck"),
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
    with app.app_context():
        app.config["SESSION_REGISTRY"].get_or_create("A16438", "ptc") \
            .set_manifest(_manifest())
    app.config["_DC"] = dc
    return app


def test_duck_cache_serves_filters_without_oracle(app):
    dc = app.config["_DC"]
    client = app.test_client()

    # 1. uygulama: tablo BİR kez yüklenir, blok lokalde koşar.
    r1 = client.post("/presentations/ptc/apply-filters", json={
        "filter_state": {"f_segment": ["BIREYSEL", "OZEL"],
                         "f_donem": {"from": "2026-05-01", "to": "2026-06-01"}}})
    assert r1.status_code == 200, r1.get_data(as_text=True)
    b1 = {b["id"]: b for b in r1.get_json()["blocks"]}
    assert b1["b_rate"]["status"] == "refetched", b1
    assert b1["b_rate"]["row_count"] == 2
    assert dc.table_loads == 1
    assert dc.block_queries == 0

    # 2. uygulama FARKLI filtre değeriyle: cache miss ama tablo önbellekte —
    # Oracle'a HİÇ gidilmez.
    r2 = client.post("/presentations/ptc/apply-filters", json={
        "filter_state": {"f_segment": ["BIREYSEL"],
                         "f_donem": {"from": "2026-05-01", "to": "2026-06-01"}}})
    assert r2.status_code == 200
    b2 = {b["id"]: b for b in r2.get_json()["blocks"]}
    assert b2["b_rate"]["row_count"] == 1
    assert dc.table_loads == 1          # TTL içinde yeniden yüklenmedi
    assert dc.block_queries == 0        # blok sorgusu Oracle'a hiç gitmedi


def test_process_block_has_no_local_import_shadowing():
    """Regresyon (ofis hatası): _process_block içindeki geç
    `from ... import expand_binds`, adı fonksiyon-yereli yapıp DAHA ÖNCEKİ
    kullanımları (library-cache / concept-injection) UnboundLocalError'a
    düşürüyordu. Fonksiyon içinde import edilen HİÇBİR ad, import satırından
    önce kullanılmamalı."""
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "routes.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_process_block")

    imports: dict[str, int] = {}
    for n in ast.walk(fn):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                nm = a.asname or a.name.split(".")[0]
                imports[nm] = min(imports.get(nm, n.lineno), n.lineno)
    hazards = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id in imports and n.lineno < imports[n.id]:
                hazards.append((n.id, n.lineno, imports[n.id]))
    assert not hazards, (
        f"_process_block içinde yerel import gölgelemesi: {hazards} — "
        "adı route kapsamında import edip fonksiyon içindeki import'u kaldır.")
