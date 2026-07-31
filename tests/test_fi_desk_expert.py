# -*- coding: utf-8 -*-
"""FI uzmanı — dev_data/experts/fi.yaml ↔ jobs/seed_fi_expert.py paritesi.

dep uzmanındaki sözleşmenin aynısı: kaynak kopya dev YAML'dır, seed job'u
onu production S3'e taşır; ikisi birebir tutulur. Süreç id'leri
prisma_home.processes.PROCESS_REGISTRY'de tanımlı olmalı (kırık bağ masada
boş kart üretir).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

FI_YAML = yaml.safe_load((REPO / "dev_data" / "experts" / "fi.yaml")
                         .read_text("utf-8"))


def _load_seed():
    spec = importlib.util.spec_from_file_location(
        "seed_fi_expert_job", REPO / "jobs" / "seed_fi_expert.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # DataClient import'u main() içinde — güvenli
    return mod


def test_yaml_structure():
    assert FI_YAML["id"] == "fi" and FI_YAML["status"] == "active"
    assert FI_YAML["bound_content"]["processes"] == [
        "uygulamalar.fi_veri_girisi", "uygulamalar.fi_islemler"]
    assert [a["id"] for a in FI_YAML["applications"]] == [
        "uygulamalar.fi_veri_girisi", "uygulamalar.fi_islemler"]
    # Panolar snapshot ile bağlanır — süreç klasörü bilinçli boş
    for view in FI_YAML["department_views"]:
        assert view["topics"] == []


def test_seed_matches_yaml():
    mod = _load_seed()
    expert = mod.build_expert(
        read_departments=["FİNANSAL YAPAY ZEKA UYGULAMALARI"],
        edit_departments=["FİNANSAL YAPAY ZEKA UYGULAMALARI"],
        view_departments=["FİNANSAL YAPAY ZEKA UYGULAMALARI"],
    )
    assert expert["id"] == FI_YAML["id"]
    assert expert["code"] == FI_YAML["code"]
    assert expert["bound_content"]["processes"] == \
        FI_YAML["bound_content"]["processes"]
    assert expert["applications"] == FI_YAML["applications"]
    assert expert["ui"] == FI_YAML["ui"]
    assert expert["department_views"][0]["topics"] == []
    assert expert["department_views"][0]["label"] == \
        FI_YAML["department_views"][0]["label"]


def test_process_ids_registered():
    flask = pytest.importorskip("flask")  # noqa: F841 — processes flask ister
    from prisma_home.processes import PROCESS_REGISTRY

    for pid in FI_YAML["bound_content"]["processes"]:
        assert pid in PROCESS_REGISTRY, pid
        assert PROCESS_REGISTRY[pid]["config_flag"] == "FI_DESK_ENABLED"


class TestMasaLandingFlatList:
    """Masa modunda landing TEK DÜZ 'Masalar' listesi (kullanıcı kararı
    2026-07-31): hero + 'Diğer Masalar' ayrımı yok, yetkili olunan tüm
    masalar (dep + fi) aynı ızgarada. Uzman (Atölye) modu eski tasarımda."""

    def test_masa_mode_single_flat_section(self, auth_client, flask_app):
        flask_app.config["PRISMA_MASA_MODE"] = True
        try:
            body = auth_client.get("/").data.decode("utf-8")
        finally:
            flask_app.config["PRISMA_MASA_MODE"] = False
        assert "Diğer Masalar" not in body
        # CSS tanımı style bloğunda durur; hero ELEMENTİ çizilmemeli
        assert '<a class="expert-featured-c"' not in body
        assert ">Masalar<" in body
        assert 'data-domain="dep"' in body
        assert 'data-domain="fi"' in body

    def test_uzman_mode_keeps_featured_layout(self, auth_client, flask_app):
        flask_app.config["PRISMA_MASA_MODE"] = False
        body = auth_client.get("/").data.decode("utf-8")
        assert "expert-featured" in body
        assert "Uzmanların" in body


def test_dashboards_job_binds_fi_expert():
    src = (REPO / "jobs" / "fi_desk_dashboards.py").read_text("utf-8")
    assert 'EXPERTS: list[str] | str = ["fi"]' in src
    assert "PUBLISH_SNAPSHOT = True" in src
    assert "def publish_snapshot" in src
