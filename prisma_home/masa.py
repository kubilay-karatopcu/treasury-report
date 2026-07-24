"""Masa modu — tek anahtarlı "LLM'siz masa" görünümü.

``PRISMA_MASA_MODE`` açıkken (env "1"/"true"/"on"):

- Hiçbir brifing / blok açıklaması / süreç açıklaması HESAPLANMAZ; PRISMA
  arayüzde LLM kullanmıyormuş gibi durur (piramit ısıtıcısı başlatılmaz,
  uzman sayfası ağır yolları atlar).
- Atölye (üretim tarafı) tamamen gizlenir ve erişilemez (``prisma_home.atolye_home``
  + tüm ``presentations.*`` + LLM uçları 404).
- "Uzman" kavramı UI'da "Masa" olarak adlandırılır; uzman adları "… Masası"
  gösterilir (``masa_name``).
- Masa (tüketici) tarafında ana sayfa aynı karşılama + yetkili masaların
  listesini gösterir; masaya girince yalnız klasörlenmiş süreçler görünür,
  brifing/özet/buton yoktur.

Anahtar KAPALIYKEN her şey birebir eski davranıştadır (varsayılan kapalı).
"""
from __future__ import annotations

from flask import current_app


def masa_mode_on() -> bool:
    """Masa modu açık mı? — app.config['PRISMA_MASA_MODE'] (varsayılan kapalı)."""
    return bool(current_app.config.get("PRISMA_MASA_MODE"))


def masa_name(name: str | None) -> str:
    """Uzman adını masa adına çevirir: 'Mevduat Uzmanı' → 'Mevduat Masası'.

    Ad 'Uzman' içermiyorsa sonuna ' Masası' eklenmez (olduğu gibi döner) —
    isimlendirme kararı yalnız 'Uzman' ekli adlarda güvenli. Boş/None güvenli.
    """
    if not name:
        return name or ""
    for a, b in (("Uzmanlığı", "Masası"), ("Uzmanı", "Masası"), ("Uzman", "Masa")):
        if a in name:
            return name.replace(a, b)
    return name


#: Masa modunda 404'lenecek tekil uçlar: Atölye ana + LLM uçları. Üretim
#: tarafının tamamı (``presentations.*``) ayrıca ön-ekle bloklanır.
BLOCKED_ENDPOINTS_IN_MASA = frozenset({
    "prisma_home.atolye_home",
    "prisma_home.expert_ask",
    "prisma_home.expert_briefing_json",
})


def is_blocked_endpoint(endpoint: str | None) -> bool:
    """Masa modunda bu uç 404'lenmeli mi? — Atölye ana, LLM uçları ve tüm
    ``presentations.*`` (üretim tarafı). Masa/tüketici + mevduat_panel + statik
    dokunulmaz. (Anahtar kontrolü çağırana ait; bu saf karar fonksiyonudur.)"""
    ep = endpoint or ""
    return ep in BLOCKED_ENDPOINTS_IN_MASA or ep.startswith("presentations.")
