"""_fill_missing_sqls — statik (data_source'suz) MEVCUT blokta title/config
patch'i SQL sentezi TETİKLEMEZ (executor'daki statik-blok istisnasının
simetriği). Eskiden başlık değişimi bile mini-call koşturuyordu."""
from __future__ import annotations

from presentations.nodes.generate_patch import _fill_missing_sqls


class _BoomLLM:
    """Sentez çağrılırsa test patlasın."""
    endpoint = "http://boom.invalid"
    token = "x"

    def __getattr__(self, name):  # pragma: no cover
        raise AssertionError("statik blok için SQL sentezi çağrılmamalı")


class _State:
    def __init__(self, manifest, patches, user_message="başlığı değiştir"):
        self.manifest = manifest
        self.pending_patches = patches
        self.user_message = user_message
        self.scope_contract = None
        self.session = None


def _manifest_static_kpi():
    return {"blocks": [{
        "id": "sec", "type": "section_header", "title": "S", "config": {},
        "children": [{"id": "kpi_s", "type": "kpi", "title": "Eski",
                      "locked": False, "config": {"value": 1.0}}]}]}


def test_static_block_title_patch_does_not_synthesize_sql():
    st = _State(_manifest_static_kpi(), [
        {"op": "replace", "path": "/blocks/0/children/0/title", "value": "Yeni"}])
    out = _fill_missing_sqls(st, st.pending_patches, _BoomLLM(), None)
    # Ek SQL patch'i üretilmedi, orijinal patch aynen durdu.
    assert out == st.pending_patches


def test_static_block_config_patch_does_not_synthesize_sql():
    st = _State(_manifest_static_kpi(), [
        {"op": "replace", "path": "/blocks/0/children/0/config/value", "value": 9.0}])
    out = _fill_missing_sqls(st, st.pending_patches, _BoomLLM(), None)
    assert out == st.pending_patches
