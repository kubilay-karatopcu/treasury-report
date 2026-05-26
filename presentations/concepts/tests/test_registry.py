"""Phase 7.a — concept registry: precedence, collisions, resolution, cache."""
from __future__ import annotations

import time

import pytest
import yaml

from presentations.concepts.registry import ConceptRegistry, CachedConceptRegistry


def _file(scope: str, *concepts: dict) -> dict:
    return {"version": 1, "scope": scope, "concepts": list(concepts)}


# ── Basic index ────────────────────────────────────────────────────────────

def test_from_dicts_indexes_by_id():
    reg = ConceptRegistry.from_dicts([
        _file("global",
              {"id": "currency", "name": "c", "type": "enum"},
              {"id": "as_of_time", "name": "t", "type": "time"}),
    ])
    assert reg.has("currency")
    assert reg.get("as_of_time").type == "time"
    assert reg.all_ids() == {"currency", "as_of_time"}
    assert len(reg) == 2


def test_by_scope_filters():
    reg = ConceptRegistry.from_dicts([
        _file("global", {"id": "currency", "name": "c", "type": "enum"}),
        _file("dept:treasury", {"id": "maturity", "name": "m", "type": "bucket"}),
    ])
    assert [c.id for c in reg.by_scope("global")] == ["currency"]
    assert [c.id for c in reg.by_scope("dept:treasury")] == ["maturity"]


# ── Scope precedence (locked decision §10.5) ───────────────────────────────

def test_user_cannot_redefine_global():
    """A user-scoped concept colliding with a global id is dropped; global wins."""
    reg = ConceptRegistry.from_dicts([
        _file("global", {
            "id": "currency", "name": "Global Currency", "type": "enum",
            "canonical_values": [{"code": "USD"}],
        }),
        _file("user", {
            "id": "currency", "name": "Hijacked", "type": "enum",
            "canonical_values": [{"code": "BTC"}],
        }),
    ])
    c = reg.get("currency")
    assert c.name == "Global Currency"
    assert c.scope == "global"


def test_dept_cannot_redefine_global():
    reg = ConceptRegistry.from_dicts([
        _file("dept:treasury", {"id": "currency", "name": "Dept", "type": "enum"}),
        _file("global", {"id": "currency", "name": "Global", "type": "enum"}),
    ])
    assert reg.get("currency").name == "Global"  # precedence independent of file order


def test_same_scope_duplicate_id_raises():
    with pytest.raises(ValueError):
        ConceptRegistry.from_dicts([
            _file("global", {"id": "currency", "name": "a", "type": "enum"}),
            _file("global", {"id": "currency", "name": "b", "type": "enum"}),
        ])


# ── Value resolution through the registry ──────────────────────────────────

def test_registry_resolve_value():
    reg = ConceptRegistry.from_dicts([
        _file("global", {
            "id": "currency", "name": "c", "type": "enum",
            "canonical_values": [{"code": "USD", "aliases": ["US Dollar"]}],
        }),
    ])
    assert reg.resolve_value("currency", "US Dollar") == "USD"
    assert reg.resolve_value("currency", "ZZ") is None
    assert reg.resolve_value("nonexistent_concept", "x") is None


# ── from_dir + live data ───────────────────────────────────────────────────

def test_from_dir_loads_catalog_concepts():
    """The shipped catalog/concepts dir loads and resolves real values."""
    import presentations
    from pathlib import Path
    data_dir = Path(presentations.__file__).parent / "catalog" / "concepts"
    reg = ConceptRegistry.from_dir(data_dir)
    assert reg.has("currency")
    assert reg.has("maturity")
    # ON loads as the string "ON", not bool True (custom YAML loader).
    assert reg.get("maturity").get_value("ON") is not None


def test_from_dir_missing_dir_is_empty(tmp_path):
    reg = ConceptRegistry.from_dir(tmp_path / "does_not_exist")
    assert len(reg) == 0


# ── CachedConceptRegistry hot reload ───────────────────────────────────────

def test_cached_registry_reloads_on_change(tmp_path):
    f = tmp_path / "global.yaml"
    f.write_text(yaml.safe_dump(
        _file("global", {"id": "currency", "name": "v1", "type": "enum"})
    ), encoding="utf-8")

    cached = CachedConceptRegistry(tmp_path, check_interval_s=0.0)
    assert cached.get("currency").name == "v1"

    # Rewrite with a new concept; interval 0 → next read reloads.
    f.write_text(yaml.safe_dump(
        _file("global",
              {"id": "currency", "name": "v2", "type": "enum"},
              {"id": "branch", "name": "b", "type": "enum"})
    ), encoding="utf-8")
    # mtime resolution can be coarse; nudge to guarantee a signature change.
    time.sleep(0.01)
    assert cached.has("branch")
    assert cached.get("currency").name == "v2"


def test_cached_registry_force_reload(tmp_path):
    f = tmp_path / "global.yaml"
    f.write_text(yaml.safe_dump(
        _file("global", {"id": "currency", "name": "v1", "type": "enum"})
    ), encoding="utf-8")
    cached = CachedConceptRegistry(tmp_path, check_interval_s=999)
    assert cached.get("currency").name == "v1"
    f.write_text(yaml.safe_dump(
        _file("global", {"id": "currency", "name": "v2", "type": "enum"})
    ), encoding="utf-8")
    # Interval is huge, so a normal read would NOT reload — force it.
    cached.reload()
    assert cached.get("currency").name == "v2"
