"""validate_patch._check_sql_source_refs — LLM'in halüsine ettiği SCHEMA.TABLO
referansları validate aşamasında yakalanır (ORA-00942 retry döngüsü yerine
LLM'e 'mevcut tablolar şunlar' geri beslemesi)."""
from __future__ import annotations

import pytest
from flask import Flask

from presentations.nodes.validate_patch import (
    _check_sql_source_refs, _iter_patch_sqls, _known_table_universe,
)


class _Store:
    def __init__(self, tables):
        self._tables = tables

    def list_tables(self, schema=None):
        return list(self._tables)


class _State:
    def __init__(self, patches, scope_contract=None):
        self.pending_patches = patches
        self.manifest = {"blocks": []}
        self.scope_contract = scope_contract


@pytest.fixture
def app():
    a = Flask(__name__)
    a.config.update(TESTING=True,
                    TABLE_DOC_STORE=_Store([("EDW", "MYU_DAILY_RES"),
                                            ("EDW", "MYU_HIST_RES")]))
    return a


def _whole_block_patch(sql):
    return {"op": "add", "path": "/blocks/0/children/-", "value": {
        "id": "b_x", "type": "bar_chart", "title": "t",
        "data_source": {"original_sql": sql},
        "config": {"categories": [], "series": []}}}


def test_valid_catalog_ref_passes(app):
    with app.app_context():
        errs = _check_sql_source_refs(_State([
            _whole_block_patch("SELECT CCY_CODE, SUM(X) FROM EDW.MYU_DAILY_RES GROUP BY CCY_CODE")]))
    assert errs == []


def test_hallucinated_table_rejected_with_universe(app):
    with app.app_context():
        errs = _check_sql_source_refs(_State([
            _whole_block_patch("SELECT * FROM EDW.GHOST_TABLE")]))
    assert len(errs) == 1
    assert "EDW.GHOST_TABLE" in errs[0]
    assert "EDW.MYU_DAILY_RES" in errs[0]  # evren mesajda listelenir


def test_alias_only_from_is_not_checked(app):
    """Hazırlık view'ları / CTE'ler şema-niteliksizdir — asla bloklanmaz."""
    with app.app_context():
        errs = _check_sql_source_refs(_State([
            _whole_block_patch("WITH t AS (SELECT * FROM agg_ccy) SELECT * FROM t")]))
    assert errs == []


def test_empty_universe_skips_check():
    """Store yok + scope yok → evren bilinemez → hiçbir şey bloklanmaz."""
    a = Flask(__name__)
    a.config.update(TESTING=True)
    with a.app_context():
        errs = _check_sql_source_refs(_State([
            _whole_block_patch("SELECT * FROM EDW.GHOST_TABLE")]))
    assert errs == []


def test_subpath_original_sql_patch_checked(app):
    with app.app_context():
        errs = _check_sql_source_refs(_State([
            {"op": "replace",
             "path": "/blocks/0/children/1/data_source/original_sql",
             "value": "SELECT 1 FROM ODS.HAYALET"}]))
    assert len(errs) == 1 and "ODS.HAYALET" in errs[0]


def test_container_children_sqls_walked(app):
    with app.app_context():
        errs = _check_sql_source_refs(_State([
            {"op": "add", "path": "/blocks/-", "value": {
                "id": "sec", "type": "section_header", "title": "S",
                "children": [{
                    "id": "leaf", "type": "kpi", "title": "k",
                    "data_source": {"original_sql": "SELECT 1 FROM EDW.YOKBOYLE"},
                    "config": {}}]}}]))
    assert len(errs) == 1 and "EDW.YOKBOYLE" in errs[0]


def test_iter_patch_sqls_shapes():
    patches = [
        _whole_block_patch("SELECT 1 FROM A.B"),
        {"op": "replace", "path": "/blocks/0/children/0/data_source",
         "value": {"original_sql": "SELECT 2 FROM C.D"}},
        {"op": "replace", "path": "/blocks/0/children/0/title", "value": "x"},
    ]
    sqls = [s for _, s in _iter_patch_sqls(patches)]
    assert sqls == ["SELECT 1 FROM A.B", "SELECT 2 FROM C.D"]


def test_universe_includes_scope_table_refs():
    class _Ref:
        schema_name, name = "ODS_TREASURY", "TRD_POS"

    class _Item:
        table_ref = _Ref()

    class _Scope:
        basket = [_Item()]

    a = Flask(__name__)
    a.config.update(TESTING=True)
    with a.app_context():
        uni = _known_table_universe(_State([], scope_contract=_Scope()))
    assert uni == {"ODS_TREASURY.TRD_POS"}
