"""Departman bakışları — Süreç Düzenlileştirme W8.

Aynı uzman altında departmana göre farklı süreç seti (topic'lere gruplu) +
farklı brifing odağı + SIKI erişim: yalnız açıkça tanımlı departmanlar uzmanı
ve süreçlerini görür (kullanıcı kararı 2026-07-23). Piramidin A (blok) ve B
(süreç) aşamaları departmandan bağımsız/paylaşımlıdır; yalnız C (uzman
brifingi) bakışa göre çatallanır.

Şema (Expert.department_views):
    [{"departments": ["Bilanço", "Hazine"],
      "label": "Bilanço Bakışı",              # ops. görünen ad
      "briefing_focus": "Bilanço yönetimi açısından...",   # ops. C prompt merceği
      "topics": [{"title": "Stok Analizi",
                  "processes": ["mevduat.maliyet", "mevduat.bakiye"]},
                 {"title": "Dönüşler", "processes": ["mevduat.donusler"]}]}]

Geriye uyum: department_views boşsa uzman LEGACY modda kalır — süreçler
bound_content.processes'ten, erişim access_scope.read'ten (eski davranış).

Saf modül: flask'a bağımlı değil (test edilebilirlik).
"""
from __future__ import annotations

import hashlib
import json

#: Legacy (department_views tanımsız) uzmanların brifing cache anahtarı eki.
LEGACY_KEY = "_legacy_"


def _view_key(v: dict) -> str:
    """Bakışın içeriğinden deterministik anahtar (brifing cache + input_hash)."""
    payload = json.dumps({
        "d": sorted(v.get("departments") or []),
        "t": v.get("topics") or [],
        "f": v.get("briefing_focus") or "",
    }, sort_keys=True, ensure_ascii=False, default=str)
    return "v_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _normalize_view(v: dict) -> dict:
    """Ham bakış → {key, label, briefing_focus, departments, topics, process_ids}.

    topics: [{title, process_ids}]; process_ids: tüm topic'lerin sıralı-tekil
    birleşimi (piramit + erişim bunu kullanır)."""
    topics: list[dict] = []
    flat: list[str] = []
    for t in v.get("topics") or []:
        pids = [p for p in (t.get("processes") or [])
                if isinstance(p, str) and p.strip()]
        topics.append({"title": (t.get("title") or "").strip() or "Süreçler",
                       "process_ids": pids})
        for p in pids:
            if p not in flat:
                flat.append(p)
    return {
        "key": _view_key(v),
        "label": (v.get("label") or "").strip(),
        "briefing_focus": (v.get("briefing_focus") or "").strip(),
        "departments": [d for d in (v.get("departments") or [])
                        if isinstance(d, str) and d.strip()],
        "topics": topics,
        "process_ids": flat,
    }


def list_views(expert) -> list[dict]:
    """Uzmanın tüm departman bakışları (normalize). Tanımsızsa boş liste."""
    return [_normalize_view(v)
            for v in (getattr(expert, "department_views", None) or [])
            if isinstance(v, dict)]


def resolve_view(expert, department: str | None) -> dict:
    """Kullanıcının departmanına göre bakışı çözer.

    Dönüş: {"granted": bool, "legacy": bool, "view": dict|None}.
    - legacy=True: uzman department_views taşımıyor → çağıran eski davranışı
      (bound_content + access_scope) uygular; granted her zaman True.
    - legacy=False, granted=True: eşleşen bakış (view dolu).
    - legacy=False, granted=False: SIKI erişim reddi (403).
    """
    views = list_views(expert)
    if not views:
        return {"granted": True, "legacy": True, "view": None}
    dept = (department or "").strip()
    for v in views:
        if dept and dept in v["departments"]:
            return {"granted": True, "legacy": False, "view": v}
    return {"granted": False, "legacy": False, "view": None}


def can_access(expert, department: str | None) -> bool:
    """Kullanıcı bu uzmanı görebilir mi? department_views varsa SIKI
    (eşleşen bakış şart); yoksa legacy access_scope.read ('*'/dept)."""
    r = resolve_view(expert, department)
    if not r["legacy"]:
        return r["granted"]
    read = (getattr(expert, "access_scope", None) or {}).get("read") or []
    return "*" in read or ((department or "").strip() in read)


def legacy_view(expert) -> dict:
    """Legacy uzman için tek-topic bakış (render + piramit tek yoldan aksın)."""
    pids = list((getattr(expert, "bound_content", None) or {}).get("processes") or [])
    return {
        "key": LEGACY_KEY,
        "label": "",
        "briefing_focus": "",
        "departments": [],
        # Başlık boş: expert.html section-head zaten "Süreçler" diyor → topic
        # başlığı çift görünmesin (legacy tek grup).
        "topics": [{"title": "", "process_ids": pids}],
        "process_ids": pids,
    }
