"""Phase 9.b.1 — Graph payload cache: hit/miss + content-hash invalidation."""
from __future__ import annotations

from presentations.catalog.api import (
    _build_graph_payload,
    _catalog_content_hash,
    _get_cached_graph_payload,
)


def test_graph_cache_hits_repeat_calls(flask_app, loader):
    """Two calls with the same loader + sicil return the SAME serialized
    dict (identity-equal), proving the cache served the second call."""
    with flask_app.app_context():
        a = _get_cached_graph_payload(loader, sicil="A16438", refresh=False)
        b = _get_cached_graph_payload(loader, sicil="A16438", refresh=False)
    assert a is b


def test_graph_cache_invalidates_on_refresh(flask_app, loader):
    """?refresh=true bypasses the cache and rebuilds."""
    with flask_app.app_context():
        a = _get_cached_graph_payload(loader, sicil="A16438", refresh=False)
        b = _get_cached_graph_payload(loader, sicil="A16438", refresh=True)
    # New dict object (rebuilt). Content equal because catalog didn't change.
    assert a is not b
    assert a == b


def test_graph_cache_invalidates_on_content_change(flask_app, loader, fixture_store):
    """Removing a table file changes the content hash → new payload built."""
    with flask_app.app_context():
        first = _get_cached_graph_payload(loader, sicil="A16438", refresh=False)
        first_node_count = len(first["nodes"])
        # Mutate the underlying store and invalidate the loader cache.
        (fixture_store.base_dir / "EDW" / "NII_MONTHLY.yaml").unlink()
        loader.invalidate()
        second = _get_cached_graph_payload(loader, sicil="A16438", refresh=False)
    assert len(second["nodes"]) == first_node_count - 1


def test_content_hash_stable_for_unchanged_catalog(flask_app, loader):
    """Same loader + same data → same hash. Determinism check."""
    with flask_app.app_context():
        h1 = _catalog_content_hash(loader, sicil="A16438")
        h2 = _catalog_content_hash(loader, sicil="A16438")
    assert h1 == h2


def test_content_hash_differs_per_user(flask_app, loader, fake_dc):
    """Two users with different upload sets must produce different hashes
    (so each gets their own cache slot)."""
    import yaml as _yaml
    upload_doc = {
        "table": "u_only_for_A",
        "schema": "__user_A16438__",
        "description": "test upload",
        "columns": {
            "branch_id": {
                "type": "VARCHAR",
                "filterable": True,
                "filter_role": "dimension",
                "concept": "branch",
            },
        },
    }
    fake_dc._upload_bytes(
        "uploads/A16438/u_only_for_A/doc.yaml",
        _yaml.safe_dump(upload_doc).encode("utf-8"),
    )
    fake_dc._upload_bytes(
        "uploads/A16438/u_only_for_A/meta.yaml",
        _yaml.safe_dump({"upload": {"id": "u_only_for_A", "uploaded_at": "2026-05-01T10:00:00Z"}}).encode("utf-8"),
    )
    loader.invalidate()
    with flask_app.app_context():
        h_a = _catalog_content_hash(loader, sicil="A16438")
        h_b = _catalog_content_hash(loader, sicil="B99999")
    assert h_a != h_b


def test_endpoint_serves_cached_payload(client):
    """Sanity: /catalog/graph still returns the §2.4 shape after the
    cache wrap (regression guard)."""
    r = client.get("/presentations/catalog/graph")
    assert r.status_code == 200
    data = r.get_json()
    assert "nodes" in data and "edges" in data and "clusters" in data


def test_endpoint_refresh_param_works(client):
    """?refresh=1 doesn't change the shape and doesn't error."""
    r = client.get("/presentations/catalog/graph?refresh=1")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data["nodes"], list)
