# -*- coding: utf-8 -*-
"""seed_fi_expert.py — FI Masası uzmanını (FI) production S3'e yazar.

OFİSTE tek koşu (Spyder → Run/F5; argparse YOK, davranış KONFİG
sabitlerinden). Kaynak kopya ``dev_data/experts/fi.yaml`` ile birebir
tutulur — süreç/uygulama bağı eklenince İKİSİNİ DE güncelle.

Masa yayın modeli (deposits ile aynı, docs/FI_DESK_SPEC.md §Masa):
  * ERİŞİM uzmandadır: ``access_scope.read`` departmanları uzmanı (ve
    altındaki her şeyi) görür; uygulama başına ek departman kısıtı
    ``applications[].departments`` ile verilir.
  * Uygulamalar (veri girişi + işlemler) masada ayrı bölümde çıkar.
  * Analiz panosu SÜREÇ değildir: ``jobs/fi_desk_dashboards.py``
    p_fi_desk'i snapshot'layıp bu uzmana bağlar (bound_experts=fi) —
    uzman sayfasındaki pano ızgarasında uzmanı gören herkese açılır.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# KONFİG — Spyder'da args çalışmadığı için buradan düzenle (argparse yok).
# ---------------------------------------------------------------------------
#: Uzmanı GÖREBİLECEK departmanlar. Boş liste → herkes (["*"]).
#: FI masasının LDAP departman adı netleşince buraya ekle (örn.
#: "MUHABİR BANKACILIK VE YAPILANDIRILMIŞ FONLAMA" — birebir eşleşme!).
READ_DEPARTMENTS: list[str] = ["FİNANSAL YAPAY ZEKA UYGULAMALARI"]
#: Uzmanı ATÖLYE'de düzenleyebilecek departmanlar.
EDIT_DEPARTMENTS: list[str] = ["FİNANSAL YAPAY ZEKA UYGULAMALARI"]
#: Masa bakışını görecek departmanlar. Boş → READ_DEPARTMENTS kullanılır.
VIEW_DEPARTMENTS: list[str] = []
#: S3'te FI zaten varsa üzerine yaz.
OVERWRITE: bool = True
# ---------------------------------------------------------------------------

EXPERT_ID = "fi"
EXPERT_KEY = f"prisma-treasury/experts/{EXPERT_ID}.yaml"

#: dev_data/experts/fi.yaml ile birebir. FI'ın masa süreci yok — panolar
#: snapshot olarak bağlanır; buradaki iki id uygulamadır (Uygulamalar bölümü).
PROCESS_IDS = (
    "uygulamalar.fi_veri_girisi",
    "uygulamalar.fi_islemler",
)

#: (uygulama id, o uygulamayı görebilecek departmanlar). BOŞ tuple = uzmanı
#: görebilen herkes. Dolu ise birebir string eşleşmesi ("*" joker YOK).
APPLICATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("uygulamalar.fi_veri_girisi", ()),
    ("uygulamalar.fi_islemler", ()),
)

BRIEFING_FOCUS = (
    "Fonlama masası bakışı: teklif hunisini, kazanım oranlarını, lender ve "
    "ürün bazlı maliyet farklarını ve vade/itfa profilini aksiyon odaklı "
    "özetle."
)


def _as_list(value: str | Sequence[str] | None) -> list[str]:
    """String-güvenli liste çevirici (list("ABC") harf-parçalama tuzağı yok)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(v) for v in value]


def build_department_views(view_departments: str | Sequence[str]) -> list[dict]:
    """FI'da süreç klasörü yok (topics boş) — bakış yalnız brifing odağını ve
    görünürlüğü taşır; panolar snapshot ızgarasından, araçlar Uygulamalar
    bölümünden gelir."""
    depts = _as_list(view_departments)
    if not depts:
        return []
    return [{
        "departments": depts,
        "label": "FI Bakışı",
        "briefing_focus": BRIEFING_FOCUS,
        "topics": [],
    }]


def build_expert(*, read_departments: str | Sequence[str],
                 edit_departments: str | Sequence[str],
                 view_departments: str | Sequence[str] = ()) -> dict:
    """S3ExpertStore-uyumlu FI dokümanı."""
    return {
        "id": EXPERT_ID,
        "version": 1,
        "code": "FI",
        "name": "FI Masası Uzmanı",
        "domain_label": "Muhabir Bankacılık & Yapılandırılmış Fonlama",
        "short_description": (
            "Toptan fonlama işlemlerini (trade loan, sendikasyon, MTN, "
            "tahvil) izler; FI masası veri girişi ve işlem panosunun "
            "sahibidir."
        ),
        "persona": {
            "system_prompt": (
                "Sen QNB Hazine'nin muhabir bankacılık ve yapılandırılmış "
                "fonlama uzmanısın. Borçlanma tekliflerini, kazanım/kayıp "
                "dinamiklerini ve fonlama maliyetlerini veriye dayanarak "
                "açık ve aksiyon odaklı yorumlarsın."
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
            "accent_color": "#B58A3C",
            "glyph": "FI",
        },
        "status": "active",
        "department_views": build_department_views(view_departments),
        "applications": [{"id": pid, "departments": list(depts)}
                         for pid, depts in APPLICATIONS],
    }


def main() -> int:
    """Argümansız çalışır — davranış yukarıdaki KONFİG bloğundan okunur."""
    from DataClient import DataClient

    expert = build_expert(
        read_departments=READ_DEPARTMENTS,
        edit_departments=EDIT_DEPARTMENTS,
        view_departments=_as_list(VIEW_DEPARTMENTS) or READ_DEPARTMENTS,
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
        print(f"Hata: {EXPERT_KEY} zaten var. Güncellemek için OVERWRITE=True yap.",
              file=sys.stderr)
        return 3

    dc._upload_bytes(EXPERT_KEY, body, content_type="application/x-yaml")
    saved = yaml.safe_load(dc.read_bytes(EXPERT_KEY).decode("utf-8"))
    if not isinstance(saved, dict) or saved.get("id") != EXPERT_ID:
        print("Hata: S3 yazımı sonrası FI uzmanı doğrulanamadı.", file=sys.stderr)
        return 4
    if saved.get("bound_content", {}).get("processes") != list(PROCESS_IDS):
        print("Hata: S3 yazımı sonrası süreç bağları doğrulanamadı.", file=sys.stderr)
        return 5
    if saved.get("applications") != expert["applications"]:
        print("Hata: S3 yazımı sonrası uygulama bağları doğrulanamadı.", file=sys.stderr)
        return 6

    action = "güncellendi" if existing is not None else "oluşturuldu"
    print(f"OK: {EXPERT_KEY} {action}; "
          f"read={saved['access_scope']['read']}, "
          f"edit={saved['access_scope']['edit']}.")
    for app in saved.get("applications") or []:
        depts = app.get("departments") or []
        scope = ", ".join(depts) if depts else "uzmanı gören herkes"
        print(f"  Uygulama: {app.get('id')} → {scope}")
    print("Not: p_fi_desk panosunu masada göstermek için "
          "jobs/fi_desk_dashboards.py'ı (PUBLISH_SNAPSHOT=True) koş — "
          "snapshot bu uzmana bağlanır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
