"""B (#2) — picking a concept on a filterable column in table docs persists a
Phase 7 human_verified binding (identity), so the filter compiler actually
applies it. A tag alone wouldn't reach the compiler.
"""
from __future__ import annotations

import pytest
from flask import Flask
from flask_login import LoginManager

from presentations.concepts.bindings import CachedBindingCatalog
from presentations.routes_library import _sync_column_bindings


class _Reg:
    """Registry stub — every concept is considered defined."""
    def has(self, _c):
        return True


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(
        CONCEPT_BINDING_CATALOG=CachedBindingCatalog(tmp_path / "bindings"),
        CONCEPT_REGISTRY=_Reg(),
    )
    lm = LoginManager(app)  # current_user → anonymous (verified_by = "unknown")

    @lm.user_loader
    def _load(_id):
        return None

    return app


def test_sync_writes_only_filterable_with_concept(app):
    with app.test_request_context():
        _sync_column_bindings("ODS_TREASURY", "RES", [
            {"name": "create_dt", "filterable": True, "concept": "as_of_time"},
            {"name": "NAME", "filterable": True, "concept": ""},         # no concept
            {"name": "AMT", "filterable": False, "concept": "segment"},  # not filterable
        ])
    bc = app.config["CONCEPT_BINDING_CATALOG"]
    bc.reload()
    bindings = bc.get_bindings("ODS_TREASURY", "RES")
    assert len(bindings) == 1
    b = bindings[0]
    assert b.column == "CREATE_DT"          # upper-cased
    assert b.concept == "as_of_time"
    assert b.confidence == "human_verified"
    assert getattr(b.transform, "kind", None) == "identity"


def test_sync_writes_chosen_transform(app):
    with app.test_request_context():
        _sync_column_bindings("ODS_TREASURY", "RES", [
            {"name": "CCY", "filterable": True, "concept": "currency",
             "transform_kind": "map", "tp_pairs": "TL = TRY\nDolar = USD"}])
    bc = app.config["CONCEPT_BINDING_CATALOG"]
    bc.reload()
    bindings = bc.get_bindings("ODS_TREASURY", "RES")
    assert len(bindings) == 1
    assert bindings[0].transform.kind == "map"
    assert bindings[0].transform.pairs == {"TL": "TRY", "Dolar": "USD"}


def test_sync_skips_incomplete_map_with_warning(app):
    with app.test_request_context():
        warns = _sync_column_bindings("ODS_TREASURY", "RES", [
            {"name": "CCY", "filterable": True, "concept": "currency",
             "transform_kind": "map", "tp_pairs": ""}])  # map needs ≥1 pair
    assert warns and "CCY" in warns[0]
    bc = app.config["CONCEPT_BINDING_CATALOG"]
    bc.reload()
    assert len(bc.get_bindings("ODS_TREASURY", "RES")) == 0


def test_sync_clears_binding_when_concept_removed(app):
    bc = app.config["CONCEPT_BINDING_CATALOG"]
    with app.test_request_context():
        _sync_column_bindings("ODS_TREASURY", "RES", [
            {"name": "CREATE_DT", "filterable": True, "concept": "as_of_time"}])
    bc.reload()
    assert len(bc.get_bindings("ODS_TREASURY", "RES")) == 1
    # Re-save without the concept → the binding is dropped.
    with app.test_request_context():
        _sync_column_bindings("ODS_TREASURY", "RES", [
            {"name": "CREATE_DT", "filterable": True, "concept": ""}])
    bc.reload()
    assert len(bc.get_bindings("ODS_TREASURY", "RES")) == 0
