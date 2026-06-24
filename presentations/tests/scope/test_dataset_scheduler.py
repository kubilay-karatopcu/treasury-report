"""Faz A — dataset scheduler: due-logic + dedup'd cron materialisation."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd

from presentations.cache.dataset_scheduler import DatasetScheduler, _dataset_due
from presentations.scope.materialize import read_dataset
from presentations.scope.schema import load_scope_from_dict


class _SyncDispatcher:
    """Runs the fetch closure inline so the test is deterministic."""

    def __init__(self):
        self.runs: list[str] = []

    def enqueue(self, *, cache_key, fetch, on_success=None, on_error=None):
        try:
            result = fetch()
            if on_success:
                on_success(result)
            self.runs.append(cache_key)
            return True
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(exc)
            return False


class _FakeDC:
    def __init__(self, df):
        self.objects: dict[str, bytes] = {}
        self._df = df

    def _upload_bytes(self, key, data, content_type=None, *, if_none_match=False):
        self.objects[key] = bytes(data)

    def read_bytes(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def read_json(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return json.loads(self.objects[key].decode("utf-8"))

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
        return self._df.copy()


class _FakeScopeStore:
    def __init__(self, scopes):
        self._scopes = scopes  # {pid: ScopeContract}

    def list_presentations(self):
        return sorted(self._scopes)

    def load_latest(self, pid):
        return self._scopes.get(pid)


def _scope(pid, *, refresh, routing="cached"):
    item = {
        "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
        "alias": "positions",
        "projection": {"columns": ["CCY", "NET_POSITION"], "include_all": False},
        "routing": {"decision": routing, "estimated_bytes": 1000},
    }
    if refresh is not None:
        item["refresh"] = refresh
    return load_scope_from_dict({
        "presentation_id": pid, "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [item], "filters": {"pinned": [], "interactive": []},
    })


# ── _dataset_due ────────────────────────────────────────────────────────────

class _Rp:
    def __init__(self, interval=None, schedule=None):
        self.interval_seconds = interval
        self.schedule = schedule


def test_due_interval():
    now = datetime(2026, 6, 15, 12, 0, 0)
    assert _dataset_due(_Rp(interval=600), None, now=now) is True
    assert _dataset_due(_Rp(interval=600), now - timedelta(seconds=601), now=now) is True
    assert _dataset_due(_Rp(interval=600), now - timedelta(seconds=300), now=now) is False


# ── Scheduler tick ──────────────────────────────────────────────────────────

def test_tick_materialises_due_dataset_once():
    df = pd.DataFrame({"CCY": ["TRY", "USD"], "NET_POSITION": [1.0, 2.0]})
    dc = _FakeDC(df)
    store = _FakeScopeStore({"p1": _scope("p1", refresh={"kind": "scheduled", "interval_seconds": 600})})
    disp = _SyncDispatcher()
    sched = DatasetScheduler(scope_store=store, data_client=dc, dispatcher=disp)

    # First tick → never materialised → due → 1 materialisation.
    assert sched.tick() == 1
    assert disp.runs == ["dataset:p1:positions"]
    got = read_dataset(dc, "p1", "positions")
    assert got is not None and got[0].shape[0] == 2

    # Second tick immediately → fresh (age ~0 < 600s) → not due → 0.
    assert sched.tick() == 0


def test_tick_skips_non_scheduled_and_lazy():
    df = pd.DataFrame({"CCY": ["TRY"]})
    dc = _FakeDC(df)
    store = _FakeScopeStore({
        "p_manual": _scope("p_manual", refresh={"kind": "manual"}),   # not scheduled
        "p_none": _scope("p_none", refresh=None),                      # no refresh
    })
    sched = DatasetScheduler(scope_store=store, data_client=dc, dispatcher=_SyncDispatcher())
    assert sched.tick() == 0


def test_two_charts_one_dataset_one_fetch():
    # The dedup win: a scope has ONE dataset; many charts reference it. The
    # scheduler materialises the dataset ONCE per tick regardless of chart count.
    df = pd.DataFrame({"A": [1], "B": [2], "C": [3]})
    dc = _FakeDC(df)
    store = _FakeScopeStore({"p1": _scope("p1", refresh={"kind": "scheduled", "interval_seconds": 600})})
    disp = _SyncDispatcher()
    sched = DatasetScheduler(scope_store=store, data_client=dc, dispatcher=disp)
    sched.tick()
    # Exactly one Oracle materialisation for the single dataset.
    assert len(disp.runs) == 1


def test_tick_materialises_scheduled_derived():
    # A derived (aggregate) table with a scheduled refresh is now cron-able: the
    # tick materialises it (pulling its source in-memory once), persisting the
    # small aggregate result. Same N-charts → one-fetch dedup, for aggregates.
    df = pd.DataFrame({"CCY": ["TRY", "TRY", "USD"], "TOTAL": [1.0, 2.0, 5.0]})
    dc = _FakeDC(df)
    src = {"sql": "SELECT ccy, total FROM big", "alias": "src",
           "routing": {"decision": "cached", "estimated_bytes": 1000}}
    der = {
        "derivation": {"kind": "aggregate", "source_alias": "src",
                       "group_by": ["CCY"],
                       "measures": [{"column": "TOTAL", "fn": "sum", "as": "TOTAL_SUM"}]},
        "alias": "agg",
        "projection": {"columns": ["CCY", "TOTAL_SUM"], "include_all": False},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0},
        "refresh": {"kind": "scheduled", "interval_seconds": 600},
    }
    scope = load_scope_from_dict({
        "presentation_id": "pderv", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [src, der], "filters": {"pinned": [], "interactive": []},
    })
    disp = _SyncDispatcher()
    sched = DatasetScheduler(scope_store=_FakeScopeStore({"pderv": scope}),
                             data_client=dc, dispatcher=disp)
    # Only the derived item is scheduled (source is manual) → exactly 1 enqueue.
    assert sched.tick() == 1
    assert disp.runs == ["dataset:pderv:agg"]
    rdf, _ = read_dataset(dc, "pderv", "agg")
    assert {r.CCY: r.TOTAL_SUM for r in rdf.itertuples()} == {"TRY": 3.0, "USD": 5.0}
