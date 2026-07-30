# -*- coding: utf-8 -*-
"""FI masası veri giriş modülü (Muhabir Bankacılık ve Yapılandırılmış Fonlama).

Faz 0: yalnız alan matrisi (``field_matrix.json``) burada yaşar; Oracle
şeması ``jobs/fi_desk_schema.py`` ile kurulur. Tasarım: docs/FI_DESK_SPEC.md.
Faz 1'de Flask blueprint'i (veri giriş formu) bu pakete eklenecek.
"""
from __future__ import annotations

import json
from pathlib import Path

_MATRIX_PATH = Path(__file__).resolve().parent / "field_matrix.json"


def load_field_matrix() -> dict:
    """Alan matrisini oku (tek otorite: fi_desk/field_matrix.json)."""
    with open(_MATRIX_PATH, encoding="utf-8") as f:
        return json.load(f)
