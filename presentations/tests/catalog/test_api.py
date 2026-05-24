"""Phase 9.a — HTTP endpoint tests for the catalog API.

Exercises filter combinations, detail load, graph payload shape, and the
auth gate on user-scoped reads.
"""
from __future__ import annotations


def test_list_catalog_returns_corporate_tables(client):
    resp = client.get("/presentations/catalog")
    assert resp.status_code == 200
    data = resp.get_json()
    names = {t["name"] for t in data["tables"]}
    assert "DEPOSITS_DAILY" in names
    assert "DIM_BRANCH" in names
    assert data["total"] == len(data["tables"])


def test_list_catalog_facets_present(client):
    resp = client.get("/presentations/catalog")
    data = resp.get_json()
    assert "facets" in data
    assert "treasury" in data["facets"]["departments"]
    assert "branch" in data["facets"]["concepts"]
    assert data["facets"]["sources"]["corporate"] > 0


def test_list_catalog_filter_by_dept(client):
    resp = client.get("/presentations/catalog?dept=treasury")
    assert resp.status_code == 200
    data = resp.get_json()
    assert all(t["department"] == "treasury" for t in data["tables"])


def test_list_catalog_filter_by_concept(client):
    resp = client.get("/presentations/catalog?concept=branch")
    assert resp.status_code == 200
    data = resp.get_json()
    assert all("branch" in t["concepts_bound"] for t in data["tables"])


def test_list_catalog_search_q(client):
    resp = client.get("/presentations/catalog?q=branch")
    assert resp.status_code == 200
    data = resp.get_json()
    # All matches must include 'branch' in name/schema/description (case-insensitive).
    for t in data["tables"]:
        hay = f"{t['name']} {t['schema']} {t.get('description', '')}".lower()
        assert "branch" in hay


def test_list_catalog_filter_by_unknown_concept_returns_empty(client):
    resp = client.get("/presentations/catalog?concept=zzz_unknown")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["tables"] == []


def test_list_catalog_scope_corporate(client):
    resp = client.get("/presentations/catalog?scope=corporate")
    assert resp.status_code == 200
    data = resp.get_json()
    assert all(t["source"] == "corporate" for t in data["tables"])


def test_list_catalog_scope_user_empty_without_uploads(client):
    """No user uploads exist in 9.a fixtures → scope=user returns empty."""
    resp = client.get("/presentations/catalog?scope=user")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["tables"] == []


# ── Detail endpoint ───────────────────────────────────────────────────────


def test_table_detail_returns_full_shape(client):
    resp = client.get("/presentations/catalog/EDW/DEPOSITS_DAILY")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "DEPOSITS_DAILY"
    assert data["schema"] == "EDW"
    # Detail-only fields populated.
    assert isinstance(data["columns"], list)
    assert any(c["name"] == "BRANCH_CODE" for c in data["columns"])
    assert isinstance(data["lookups"], list)


def test_table_detail_404_for_missing_table(client):
    resp = client.get("/presentations/catalog/EDW/NOPE")
    assert resp.status_code == 404


def test_table_detail_403_for_other_users_upload(client):
    """User can't access another user's __user_<sicil>__ schema."""
    # The test user is A16438 (configured in conftest). Try someone else.
    resp = client.get("/presentations/catalog/__user_B99999__/u_anything")
    assert resp.status_code == 403


# ── Graph endpoint ────────────────────────────────────────────────────────


def test_graph_payload_shape(client):
    resp = client.get("/presentations/catalog/graph")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "nodes" in data and "edges" in data and "clusters" in data
    assert all("id" in n and "label" in n for n in data["nodes"])


def test_graph_includes_lookup_edge(client):
    """DEPOSITS_DAILY.BRANCH_CODE lookups to DIM_BRANCH — must appear."""
    resp = client.get("/presentations/catalog/graph")
    data = resp.get_json()
    lookups = [e for e in data["edges"] if e["kind"] == "lookup"]
    assert any(
        e["source"] == "EDW.DEPOSITS_DAILY" and e["target"] == "EDW.DIM_BRANCH"
        for e in lookups
    )


def test_graph_includes_shared_concept_edges(client):
    """Tables binding the same concept produce shared_concept edges."""
    resp = client.get("/presentations/catalog/graph")
    data = resp.get_json()
    sc = [e for e in data["edges"] if e["kind"] == "shared_concept"]
    # All fixture tables share at least one concept with at least one other.
    assert len(sc) >= 1
