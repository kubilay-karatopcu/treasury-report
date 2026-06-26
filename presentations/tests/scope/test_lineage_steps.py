"""Oturum 3.4 (C3) — türetilmiş node'un üretim adımları (lineage SQL).

`_scope_lineage_steps` scope basket'inin türetme DAG'ını leaf→root yürür ve her
node için (yan etkisiz) compile_* ile SQL üretir. "Show steps" paneli bunu
gösterir (ara CTE/türetme zinciri görünür).
"""
from __future__ import annotations

from presentations.routes_scope import _scope_lineage_steps
from presentations.scope.schema import load_scope_from_dict


def _scope():
    return load_scope_from_dict({
        "presentation_id": "p_steps", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-24T00:00:00Z",
        "basket": [
            {"alias": "reservations",
             "table_ref": {"schema": "EDW", "name": "RESERVATIONS"},
             "projection": {"columns": ["CREATE_TM", "OFFERED_RATE"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}},
            {"alias": "combo_daily",
             "derivation": {"kind": "aggregate", "source_alias": "reservations",
                            "group_by": ["CREATE_TM"],
                            "measures": [{"column": "OFFERED_RATE", "fn": "avg",
                                          "as": "DAILY_AVG"}]},
             "projection": {"columns": ["CREATE_TM", "DAILY_AVG"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}},
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []}, "joins": [],
    })


def test_lineage_leaf_first_order():
    steps = _scope_lineage_steps(_scope(), "combo_daily")
    assert [s["alias"] for s in steps] == ["reservations", "combo_daily"]  # leaf→root
    by = {s["alias"]: s for s in steps}
    assert by["reservations"]["kind"] == "table"
    assert "RESERVATIONS" in by["reservations"]["sql"].upper()
    assert by["combo_daily"]["kind"] == "aggregate"
    assert by["combo_daily"]["sources"] == ["reservations"]
    assert "GROUP BY" in by["combo_daily"]["sql"].upper()


def test_lineage_base_table_single_step():
    steps = _scope_lineage_steps(_scope(), "reservations")
    assert [s["alias"] for s in steps] == ["reservations"]


def test_lineage_missing_alias_is_empty():
    assert _scope_lineage_steps(_scope(), "nope") == []


# ── C3 (N2) — bloğun kendi SQL'indeki CTE'leri adım olarak çıkar ──────────────

def test_extract_cte_steps_basic():
    from presentations.routes_scope import _extract_cte_steps
    sql = (
        "WITH daily AS (SELECT CREATE_TM, SUM(N) AS cnt FROM reservations GROUP BY CREATE_TM),\n"
        "     cum AS (SELECT CREATE_TM, SUM(cnt) OVER (ORDER BY CREATE_TM) AS c FROM daily)\n"
        "SELECT * FROM cum"
    )
    steps = _extract_cte_steps(sql)
    assert [s["alias"] for s in steps] == ["daily", "cum"]       # iki ara adım görünür
    assert all(s["kind"] == "cte" for s in steps)
    assert "GROUP BY" in steps[0]["sql"].upper()
    assert "OVER" in steps[1]["sql"].upper()                     # iç içe parantez doğru tarandı


def test_extract_cte_steps_none_without_with():
    from presentations.routes_scope import _extract_cte_steps
    assert _extract_cte_steps("SELECT * FROM t") == []
    assert _extract_cte_steps("") == []
    assert _extract_cte_steps("   with_table AS x") == []        # 'WITH ' değil
