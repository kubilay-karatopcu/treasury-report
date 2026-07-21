"""Production S3'e Mevduat Uzmanı (DEP) tanımını yazar.

Bu script yalnız uzman kaydını oluşturur; Oracle'a sorgu atmaz ve dashboard
manifestlerini değiştirmez. DEP uzmanının altındaki süreç bağlantıları doğrudan
``/mevduat-panel/`` sayfalarına gider; o panel production'da DataClient üzerinden
Oracle/EDW sorgularını çalıştırır.

Ofis/production ortamında örnek kullanım::

    python jobs/seed_mevduat_expert.py --edit-department HAZINE

Mevcut DEP kaydını bilinçli olarak güncellemek için::

    python jobs/seed_mevduat_expert.py --edit-department HAZINE --overwrite
"""
from __future__ import annotations

import argparse
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


EXPERT_ID = "dep"
EXPERT_KEY = f"prisma-treasury/experts/{EXPERT_ID}.yaml"

PROCESS_IDS = (
    "mevduat.maliyet",
    "mevduat.bakiye",
    "mevduat.vade",
    "mevduat.donusler",
    "mevduat.yeni_uretim",
    "mevduat.sektor",
    "mevduat.bsc",
)


def build_expert(*, read_departments: Sequence[str], edit_departments: Sequence[str]) -> dict:
    """Build the S3ExpertStore-compatible DEP document."""
    return {
        "id": EXPERT_ID,
        "version": 1,
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
            "read": list(read_departments) or ["*"],
            "edit": list(edit_departments),
        },
        "ui": {
            "accent_color": "#4A9B6E",
            "glyph": "DEP",
        },
        "status": "active",
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--edit-department", action="append", default=[], metavar="DEPARTMENT",
        help="Uzmanı Atölye'de düzenleyebilecek departman. Birden çok kez verilebilir.",
    )
    parser.add_argument(
        "--read-department", action="append", default=[], metavar="DEPARTMENT",
        help="Uzmanı görebilecek departman. Verilmezse tüm kullanıcılar görebilir.",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="S3'teki mevcut DEP uzmanını bu tanımla değiştir.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Yazmadan önce üretilecek YAML'ı ekrana bas.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.edit_department:
        print("Hata: en az bir --edit-department vermelisin.", file=sys.stderr)
        return 2

    expert = build_expert(
        read_departments=args.read_department,
        edit_departments=args.edit_department,
    )
    body = yaml.safe_dump(
        expert, allow_unicode=True, sort_keys=False, default_flow_style=False,
    ).encode("utf-8")

    if args.dry_run:
        print(body.decode("utf-8"))
        return 0

    dc = DataClient()
    try:
        existing = dc.read_bytes(EXPERT_KEY)
    except Exception:
        existing = None

    if existing is not None and not args.overwrite:
        print(
            f"Hata: {EXPERT_KEY} zaten var. Bilinçli güncelleme için --overwrite kullan.",
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
    print(f"OK: {EXPERT_KEY} {action}; {len(PROCESS_IDS)} mevduat süreci bağlı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
