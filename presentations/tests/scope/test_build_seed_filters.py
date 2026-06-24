"""Oturum 3.3 (C1) — build'de bağlı concept'leri dashboard filtresi olarak seed.

`_seed_concept_filters_at_build` manifest basket'inin table_ref human_verified
binding'lerini + column_concepts'i toplayıp dashboard `filters`'a EKLER (yalnız
ekler — mevcut/kullanıcı-düzenli filtreyi ezmez, idempotent).
"""
from __future__ import annotations

import pytest
from flask import Flask


class _Binding:
    def __init__(self, concept):
        self.concept = concept


class _Catalog:
    def __init__(self, mapping):
        self._m = mapping  # {(schema, table): [concept_id, ...]}

    def get_bindings(self, schema, table):
        return [_Binding(c) for c in self._m.get((schema, table), [])]


class _Concept:
    def __init__(self, cid):
        self.id = cid


class _Registry:
    def __init__(self, ids):
        self._ids = set(ids)

    @property
    def snapshot(self):
        return self

    def get(self, cid):
        return _Concept(cid) if cid in self._ids else None


@pytest.fixture
def app(monkeypatch):
    a = Flask(__name__)
    a.config["CONCEPT_REGISTRY"] = _Registry({"currency", "segment", "as_of_time"})
    a.config["CONCEPT_BINDING_CATALOG"] = _Catalog({
        ("EDW", "DEPOSITS"): ["currency", "segment"],
    })
    import presentations.concepts.user_scope as us
    monkeypatch.setattr(us, "build_effective_registry", lambda base, user: base)
    import presentations.routes as routes_mod
    monkeypatch.setattr(
        routes_mod, "_filter_proposal_from_concept",
        lambda c: {"id": "f_" + c.id, "semantic_tag": c.id, "type": "enum_multi",
                   "allowed_values": [], "default": [], "source": "concept"})
    return a


def test_seed_adds_all_bound_concepts(app):
    from presentations.routes_scope import _seed_concept_filters_at_build
    manifest = {
        "basket": [{"alias": "dep", "table": "EDW.DEPOSITS",
                    "column_concepts": {"X": "as_of_time"}}],
        "filters": [],
    }
    with app.app_context():
        added = _seed_concept_filters_at_build(manifest)
    tags = {f["semantic_tag"] for f in manifest["filters"]}
    assert tags == {"currency", "segment", "as_of_time"}  # table binds + column_concept
    assert added == 3


def test_seed_idempotent_and_preserves_user_filter(app):
    from presentations.routes_scope import _seed_concept_filters_at_build
    manifest = {
        "basket": [{"alias": "dep", "table": "EDW.DEPOSITS"}],
        # currency already on the dashboard with a user-narrowed default.
        "filters": [{"id": "f_currency", "semantic_tag": "currency",
                     "type": "enum_multi", "default": ["USD"]}],
    }
    with app.app_context():
        added = _seed_concept_filters_at_build(manifest)
    cur = [f for f in manifest["filters"] if f["semantic_tag"] == "currency"]
    assert len(cur) == 1 and cur[0]["default"] == ["USD"]   # not clobbered
    assert added == 1                                        # only segment added
    assert any(f["semantic_tag"] == "segment" for f in manifest["filters"])


def test_seed_noop_without_registry():
    from presentations.routes_scope import _seed_concept_filters_at_build
    a = Flask(__name__)  # no CONCEPT_REGISTRY / CONCEPT_BINDING_CATALOG
    manifest = {"basket": [{"alias": "dep", "table": "EDW.DEPOSITS"}], "filters": []}
    with a.app_context():
        added = _seed_concept_filters_at_build(manifest)
    assert added == 0 and manifest["filters"] == []
