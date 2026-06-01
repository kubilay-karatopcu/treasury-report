"""Regression: AppCatalog must resolve concept coverage from Phase 7 verified
bindings, not just the Phase 6.5.b suggested_semantic_tag.

The prod bug: ``table_binds_concept`` called a non-existent
``BindingCatalog.concepts_for_table``; the AttributeError was swallowed and the
method fell through to the tag, so a column bound to a concept via the
documentation UI (``CREATE_DT`` → ``as_of_time``, human_verified, no tag) was
reported "not bound" — the scope validator then warned that a working filter
had no effect.
"""
from __future__ import annotations

from presentations.concepts.bindings import BindingCatalog
from presentations.scope.catalog import AppCatalog


class _Col:
    def __init__(self, type=None, tag=None):
        self.type = type
        self.suggested_semantic_tag = tag


class _Doc:
    def __init__(self, schema, table, columns, partition_column=None, daily=None):
        self.schema_name = schema
        self.table = table
        self.columns = columns
        self.partition_column = partition_column
        self.estimated_daily_rows = daily


class _DocStore:
    def __init__(self, docs):
        self._docs = docs

    def load(self, schema, name):
        return self._docs.get((schema, name))


def _catalog() -> AppCatalog:
    bindings = BindingCatalog.from_dicts([{
        "schema": "ODS_TREASURY", "table": "STRATEGIC_DEP_PRCNG_CORE_RES",
        "concept_bindings": [
            {"concept": "as_of_time", "column": "CREATE_DT",
             "transform": {"kind": "identity"}, "confidence": "human_verified"},
            # llm_proposed → gated out of the compiler, so must read as unbound.
            {"concept": "segment", "column": "SEG",
             "transform": {"kind": "identity"}, "confidence": "llm_proposed"},
        ],
    }])
    docs = _DocStore({
        ("ODS_TREASURY", "STRATEGIC_DEP_PRCNG_CORE_RES"): _Doc(
            "ODS_TREASURY", "STRATEGIC_DEP_PRCNG_CORE_RES",
            {"CREATE_DT": _Col(type="DATE"), "SEG": _Col(type="VARCHAR")},
            partition_column="CREATE_DT", daily=1_000_000),
        # Legacy table: not in the binding catalog, carries a Phase 6.5.b tag.
        ("EDW", "LEGACY_TAGGED"): _Doc(
            "EDW", "LEGACY_TAGGED", {"D": _Col(type="DATE", tag="as_of_time")}),
    })
    return AppCatalog(table_doc_store=docs, concept_registry=None, binding_catalog=bindings)


def test_human_verified_binding_is_bound_without_tag():
    cat = _catalog()
    assert cat.table_binds_concept(
        "ODS_TREASURY", "STRATEGIC_DEP_PRCNG_CORE_RES", "as_of_time") is True


def test_unverified_binding_reads_as_not_bound():
    cat = _catalog()
    assert cat.table_binds_concept(
        "ODS_TREASURY", "STRATEGIC_DEP_PRCNG_CORE_RES", "segment") is False


def test_table_meta_overlays_verified_binding_concept():
    # Routing's partition estimate reads column_concept(partition_column); it
    # must see the bound concept so a date filter can shrink the table.
    cat = _catalog()
    tm = cat.table_meta("ODS_TREASURY", "STRATEGIC_DEP_PRCNG_CORE_RES")
    assert tm is not None
    assert tm.column_concept("CREATE_DT") == "as_of_time"


def test_legacy_tag_fallback_when_table_absent_from_bindings():
    cat = _catalog()
    assert cat.table_binds_concept("EDW", "LEGACY_TAGGED", "as_of_time") is True


def test_unknown_table_is_unverifiable():
    cat = _catalog()
    assert cat.table_binds_concept("X", "NOPE", "as_of_time") is None


def test_partition_filter_shrinks_estimate_via_binding_overlay():
    # ④ chain: the binding overlay makes CREATE_DT resolve to as_of_time, so a
    # date range on that concept collapses the row estimate (5 days * daily)
    # instead of the horizon fallback — which is what flips a lazy table to
    # cached. If the overlay regressed, partition_concept would be None and the
    # range would not shrink the estimate (the two would be equal).
    from presentations.scope.routing import estimate_post_scope_size
    from presentations.scope.schema import PinnedFilter, Projection

    cat = _catalog()
    tm = cat.table_meta("ODS_TREASURY", "STRATEGIC_DEP_PRCNG_CORE_RES")
    proj = Projection(columns=["CREATE_DT", "SEG"])
    no_filter = estimate_post_scope_size(tm, proj, [])
    rng = PinnedFilter.model_validate(
        {"id": "pf_d", "concept": "as_of_time", "op": "between",
         "from": "2025-12-01", "to": "2025-12-05"})
    with_filter = estimate_post_scope_size(tm, proj, [rng])
    assert with_filter < no_filter
