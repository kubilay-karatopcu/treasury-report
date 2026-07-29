"""Production S3'e Mevduat Uzmanı (DEP) tanımını yazar.

Bu script yalnız uzman kaydını oluşturur; Oracle'a sorgu atmaz ve dashboard
manifestlerini değiştirmez. DEP uzmanının altındaki süreç bağlantıları doğrudan
``/mevduat-panel/`` sayfalarına gider; o panel production'da DataClient üzerinden
Oracle/EDW sorgularını çalıştırır.

ÇALIŞTIRMA (Spyder — argparse/args YOK):
    Aşağıdaki KONFİG bloğunu düzenle, sonra dosyayı doğrudan çalıştır (Run / F5).
    Terminalden de argümansız çalışır: ``python jobs/seed_mevduat_expert.py``

    - ``READ_DEPARTMENTS`` : uzmanı görebilecek departman(lar). BOŞ liste → herkes ("*").
    - ``EDIT_DEPARTMENTS`` : Atölye'de düzenleyebilecek departman(lar).
    - ``OVERWRITE``        : S3'te DEP zaten varsa üzerine yaz (güncelleme için True).

    Departman string'i LDAP'teki ``TRESUARY_LDAP.DEPARTMENT`` ile BİREBİR
    eşleşmeli (büyük/küçük harf ve Türkçe karakter dahil); erişim tam-string
    karşılaştırmasıdır.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

import yaml

# ``python jobs/...`` çalıştırıldığında import yolu jobs/ ile başlar; ortak
# DataClient ise repo kökündedir.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DataClient import DataClient


# ---------------------------------------------------------------------------
# KONFİG — Spyder'da args çalışmadığı için buradan düzenle (argparse yok).
# ---------------------------------------------------------------------------
#: Uzmanı GÖREBİLECEK departmanlar. Boş liste bırakırsan herkes görür (["*"]).
READ_DEPARTMENTS: list[str] = ["FİNANSAL YAPAY ZEKA UYGULAMALARI"]
#: Uzmanı ATÖLYE'de düzenleyebilecek departmanlar.
EDIT_DEPARTMENTS: list[str] = ["FİNANSAL YAPAY ZEKA UYGULAMALARI"]
#: S3'te DEP zaten varsa üzerine yaz (mevcut kaydı güncellemek için True).
OVERWRITE: bool = True
# ---------------------------------------------------------------------------


EXPERT_ID = "dep"
EXPERT_KEY = f"prisma-treasury/experts/{EXPERT_ID}.yaml"

# Süreç bağları dev_data/experts/dep.yaml ile birebir tutulur — kaynak orası,
# bu job onu production S3'e taşır. dep.yaml'a süreç eklenince buraya da ekle.
PROCESS_IDS = (
    "mevduat.maliyet",
    "mevduat.bakiye",
    "mevduat.vade",
    "mevduat.donusler",
    "mevduat.yeni_uretim",
    "mevduat.sektor",
    "mevduat.bsc",
    # Faz S2 — legacy statik dashboard'ların PRISMA-native karşılıkları
    # (docs/STATIK_DASHBOARD_ADAPTASYON.md).
    "mevduat.oranlar",
    "mevduat.miktarlar",
    "mevduat.tarihsel",
    "mevduat.rakip",
    # Faz S4 — Uygulamalar: analiz panosu değil, etkileşimli araçlar.
    "uygulamalar.panel_parametreler",
    "uygulamalar.panel_rezervasyon",
    "uygulamalar.asistan",
)


def _as_list(value: str | Sequence[str] | None) -> list[str]:
    """Departman girdisini güvenle listeye çevirir.

    Tek bir string verilirse tek elemanlı liste döner — ``list("ABC")``'nin
    string'i tek tek harflere ("A","B","C") parçalama tuzağını önler. Bu, KONFİG
    bloğuna yanlışlıkla liste yerine string yazılınca erişimin sessizce bozulmasını
    engeller (aksi halde access_scope harf listesi olur, hiç kimse eşleşmez).
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(v) for v in value]


def build_expert(*, read_departments: str | Sequence[str],
                 edit_departments: str | Sequence[str]) -> dict:
    """Build the S3ExpertStore-compatible DEP document."""
    return {
        "id": EXPERT_ID,
        "version": 3,
        "code": "DEP",
        "name": "Mevduat Uzmanı",
        "domain_label": "Mevduat & Fonlama",
        "short_description": (
            "TL mevduat stoku, fiyatlama ve dönüş dinamiklerini izler; "
            "mevduat panolarının sahibidir."
        ),
        "persona": {
            "system_prompt": (
                "Sen QNB Hazine'nin mevduat uzmanısın. Mevduat bakiyesi, "
                "fiyatlama, vade ve dönüş dinamiklerini veriye dayanarak "
                "açık, ihtiyatlı ve aksiyon odaklı yorumlarsın."
            ),
            "voice_examples": [],
        },
        "bound_content": {
            "blocks": [],
            "snapshots": [],
            "processes": list(PROCESS_IDS),
        },
        "briefing_recipe": {
            "cache_ttl_seconds": 1800,
            "sections": [],
        },
        "access_scope": {
            "read": _as_list(read_departments) or ["*"],
            "edit": _as_list(edit_departments),
        },
        "ui": {
            "accent_color": "#4A9B6E",
            "glyph": "DEP",
        },
        "status": "active",
    }


def main() -> int:
    """Argümansız çalışır — davranış yukarıdaki KONFİG bloğundan okunur."""
    expert = build_expert(
        read_departments=READ_DEPARTMENTS,
        edit_departments=EDIT_DEPARTMENTS,
    )
    body = yaml.safe_dump(
        expert, allow_unicode=True, sort_keys=False, default_flow_style=False,
    ).encode("utf-8")

    dc = DataClient()
    try:
        existing = dc.read_bytes(EXPERT_KEY)
    except Exception:
        existing = None

    if existing is not None and not OVERWRITE:
        print(
            f"Hata: {EXPERT_KEY} zaten var. Güncellemek için KONFİG'te OVERWRITE=True yap.",
            file=sys.stderr,
        )
        return 3

    dc._upload_bytes(EXPERT_KEY, body, content_type="application/x-yaml")
    saved = yaml.safe_load(dc.read_bytes(EXPERT_KEY).decode("utf-8"))
    if not isinstance(saved, dict) or saved.get("id") != EXPERT_ID:
        print("Hata: S3 yazımı sonrası DEP uzmanı doğrulanamadı.", file=sys.stderr)
        return 4
    if saved.get("bound_content", {}).get("processes") != list(PROCESS_IDS):
        print("Hata: S3 yazımı sonrası pano süreç bağları doğrulanamadı.", file=sys.stderr)
        return 5

    action = "güncellendi" if existing is not None else "oluşturuldu"
    print(
        f"OK: {EXPERT_KEY} {action}; {len(PROCESS_IDS)} süreç bağlı; "
        f"read={saved['access_scope']['read']}, edit={saved['access_scope']['edit']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
