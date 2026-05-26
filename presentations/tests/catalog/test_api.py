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
    """Phase 9 UX revision: the 'sources' facet is now keyed by schema
    name (EDW, HIST, …) so the left rail can render schema-grouped
    chips. The legacy 'corporate'/'user_upload' tags are gone from the
    user-facing surface — schemas are the navigation primitive."""
    resp = client.get("/presentations/catalog")
    data = resp.get_json()
    assert "facets" in data
    assert "treasury" in data["facets"]["departments"]
    assert "branch" in data["facets"]["concepts"]
    # Source facet keyed by schema_name now.
    assert data["facets"]["sources"]["EDW"] > 0


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


def test_graph_payload_is_pure_bipartite(client):
    """The /catalog/graph emit is hub-and-spoke only — every edge is a
    table→concept ``binds``. Table-to-table edges (lookup, manual) are
    dropped from this surface because they'd just add noise; consumers
    that need FK semantics call the lower-level compute_edges directly.
    """
    resp = client.get("/presentations/catalog/graph")
    data = resp.get_json()
    kinds = {e["kind"] for e in data["edges"]}
    assert kinds == {"binds"}


def test_graph_emits_concept_hubs(client):
    """9.b.1 bipartite topology — concept hubs surface as their own
    nodes; tables connect to them via ``binds`` edges instead of
    forming N×N ``shared_concept`` mesh."""
    resp = client.get("/presentations/catalog/graph")
    data = resp.get_json()

    concept_nodes = [n for n in data["nodes"] if n["type"] == "concept"]
    assert len(concept_nodes) >= 1
    # Each concept hub carries usage_count = how many tables bind it.
    for c in concept_nodes:
        assert c["usage_count"] >= 1
        assert c["id"].startswith("concept:")

    bind_edges = [e for e in data["edges"] if e["kind"] == "binds"]
    assert len(bind_edges) >= 1
    # Bind edge targets are always concept-hub ids.
    for e in bind_edges:
        assert e["target"].startswith("concept:")

    # And we should NOT emit shared_concept edges anymore — the bipartite
    # topology expresses the same information structurally.
    assert not any(e["kind"] == "shared_concept" for e in data["edges"])


def test_graph_table_nodes_have_type_field(client):
    resp = client.get("/presentations/catalog/graph")
    data = resp.get_json()
    table_nodes = [n for n in data["nodes"] if n["type"] == "table"]
    assert len(table_nodes) >= 1
    # Tables carry department + source; concepts have those as None.
    for n in table_nodes:
        assert n["source"] in ("corporate", "user_upload")


# ── Concept-detail endpoint (Phase 9 UX revision) ────────────────────────


def test_concept_detail_returns_bound_tables(client):
    """Clicking a concept hub on the graph fetches its docs + the list
    of tables binding it. With no CONCEPT_REGISTRY wired in the test
    app, the endpoint synthesises a minimal record so the right panel
    still has something to show."""
    resp = client.get("/presentations/catalog/concept/branch")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["id"] == "branch"
    assert data["usage_count"] >= 1
    bound = data["bound_tables"]
    names = {t["name"] for t in bound}
    # branch is bound by DEPOSITS_DAILY, DEPOSITS_BY_BRANCH, DIM_BRANCH
    # in the EDW fixture set.
    assert names & {"DEPOSITS_DAILY", "DEPOSITS_BY_BRANCH", "DIM_BRANCH"}


def test_concept_detail_404_when_unknown_and_unbound(client):
    """A concept name that's neither in the registry nor bound by any
    catalog table should 404 — the right rail then shows an error
    state instead of pretending the concept exists."""
    resp = client.get("/presentations/catalog/concept/no_such_concept_xyz")
    assert resp.status_code == 404
